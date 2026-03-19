#!/usr/bin/env python3
"""Automated browser tests for Claude Session Viewer."""

import subprocess
import time
import sys

from playwright.sync_api import sync_playwright

SERVER_PORT = 18923
URL = f"http://127.0.0.1:{SERVER_PORT}"
SAMPLE_FOLDER = "-Users-henry-Desktop----AI-----"
PASSED = 0
FAILED = 0
ERRORS = []


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  \033[32mPASS\033[0m {name}")
    else:
        FAILED += 1
        msg = f"  \033[31mFAIL\033[0m {name}" + (f" — {detail}" if detail else "")
        print(msg)
        ERRORS.append(msg)


def run_tests():
    # Start server
    print("Starting server...")
    import os
    env = os.environ.copy()
    env["BROWSER"] = "echo"  # Prevent webbrowser.open from launching a browser
    proc = subprocess.Popen(
        [sys.executable, "claude_session_viewer.py"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env,
    )
    # Wait for server to be ready
    import urllib.request
    for _ in range(20):
        try:
            urllib.request.urlopen(f"{URL}/api/folders", timeout=1)
            break
        except Exception:
            time.sleep(0.5)
    else:
        print("ERROR: Server did not start")
        proc.kill()
        return False

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()

            # Collect JS console errors
            js_errors = []
            page.on("console", lambda msg: js_errors.append(msg.text) if msg.type == "error" else None)
            page.on("pageerror", lambda err: js_errors.append(str(err)))

            # ─── Test 1: Page loads ───
            print("\n[1] Page Load")
            page.goto(URL, timeout=30000, wait_until="domcontentloaded")
            check("Page loads", page.title() != "")
            check("No JS errors on load", len(js_errors) == 0, f"Errors: {js_errors[:3]}")

            # ─── Test 2: Sidebar folders load ───
            print("\n[2] Sidebar")
            page.wait_for_selector(".folder", timeout=5000)
            folders = page.query_selector_all(".folder")
            check("Folders loaded", len(folders) > 0, f"Found: {len(folders)}")

            # ─── Test 3: Folder search/filter ───
            print("\n[3] Folder Filter")
            search_input = page.query_selector("#folder-search")
            check("Search input exists", search_input is not None)
            search_input.fill("AI")
            time.sleep(0.3)
            visible_folders = page.evaluate("""
                () => [...document.querySelectorAll('.folder')]
                    .filter(f => f.style.display !== 'none').length
            """)
            check("Folder filter works", visible_folders < len(folders), f"Visible: {visible_folders}/{len(folders)}")

            # Clear filter
            search_input.fill("")
            time.sleep(0.3)
            visible_after_clear = page.evaluate("""
                () => [...document.querySelectorAll('.folder')]
                    .filter(f => f.style.display !== 'none').length
            """)
            check("Clear filter restores all", visible_after_clear == len(folders))

            # ─── Test 4: Open folder and load session ───
            print("\n[4] Load Session")
            # Find and click the target folder
            folder_clicked = page.evaluate(f"""
                () => {{
                    const folders = document.querySelectorAll('.folder-name');
                    for (const f of folders) {{
                        if (f.closest('.folder').dataset.name === '{SAMPLE_FOLDER}') {{
                            f.click();
                            return true;
                        }}
                    }}
                    return false;
                }}
            """)
            check("Target folder found and clicked", folder_clicked)

            if folder_clicked:
                page.wait_for_selector(".session-item", timeout=10000)
                sessions = page.query_selector_all(".session-item")
                check("Sessions listed", len(sessions) > 0, f"Found: {len(sessions)}")

                # Click first session
                if sessions:
                    sessions[0].click()
                    page.wait_for_selector(".view-toggle", timeout=10000)
                    check("Session loaded (view-toggle visible)", True)

                    # Check Chat View loaded
                    entries = page.query_selector_all("#entries .entry")
                    check("Chat entries rendered", len(entries) > 0, f"Found: {len(entries)}")

            # ─── Test 5: View toggles ───
            print("\n[5] View Toggles")

            # API View
            api_btn = page.evaluate("""
                () => {
                    const btns = document.querySelectorAll('.view-toggle button');
                    for (const b of btns) {
                        if (b.textContent.includes('API')) { b.click(); return true; }
                    }
                    return false;
                }
            """)
            if api_btn:
                time.sleep(2)
                api_calls = page.query_selector_all(".api-call")
                check("API View renders", len(api_calls) > 0, f"API calls: {len(api_calls)}")

            # Stats View
            stats_btn = page.evaluate("""
                () => {
                    const btns = document.querySelectorAll('.view-toggle button');
                    for (const b of btns) {
                        if (b.textContent.includes('Stats')) { b.click(); return true; }
                    }
                    return false;
                }
            """)
            if stats_btn:
                time.sleep(2)
                stat_cards = page.query_selector_all(".stat-card")
                check("Stats View renders", len(stat_cards) > 0, f"Stat cards: {len(stat_cards)}")

                svg_charts = page.query_selector_all("svg")
                check("SVG charts rendered", len(svg_charts) > 0, f"SVGs: {len(svg_charts)}")

            # Timeline View
            tl_btn = page.evaluate("""
                () => {
                    const btns = document.querySelectorAll('.view-toggle button');
                    for (const b of btns) {
                        if (b.textContent.includes('Timeline')) { b.click(); return true; }
                    }
                    return false;
                }
            """)
            if tl_btn:
                time.sleep(2)
                tl_entries = page.query_selector_all(".timeline-card")
                check("Timeline View renders", len(tl_entries) > 0, f"Timeline cards: {len(tl_entries)}")

            # Back to Chat View
            page.evaluate("""
                () => {
                    const btns = document.querySelectorAll('.view-toggle button');
                    for (const b of btns) {
                        if (b.textContent.includes('Chat')) { b.click(); return true; }
                    }
                }
            """)
            time.sleep(1)

            # ─── Test 6: Tool ID linking ───
            print("\n[6] Tool ID Linking")
            tool_link_result = page.evaluate("""
                () => {
                    const toolUse = document.querySelector('[data-call-id]');
                    if (!toolUse) return 'no tool_use found';
                    const callId = toolUse.dataset.callId;
                    const result = document.querySelector(`[data-result-id="${callId}"]`);
                    if (!result) return 'no matching tool_result for ' + callId.slice(0,16);
                    return 'matched: ' + callId.slice(0,16);
                }
            """)
            check("Tool ID pairs exist", tool_link_result.startswith("matched"), tool_link_result)

            # ─── Test 7: Credential Detection ───
            print("\n[7] Credential Detection")
            # Check if secrets endpoint works (via fetch in page context)
            secrets_result = page.evaluate("""
                async () => {
                    if (!currentFile) return 'no file loaded';
                    const res = await fetch('/api/secrets?file=' + encodeURIComponent(currentFile));
                    const data = await res.json();
                    return 'secrets: ' + data.length;
                }
            """)
            check("Secrets endpoint works", secrets_result.startswith("secrets"), secrets_result)

            # ─── Test 8: Export ───
            print("\n[8] Export")
            export_md = page.evaluate("""
                async () => {
                    if (!currentFile) return 'no file';
                    const res = await fetch('/api/export?file=' + encodeURIComponent(currentFile) + '&fmt=md');
                    const ct = res.headers.get('content-type');
                    const cd = res.headers.get('content-disposition');
                    return `type=${ct} disp=${cd} status=${res.status}`;
                }
            """)
            check("Markdown export works", "markdown" in export_md and "200" in export_md, export_md)

            export_html = page.evaluate("""
                async () => {
                    if (!currentFile) return 'no file';
                    const res = await fetch('/api/export?file=' + encodeURIComponent(currentFile) + '&fmt=html');
                    return `status=${res.status} size=${(await res.text()).length}`;
                }
            """)
            check("HTML export works", "200" in export_html, export_html)

            # ─── Test 9: Global Search ───
            print("\n[9] Global Search")
            search_result = page.evaluate("""
                async () => {
                    const res = await fetch('/api/search?q=test');
                    const data = await res.json();
                    return 'results: ' + data.length;
                }
            """)
            check("Search API works", search_result.startswith("results"), search_result)

            # ─── Test 10: Poll (Live Watch) ───
            print("\n[10] Live Watch")
            poll_result = page.evaluate("""
                async () => {
                    const res = await fetch('/api/poll?since=0');
                    const data = await res.json();
                    return 'files: ' + data.length;
                }
            """)
            check("Poll endpoint works", poll_result.startswith("files"), poll_result)

            # ─── Test 11: Final JS error check ───
            print("\n[11] JS Error Summary")
            check("No JS errors throughout test", len(js_errors) == 0, f"Errors: {js_errors[:5]}")

            browser.close()

    finally:
        proc.kill()
        proc.wait()

    # ─── Summary ───
    print(f"\n{'='*50}")
    print(f"  \033[32m{PASSED} PASSED\033[0m  |  \033[31m{FAILED} FAILED\033[0m")
    if ERRORS:
        print(f"\nFailed tests:")
        for e in ERRORS:
            print(e)
    print(f"{'='*50}")
    return FAILED == 0


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
