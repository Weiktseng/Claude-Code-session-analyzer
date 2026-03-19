#!/usr/bin/env python3
"""Claude Code Session Viewer — Local web app for browsing session logs."""

import http.server
import json
import html
import os
import re
import tempfile
import urllib.parse
import webbrowser
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path

import mimetypes

PORT = int(os.environ.get("CSV_PORT", 18923))
BASE_DIR = os.environ.get(
    "CSV_BASE_DIR",
    os.path.join(os.path.expanduser("~"), ".claude", "projects"),
)
LOCAL_TZ_OFFSET = int(os.environ.get("CSV_TZ_OFFSET", 8))  # default UTC+8
TW = timezone(timedelta(hours=LOCAL_TZ_OFFSET))

# ─── Compact Detection ───
COMPACT_PATTERNS = [
    (r'壓縮進度紀錄|Curator 壓縮於', 'curator', 'Curator 壓縮紀錄'),
    (r'continued from a previous conversation that ran out of context', 'context-cont', 'Context 延續摘要'),
    (r'<command-name>/compact</command-name>', 'compact-cmd', '/compact 指令'),
    (r'Compacted \(ctrl\+o', 'compacted', 'Compacted 輸出'),
    (r'Summary:.*Primary Request|The summary below covers the earlier portion', 'summary', 'Session 摘要'),
]


def detect_compact(text):
    """Return list of (kind, label) for compact content detected in text."""
    if not text:
        return []
    found = []
    for pattern, kind, label in COMPACT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            found.append((kind, label))
    return found


def fmt_time(ts):
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TW)
        return dt.strftime("%m/%d %H:%M:%S")
    except Exception:
        return str(ts)


def fmt_date(ts):
    if not ts:
        return ""
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TW)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def esc(text):
    if not text:
        return ""
    return html.escape(str(text))


def render_md(text):
    if not text:
        return ""
    t = html.escape(text)
    t = re.sub(r'```(\w*)\n(.*?)```', lambda m: f'<pre class="code-block"><code>{m.group(2)}</code></pre>', t, flags=re.DOTALL)
    t = re.sub(r'`([^`]+)`', r'<code class="ic">\1</code>', t)
    t = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', t)
    t = re.sub(r'^(#{1,3}) (.+)$', lambda m: f'<h{len(m.group(1))} style="margin:8px 0">{m.group(2)}</h{len(m.group(1))}>', t, flags=re.MULTILINE)
    t = t.replace("\n", "<br>")
    return t


def compact_badges(compacts):
    """Render compact badges HTML."""
    if not compacts:
        return ""
    badges = ""
    for kind, label in compacts:
        badges += f'<span class="compact-badge compact-{kind}">{esc(label)}</span>'
    return badges


def get_session_summary(filepath):
    """Quick scan: get first user message preview + timestamp + record count + compact info."""
    first_user = ""
    first_ts = ""
    last_ts = ""
    count = 0
    model = ""
    has_compact = False
    compact_count = 0
    try:
        with open(filepath) as f:
            for line in f:
                count += 1
                r = json.loads(line)
                ts = r.get("timestamp", "")
                if ts and not first_ts:
                    first_ts = ts
                if ts:
                    last_ts = ts
                if r.get("type") == "user":
                    msg = r.get("message", {})
                    c = msg.get("content", "")
                    raw = c if isinstance(c, str) else str(c)
                    if not first_user:
                        if isinstance(c, list):
                            c = " ".join(b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text")
                        first_user = str(c)[:120].replace("\n", " ")
                    if detect_compact(raw):
                        has_compact = True
                        compact_count += 1
                if r.get("type") == "assistant" and not model:
                    model = r.get("message", {}).get("model", "")
    except Exception:
        pass
    return {
        "preview": first_user or "(empty)",
        "start": fmt_date(first_ts),
        "end": fmt_date(last_ts),
        "records": count,
        "model": model,
        "has_compact": has_compact,
        "compact_count": compact_count,
    }


def render_session(filepath):
    """Render a session JSONL into HTML entries."""
    with open(filepath) as f:
        records = [json.loads(line) for line in f]

    parts = []
    eid = 0
    compact_total = 0
    for r in records:
        rtype = r.get("type", "")
        ts = fmt_time(r.get("timestamp", ""))

        if rtype == "user":
            msg = r.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                tool_results = []
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif block.get("type") == "tool_result":
                        tid_full = block.get("tool_use_id", "")
                        tid = tid_full[:12]
                        rc = block.get("content", "")
                        if isinstance(rc, list):
                            rc = "\n".join(b.get("text", "") for b in rc if isinstance(b, dict))
                        rc_str = str(rc)[:500]
                        tool_results.append((tid, tid_full, rc_str))
                if text_parts:
                    text = "\n".join(text_parts)
                    if text.strip():
                        compacts = detect_compact(text)
                        is_compact = " compact" if compacts else ""
                        badges = compact_badges(compacts)
                        if compacts:
                            compact_total += 1
                        parts.append(f'<div class="entry user{is_compact}" data-type="user" data-compact="{1 if compacts else 0}" id="e{eid}">{badges}<span class="tag tag-user">USER</span><span class="time">{ts}</span><button class="cbtn" onclick="fold(this)">▼</button><div class="content">{render_md(text)}</div></div>')
                        eid += 1
                for tid, tid_full, rc_str in tool_results:
                    compacts = detect_compact(rc_str)
                    is_compact = " compact" if compacts else ""
                    badges = compact_badges(compacts)
                    if compacts:
                        compact_total += 1
                    parts.append(f'<div class="entry tool-result{is_compact} api-clickable" data-type="tool" data-compact="{1 if compacts else 0}" data-result-id="{esc(tid_full)}" onclick="jumpToCall(this)" title="Click to jump to tool_use" id="e{eid}">{badges}<span class="tag tag-result">RESULT</span><span class="time">{ts}</span> <span class="tid">id:{tid}…</span><button class="cbtn" onclick="event.stopPropagation();fold(this)">▼</button><div class="content">{esc(rc_str)}</div></div>')
                    eid += 1
            else:
                text_str = str(content)
                if text_str.strip():
                    compacts = detect_compact(text_str)
                    is_compact = " compact" if compacts else ""
                    badges = compact_badges(compacts)
                    if compacts:
                        compact_total += 1
                    parts.append(f'<div class="entry user{is_compact}" data-type="user" data-compact="{1 if compacts else 0}" id="e{eid}">{badges}<span class="tag tag-user">USER</span><span class="time">{ts}</span><button class="cbtn" onclick="fold(this)">▼</button><div class="content">{render_md(text_str)}</div></div>')
                    eid += 1

        elif rtype == "assistant":
            msg = r.get("message", {})
            for block in msg.get("content", []):
                btype = block.get("type", "")
                if btype == "text":
                    text = block.get("text", "")
                    if not text.strip():
                        continue
                    compacts = detect_compact(text)
                    is_compact = " compact" if compacts else ""
                    badges = compact_badges(compacts)
                    if compacts:
                        compact_total += 1
                    parts.append(f'<div class="entry assistant{is_compact}" data-type="assistant" data-compact="{1 if compacts else 0}" id="e{eid}">{badges}<span class="tag tag-assistant">ASSISTANT</span><span class="time">{ts}</span><button class="cbtn" onclick="fold(this)">▼</button><div class="content">{render_md(text)}</div></div>')
                    eid += 1
                elif btype == "thinking":
                    text = block.get("thinking", "")
                    if not text.strip():
                        continue
                    preview = text[:100].replace("\n", " ")
                    parts.append(f'<div class="entry thinking hidden-default" data-type="thinking" data-compact="0" id="e{eid}"><span class="tag tag-thinking">THINKING</span><span class="time">{ts}</span><details><summary>{esc(preview)}…</summary><div class="content">{esc(text)}</div></details></div>')
                    eid += 1
                elif btype == "tool_use":
                    name = block.get("name", "?")
                    tid_full = block.get("id", "")
                    tid = tid_full[:12]
                    inp = block.get("input", {})
                    detail = ""
                    if name == "Bash":
                        detail = inp.get("command", "")
                    elif name in ("Read", "Write"):
                        detail = inp.get("file_path", "")
                    elif name == "Edit":
                        detail = f"file: {inp.get('file_path','')}\nold: {inp.get('old_string','')[:150]}\nnew: {inp.get('new_string','')[:150]}"
                    elif name == "Grep":
                        detail = f"pattern: {inp.get('pattern','')}  path: {inp.get('path','')}"
                    elif name == "Glob":
                        detail = f"pattern: {inp.get('pattern','')}"
                    elif name == "Agent":
                        detail = inp.get("prompt", "")[:300]
                    elif name == "Skill":
                        detail = f"skill: {inp.get('skill','')}"
                    else:
                        detail = json.dumps(inp, ensure_ascii=False)[:400]
                    parts.append(f'<div class="entry tool api-clickable" data-type="tool" data-compact="0" data-call-id="{esc(tid_full)}" onclick="jumpToResult(this)" title="Click to jump to result" id="e{eid}"><span class="tag tag-tool">TOOL</span> <span class="tool-name">{esc(name)}</span> <span class="tid">id:{tid}…</span><span class="time">{ts}</span><button class="cbtn" onclick="event.stopPropagation();fold(this)">▼</button><div class="content">{esc(detail)}</div></div>')
                    eid += 1

        elif rtype == "system":
            sub = r.get("subtype", "")
            if sub:
                parts.append(f'<div class="entry system hidden-default" data-type="system" data-compact="0" id="e{eid}"><span class="tag tag-system">SYS</span><span class="time">{ts}</span> <span style="color:#8b949e">{esc(sub)}</span></div>')
                eid += 1

        elif rtype == "progress":
            data = r.get("data", {})
            dtype = data.get("type", "")
            if dtype == "agent_progress":
                content = data.get("content", "")
                if content and len(str(content)) > 20:
                    preview = str(content)[:100].replace("\n", " ")
                    parts.append(f'<div class="entry progress hidden-default" data-type="progress" data-compact="0" id="e{eid}"><span class="tag tag-progress">AGENT</span><span class="time">{ts}</span><details><summary>{esc(preview)}…</summary><div class="content">{esc(str(content))}</div></details></div>')
                    eid += 1

    return "\n".join(parts), eid, compact_total


def render_api_view(filepath):
    """Render session as API call pairs: request → response."""
    with open(filepath) as f:
        records = [json.loads(line) for line in f]

    from collections import OrderedDict
    api_groups = OrderedDict()
    pending_inputs = []

    for r in records:
        rtype = r.get("type", "")
        if rtype == "user":
            pending_inputs.append(r.get("message", {}))
        elif rtype == "assistant":
            msg = r.get("message", {})
            msg_id = msg.get("id", "")
            if not msg_id:
                continue
            if msg_id not in api_groups:
                api_groups[msg_id] = {
                    "model": msg.get("model", ""),
                    "ts": r.get("timestamp", ""),
                    "turns": [],
                    "stop_reason": msg.get("stop_reason", ""),
                    "usage": msg.get("usage", {}),
                }
            api_groups[msg_id]["turns"].append({
                "inputs": list(pending_inputs),
                "blocks": list(msg.get("content", [])),
            })
            if msg.get("stop_reason"):
                api_groups[msg_id]["stop_reason"] = msg["stop_reason"]
            if msg.get("usage"):
                api_groups[msg_id]["usage"] = msg["usage"]
            pending_inputs = []

    parts = []
    total_in = 0
    total_out = 0
    total_cache_read = 0
    total_cache_create = 0

    for i, (mid, group) in enumerate(api_groups.items()):
        usage = group.get("usage", {})
        in_tok = usage.get("input_tokens", 0)
        out_tok = usage.get("output_tokens", 0)
        cache_read = usage.get("cache_read_input_tokens", 0)
        cache_create = usage.get("cache_creation_input_tokens", 0)
        total_in += in_tok
        total_out += out_tok
        total_cache_read += cache_read
        total_cache_create += cache_create

        stop = group.get("stop_reason", "")
        stop_class = "stop-end" if stop == "end_turn" else "stop-tool" if stop == "tool_use" else "stop-other"
        ts = fmt_time(group.get("ts", ""))

        turn_count = len(group["turns"])

        # Helper to render input messages
        def render_input(m):
            h = ""
            c = m.get("content", "")
            if isinstance(c, str) and c.strip():
                compacts = detect_compact(c)
                cbadges = compact_badges(compacts)
                cclass = " api-compact" if compacts else ""
                h += f'<div class="api-msg api-user{cclass}">{cbadges}<span class="api-role">user</span> <span class="api-size">{len(c):,} chars</span>'
                if len(c) > 300:
                    h += f'<details><summary>{esc(c[:120])}…</summary><div class="api-body">{esc(c)}</div></details>'
                else:
                    h += f'<div class="api-body">{esc(c[:300])}</div>'
                h += '</div>'
            elif isinstance(c, list):
                for block in c:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type", "")
                    if bt == "tool_result":
                        tid = block.get("tool_use_id", "")
                        rc = block.get("content", "")
                        if isinstance(rc, list):
                            rc = "\n".join(b.get("text", "") for b in rc if isinstance(b, dict))
                        rc_str = str(rc)
                        is_error = block.get("is_error", False)
                        err_class = " api-error" if is_error else ""
                        compacts = detect_compact(rc_str)
                        cbadges = compact_badges(compacts)
                        cclass = " api-compact" if compacts else ""
                        h += f'<div class="api-msg api-tool-result{err_class}{cclass} api-clickable" data-result-id="{esc(tid)}" onclick="jumpToCall(this)" title="Click to jump to tool_use">{cbadges}<span class="api-role">tool_result</span> <span class="api-tid">id:{tid[:16]}</span> <span class="api-size">{len(rc_str):,} chars</span>'
                        if len(rc_str) > 200:
                            h += f'<details><summary>{esc(rc_str[:120])}…</summary><div class="api-body">{esc(rc_str)}</div></details>'
                        else:
                            h += f'<div class="api-body">{esc(rc_str)}</div>'
                        h += '</div>'
                    elif bt == "text":
                        txt = block.get("text", "")
                        if txt.strip():
                            compacts = detect_compact(txt)
                            cbadges = compact_badges(compacts)
                            cclass = " api-compact" if compacts else ""
                            h += f'<div class="api-msg api-user{cclass}">{cbadges}<span class="api-role">user</span> <span class="api-size">{len(txt):,} chars</span><div class="api-body">{esc(txt[:300])}</div></div>'
            return h

        # Helper to render output blocks
        def render_output(blocks):
            h = ""
            for block in blocks:
                bt = block.get("type", "")
                if bt == "thinking":
                    text = block.get("thinking", "")
                    h += f'<div class="api-msg api-thinking"><span class="api-role">thinking</span> <span class="api-size">{len(text):,} chars</span>'
                    if len(text) > 150:
                        h += f'<details><summary>{esc(text[:100])}…</summary><div class="api-body">{esc(text)}</div></details>'
                    else:
                        h += f'<div class="api-body">{esc(text)}</div>'
                    h += '</div>'
                elif bt == "text":
                    text = block.get("text", "")
                    h += f'<div class="api-msg api-text"><span class="api-role">text</span> <span class="api-size">{len(text):,} chars</span>'
                    if len(text) > 300:
                        h += f'<details><summary>{esc(text[:120])}…</summary><div class="api-body">{esc(text)}</div></details>'
                    else:
                        h += f'<div class="api-body">{esc(text)}</div>'
                    h += '</div>'
                elif bt == "tool_use":
                    name = block.get("name", "?")
                    tid = block.get("id", "")
                    inp = block.get("input", {})
                    inp_json = json.dumps(inp, ensure_ascii=False)
                    h += f'<div class="api-msg api-tool-call api-clickable" data-call-id="{esc(tid)}" onclick="jumpToResult(this)" title="Click to jump to result"><span class="api-role">tool_use</span> <span class="api-tool-name">{esc(name)}</span> <span class="api-tid">id:{tid[:16]}</span> <span class="api-size">{len(inp_json):,} chars</span>'
                    if len(inp_json) > 200:
                        h += f'<details><summary>{esc(inp_json[:120])}…</summary><div class="api-body">{esc(inp_json)}</div></details>'
                    else:
                        h += f'<div class="api-body">{esc(inp_json)}</div>'
                    h += '</div>'
            return h

        # Build turns HTML — show each turn as a paired row
        turns_html = ""
        for j, turn in enumerate(group["turns"]):
            req_html = ""
            for m in turn["inputs"]:
                req_html += render_input(m)
            resp_html = render_output(turn["blocks"])

            if turn_count == 1:
                # Single turn: simple layout
                turns_html += f"""<div class="api-pair">
    <div class="api-req"><div class="api-label">REQUEST</div>{req_html or '<div class="api-empty">(streaming continuation)</div>'}</div>
    <div class="api-arrow">→</div>
    <div class="api-resp"><div class="api-label">RESPONSE</div>{resp_html}</div>
</div>"""
            else:
                # Multi-turn: show turn number and connect them visually
                turn_label = f'<div class="turn-label">Turn {j+1}/{turn_count}</div>'
                connector = ' turn-first' if j == 0 else ' turn-mid' if j < turn_count - 1 else ' turn-last'
                turns_html += f"""<div class="api-pair api-turn{connector}">
    <div class="api-req">{turn_label}{req_html or '<div class="api-empty">(streaming)</div>'}</div>
    <div class="api-arrow">{'→' if req_html else '↓'}</div>
    <div class="api-resp">{resp_html}</div>
</div>"""

        # Token bar visualization
        tok_bar = ""
        if in_tok or out_tok or cache_read or cache_create:
            total = max(in_tok + cache_read + cache_create, 1)
            tok_bar = f"""<div class="tok-bar">
                <div class="tok-seg tok-cache-read" style="width:{cache_read/total*100:.1f}%" title="cache read: {cache_read:,}"></div>
                <div class="tok-seg tok-cache-create" style="width:{cache_create/total*100:.1f}%" title="cache create: {cache_create:,}"></div>
                <div class="tok-seg tok-input" style="width:{in_tok/total*100:.1f}%" title="input: {in_tok:,}"></div>
            </div>"""

        parts.append(f"""<div class="api-call" id="api{i}">
  <div class="api-header">
    <span class="api-num">#{i+1}</span>
    <span class="api-model">{esc(group['model'])}</span>
    <span class="api-time">{ts}</span>
    <span class="api-stop {stop_class}">{esc(stop)}</span>
    <span class="api-turns">{turn_count} turn{'s' if turn_count > 1 else ''}</span>
    <span class="api-tokens">in:{in_tok:,} out:{out_tok:,} cache_r:{cache_read:,} cache_w:{cache_create:,}</span>
    <span class="api-mid">{esc(mid[:24])}</span>
  </div>
  {tok_bar}
  {turns_html}
</div>""")

    # Summary header
    summary = f"""<div class="api-summary">
  <strong>{len(api_groups)} API Calls</strong> |
  Total tokens — input: {total_in:,} | output: {total_out:,} | cache read: {total_cache_read:,} | cache create: {total_cache_create:,} |
  Est. cost: ~${(total_in * 15 + total_out * 75 + total_cache_read * 1.5 + total_cache_create * 18.75) / 1_000_000:.2f}
</div>"""

    return summary + "\n".join(parts), len(api_groups)


def compute_stats(filepath):
    """Compute session statistics for the stats view."""
    with open(filepath) as f:
        records = [json.loads(line) for line in f]

    total_in = 0
    total_out = 0
    total_cache_read = 0
    total_cache_create = 0
    per_call_cost = []
    tool_usage = {}
    first_ts = None
    last_ts = None
    api_call_count = 0
    user_msg_count = 0
    assistant_msg_count = 0
    tool_msg_count = 0

    for r in records:
        rtype = r.get("type", "")
        ts = r.get("timestamp", "")
        if ts:
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if first_ts is None:
                    first_ts = dt
                last_ts = dt
            except Exception:
                pass

        if rtype == "user":
            user_msg_count += 1
            msg = r.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        tool_msg_count += 1

        elif rtype == "assistant":
            assistant_msg_count += 1
            msg = r.get("message", {})
            usage = msg.get("usage", {})
            if usage:
                api_call_count += 1
                in_tok = usage.get("input_tokens", 0)
                out_tok = usage.get("output_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)
                cache_create = usage.get("cache_creation_input_tokens", 0)
                total_in += in_tok
                total_out += out_tok
                total_cache_read += cache_read
                total_cache_create += cache_create
                call_cost = (in_tok * 15 + out_tok * 75 + cache_read * 1.5 + cache_create * 18.75) / 1_000_000
                per_call_cost.append(round(call_cost, 6))

            for block in msg.get("content", []):
                if isinstance(block, dict) and block.get("type") == "tool_use":
                    name = block.get("name", "unknown")
                    tool_usage[name] = tool_usage.get(name, 0) + 1

    duration_sec = 0
    if first_ts and last_ts:
        duration_sec = int((last_ts - first_ts).total_seconds())

    total_cost = (total_in * 15 + total_out * 75 + total_cache_read * 1.5 + total_cache_create * 18.75) / 1_000_000

    return {
        "total_input": total_in,
        "total_output": total_out,
        "total_cache_read": total_cache_read,
        "total_cache_create": total_cache_create,
        "total_cost": round(total_cost, 4),
        "per_call_cost": per_call_cost,
        "tool_usage": tool_usage,
        "duration_sec": duration_sec,
        "api_call_count": api_call_count,
        "user_msg_count": user_msg_count,
        "assistant_msg_count": assistant_msg_count,
        "tool_msg_count": tool_msg_count,
    }


SECRET_PATTERNS = [
    ("Anthropic API Key", r'sk-ant-[a-zA-Z0-9\-]{20,}'),
    ("OpenAI API Key", r'sk-[a-zA-Z0-9]{20,}'),
    ("GitHub PAT", r'ghp_[a-zA-Z0-9]{36}'),
    ("AWS Access Key", r'AKIA[0-9A-Z]{16}'),
    ("Private Key", r'-----BEGIN[A-Z ]*PRIVATE KEY-----'),
    ("Generic Secret", r'(?:api_key|token|secret|password|API_KEY|TOKEN|SECRET|PASSWORD)\s*[=:]\s*["\']?([A-Za-z0-9_\-/.+]{8,})["\']?'),
]


def search_sessions(query, folder=None):
    """Search across session JSONL files for matching text content."""
    query_lower = query.lower()
    results = []
    total = 0

    if folder:
        folders_to_search = [folder]
    else:
        try:
            folders_to_search = [
                name for name in sorted(os.listdir(BASE_DIR))
                if os.path.isdir(os.path.join(BASE_DIR, name))
            ]
        except Exception:
            return []

    for fname in folders_to_search:
        folder_path = os.path.join(BASE_DIR, fname)
        if not os.path.isdir(folder_path):
            continue
        try:
            jsonl_files = [f for f in os.listdir(folder_path) if f.endswith(".jsonl")]
        except Exception:
            continue
        for jf in jsonl_files:
            if total >= 50:
                break
            fpath = os.path.join(folder_path, jf)
            file_matches = 0
            try:
                with open(fpath) as f:
                    for line in f:
                        if file_matches >= 10 or total >= 50:
                            break
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        rtype = r.get("type", "")
                        ts = r.get("timestamp", "")
                        text = ""
                        if rtype == "user":
                            msg = r.get("message", {})
                            c = msg.get("content", "")
                            if isinstance(c, str):
                                text = c
                            elif isinstance(c, list):
                                text = " ".join(
                                    b.get("text", "") for b in c
                                    if isinstance(b, dict) and b.get("type") == "text"
                                )
                        elif rtype == "assistant":
                            msg = r.get("message", {})
                            blocks = msg.get("content", [])
                            parts = []
                            for b in blocks:
                                if isinstance(b, dict) and b.get("type") == "text":
                                    parts.append(b.get("text", ""))
                            text = " ".join(parts)
                        else:
                            continue

                        if not text:
                            continue
                        if query_lower in text.lower():
                            # Find the match position for preview context
                            idx = text.lower().find(query_lower)
                            start = max(0, idx - 40)
                            end = min(len(text), idx + len(query) + 40)
                            preview = text[start:end].replace("\n", " ")
                            if start > 0:
                                preview = "..." + preview
                            if end < len(text):
                                preview = preview + "..."
                            match_text = text[idx:idx + len(query)]
                            results.append({
                                "folder": fname,
                                "filename": jf,
                                "session_id": jf.replace(".jsonl", ""),
                                "preview": preview,
                                "match_text": match_text,
                                "record_type": rtype,
                                "timestamp": fmt_date(ts),
                                "path": fpath,
                            })
                            file_matches += 1
                            total += 1
            except Exception:
                continue
        if total >= 50:
            break

    return results


def poll_changes(since):
    """Find files modified after the given epoch timestamp."""
    changed = []
    try:
        for name in os.listdir(BASE_DIR):
            folder_path = os.path.join(BASE_DIR, name)
            if not os.path.isdir(folder_path):
                continue
            try:
                for jf in os.listdir(folder_path):
                    if not jf.endswith(".jsonl"):
                        continue
                    fpath = os.path.join(folder_path, jf)
                    try:
                        mtime = os.path.getmtime(fpath)
                        if mtime > since:
                            changed.append({
                                "folder": name,
                                "filename": jf,
                                "mtime": mtime,
                                "path": fpath,
                            })
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception:
        pass
    return changed


def scan_secrets(filepath):
    """Scan a session file for potential secrets/credentials."""
    matches = []
    seen = set()
    with open(filepath) as f:
        for line_num, line in enumerate(f, 1):
            for label, pattern in SECRET_PATTERNS:
                for m in re.finditer(pattern, line):
                    raw = m.group(0)
                    if raw in seen:
                        continue
                    seen.add(raw)
                    if len(raw) > 10:
                        masked = raw[:6] + "..." + raw[-4:]
                    else:
                        masked = raw[:3] + "..." + raw[-2:]
                    matches.append({
                        "type": label,
                        "masked": masked,
                        "line": line_num,
                    })
    return matches


def export_markdown(filepath):
    """Export a session as readable Markdown."""
    with open(filepath) as f:
        records = [json.loads(line) for line in f]

    lines = []
    sid = Path(filepath).stem
    lines.append(f"# Session: {sid}\n")

    for r in records:
        rtype = r.get("type", "")
        ts = fmt_time(r.get("timestamp", ""))

        if rtype == "user":
            msg = r.get("message", {})
            content = msg.get("content", "")
            if isinstance(content, list):
                for block in content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "text":
                        text = block.get("text", "")
                        if text.strip():
                            lines.append(f"\n## User ({ts})\n\n{text}\n")
                    elif block.get("type") == "tool_result":
                        tid = block.get("tool_use_id", "")[:12]
                        rc = block.get("content", "")
                        if isinstance(rc, list):
                            rc = "\n".join(b.get("text", "") for b in rc if isinstance(b, dict))
                        lines.append(f"\n### Tool Result (id:{tid}...)\n\n```\n{str(rc)[:2000]}\n```\n")
            else:
                text = str(content)
                if text.strip():
                    lines.append(f"\n## User ({ts})\n\n{text}\n")

        elif rtype == "assistant":
            msg = r.get("message", {})
            for block in msg.get("content", []):
                btype = block.get("type", "")
                if btype == "text":
                    text = block.get("text", "")
                    if text.strip():
                        lines.append(f"\n## Assistant ({ts})\n\n{text}\n")
                elif btype == "thinking":
                    text = block.get("thinking", "")
                    if text.strip():
                        lines.append(f"\n### Thinking\n\n<details><summary>Thinking...</summary>\n\n{text}\n\n</details>\n")
                elif btype == "tool_use":
                    name = block.get("name", "?")
                    inp = block.get("input", {})
                    inp_json = json.dumps(inp, ensure_ascii=False, indent=2)
                    lines.append(f"\n### Tool: {name}\n\n```json\n{inp_json[:3000]}\n```\n")

    return "\n".join(lines)


def export_html(filepath):
    """Export a session as standalone HTML."""
    content, count, compact_total = render_session(filepath)
    sid = Path(filepath).stem
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Session: {esc(sid)}</title>
<style>
{CSS}
body {{ background: #0d1117; color: #c9d1d9; font-family: -apple-system, "Segoe UI", sans-serif; padding: 20px; max-width: 900px; margin: 0 auto; }}
.sidebar, .nav-toolbar, .scroll-progress, .minimap, .position-indicator, .filters {{ display: none; }}
.main {{ margin-left: 0; }}
</style>
</head>
<body>
<h1 style="color:#58a6ff">Session: {esc(sid)}</h1>
<p style="color:#8b949e;margin-bottom:16px">{count} entries | Exported {datetime.now().strftime('%Y-%m-%d %H:%M')}</p>
{content}
</body>
</html>"""


# ─── CSS ───
CSS = """
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Segoe UI", sans-serif; background: #0d1117; color: #c9d1d9; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }

/* Layout */
.sidebar { position: fixed; left: 0; top: 0; bottom: 0; width: 300px; background: #010409; border-right: 1px solid #21262d; overflow-y: auto; padding: 12px; z-index: 10; }
.main { margin-left: 300px; padding: 20px; max-width: 900px; }

/* Sidebar */
.sidebar h2 { color: #58a6ff; font-size: 1.1em; margin-bottom: 8px; display: flex; align-items: center; gap: 6px; }
.sidebar input { width: 100%; background: #0d1117; color: #c9d1d9; border: 1px solid #30363d; padding: 6px 8px; border-radius: 6px; font-size: 0.85em; margin-bottom: 8px; }
.folder { margin-bottom: 2px; }
.folder-name { display: block; padding: 5px 8px; border-radius: 4px; cursor: pointer; font-size: 0.82em; color: #8b949e; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.folder-name:hover { background: #161b22; color: #c9d1d9; }
.folder-name.active { background: #1f6feb22; color: #58a6ff; }
.sessions { display: none; padding-left: 12px; }
.sessions.open { display: block; }
.session-item { display: block; padding: 6px 8px; border-radius: 4px; cursor: pointer; font-size: 0.78em; margin-bottom: 2px; border-left: 2px solid transparent; }
.session-item:hover { background: #161b22; }
.session-item.active { background: #1f6feb15; border-left-color: #58a6ff; }
.session-preview { color: #8b949e; font-size: 0.9em; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 240px; }
.session-meta { color: #484f58; font-size: 0.85em; margin-top: 2px; }
.compact-indicator { display: inline-block; background: #a371f722; color: #a371f7; font-size: 0.8em; padding: 0 4px; border-radius: 3px; margin-left: 4px; }

/* Main content */
.header { margin-bottom: 12px; }
.header h1 { color: #58a6ff; font-size: 1.3em; }
.header .meta { color: #8b949e; font-size: 0.82em; margin-top: 2px; }
.compact-summary { background: #a371f712; border: 1px solid #a371f733; border-radius: 8px; padding: 10px 14px; margin-bottom: 14px; font-size: 0.85em; color: #a371f7; }
.compact-summary strong { color: #d2a8ff; }
.filters { margin-bottom: 14px; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; }
.filters button { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 3px 10px; border-radius: 6px; cursor: pointer; font-size: 0.8em; transition: all 0.15s; }
.filters button:hover { border-color: #484f58; }
.filters button.active { background: #388bfd26; border-color: #388bfd; color: #58a6ff; }
.filters button.compact-filter.active { background: #a371f722; border-color: #a371f7; color: #a371f7; }
#search { background: #0d1117; color: #c9d1d9; border: 1px solid #30363d; padding: 5px 8px; border-radius: 6px; width: 220px; font-size: 0.85em; }

/* Entries */
.entry { margin-bottom: 8px; border-left: 3px solid #30363d; padding: 6px 10px; border-radius: 0 6px 6px 0; background: #161b22; transition: all 0.15s; position: relative; }
.entry.user { border-left-color: #3fb950; }
.entry.assistant { border-left-color: #58a6ff; }
.entry.tool { border-left-color: #d29922; }
.entry.tool-result { border-left-color: #d2992280; }
.entry.thinking { border-left-color: #484f58; }
.entry.system { border-left-color: #f85149; }
.entry.progress { border-left-color: #6e7681; }

/* Compact entries */
.entry.compact { border-left-color: #a371f7 !important; background: #1a1028; box-shadow: inset 0 0 0 1px #a371f722; }
.entry.compact::before { content: ''; position: absolute; top: 0; right: 0; width: 0; height: 0; border-top: 16px solid #a371f7; border-left: 16px solid transparent; border-radius: 0 6px 0 0; }
.compact-badge { display: inline-block; font-size: 0.65em; padding: 1px 6px; border-radius: 3px; margin-right: 4px; font-weight: 600; background: #a371f730; color: #d2a8ff; border: 1px solid #a371f744; letter-spacing: 0.3px; }
.compact-curator { background: #a371f730; color: #d2a8ff; border-color: #a371f744; }
.compact-context-cont { background: #da363330; color: #ff7b72; border-color: #da363344; }
.compact-compact-cmd { background: #3fb95030; color: #7ee787; border-color: #3fb95044; }
.compact-compacted { background: #d2992230; color: #e3b341; border-color: #d2992244; }
.compact-summary { background: #1f6feb30; color: #79c0ff; border-color: #1f6feb44; }

/* Only-compact mode */
.only-compact .entry:not(.compact) { display: none !important; }

.tag { display: inline-block; font-size: 0.7em; padding: 1px 5px; border-radius: 3px; margin-right: 4px; font-weight: 600; }
.tag-user { background: #238636; color: #fff; }
.tag-assistant { background: #1f6feb; color: #fff; }
.tag-tool { background: #9e6a03; color: #fff; }
.tag-result { background: #9e6a0366; color: #d29922; }
.tag-thinking { background: #30363d; color: #8b949e; }
.tag-system { background: #da3633; color: #fff; }
.tag-progress { background: #21262d; color: #6e7681; }
.time { color: #484f58; font-size: 0.75em; margin-left: 4px; }
.tid { color: #6e7681; font-size: 0.7em; font-family: monospace; }
.tool-name { color: #d29922; font-weight: 600; font-size: 0.9em; }
.cbtn { background: none; border: none; color: #484f58; cursor: pointer; font-size: 0.7em; margin-left: 6px; }
.content { margin-top: 5px; font-size: 0.88em; line-height: 1.55; white-space: pre-wrap; word-break: break-word; }
.folded .content { display: none; }
.folded .cbtn { color: #58a6ff; }
pre.code-block { background: #0d1117; border: 1px solid #30363d; border-radius: 4px; padding: 6px; overflow-x: auto; margin: 4px 0; font-size: 0.9em; }
code.ic { background: #30363d; padding: 1px 3px; border-radius: 3px; font-size: 0.9em; }
.hidden-default { display: none; }
.show-hidden .hidden-default { display: block; }
.entry.filtered-out { display: none; }

/* API View */
.api-summary { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 10px 14px; margin-bottom: 16px; font-size: 0.85em; color: #8b949e; }
.api-summary strong { color: #58a6ff; }
.api-call { background: #161b22; border: 1px solid #21262d; border-radius: 8px; margin-bottom: 12px; overflow: hidden; }
.api-header { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: #0d1117; border-bottom: 1px solid #21262d; font-size: 0.78em; flex-wrap: wrap; }
.api-num { color: #58a6ff; font-weight: 700; font-size: 1.1em; }
.api-model { color: #8b949e; }
.api-time { color: #484f58; }
.api-stop { padding: 1px 6px; border-radius: 3px; font-size: 0.9em; font-weight: 600; }
.stop-end { background: #23863622; color: #3fb950; }
.stop-tool { background: #9e6a0322; color: #d29922; }
.stop-other { background: #30363d; color: #8b949e; }
.api-turns { color: #6e7681; }
.api-tokens { color: #484f58; font-family: monospace; font-size: 0.9em; }
.api-mid { color: #30363d; font-family: monospace; font-size: 0.85em; margin-left: auto; }
.tok-bar { display: flex; height: 3px; background: #21262d; }
.tok-seg { height: 100%; }
.tok-cache-read { background: #3fb95088; }
.tok-cache-create { background: #d2992288; }
.tok-input { background: #58a6ff88; }
.api-pair { display: grid; grid-template-columns: 1fr auto 1fr; gap: 0; min-height: 40px; }
.api-req { padding: 8px 10px; border-right: 1px solid #21262d; }
.api-resp { padding: 8px 10px; }
.api-arrow { display: flex; align-items: center; justify-content: center; color: #30363d; font-size: 1.3em; padding: 0 6px; background: #0d1117; }
.api-label { font-size: 0.65em; font-weight: 700; text-transform: uppercase; letter-spacing: 1px; color: #484f58; margin-bottom: 6px; }
.api-msg { margin-bottom: 6px; padding: 4px 6px; border-radius: 4px; font-size: 0.82em; border-left: 2px solid #30363d; }
.api-msg.api-user { border-left-color: #3fb950; background: #23863608; }
.api-msg.api-tool-result { border-left-color: #d2992280; background: #9e6a0308; }
.api-msg.api-error { border-left-color: #f85149; background: #da363308; }
.api-msg.api-thinking { border-left-color: #484f58; background: #30363d10; }
.api-msg.api-text { border-left-color: #58a6ff; background: #1f6feb08; }
.api-msg.api-tool-call { border-left-color: #d29922; background: #9e6a0310; }
.api-msg.api-compact { box-shadow: inset 0 0 0 1px #a371f722; }
.api-role { font-size: 0.85em; font-weight: 600; color: #6e7681; }
.api-size { font-size: 0.8em; color: #484f58; font-family: monospace; }
.api-tid { font-size: 0.75em; color: #484f58; font-family: monospace; }
.api-tool-name { color: #d29922; font-weight: 600; }
.api-body { margin-top: 3px; color: #8b949e; font-size: 0.92em; white-space: pre-wrap; word-break: break-word; max-height: 200px; overflow-y: auto; }
.api-empty { color: #30363d; font-style: italic; font-size: 0.85em; }
/* Multi-turn styling */
.turn-label { font-size: 0.65em; font-weight: 700; color: #484f58; background: #21262d; display: inline-block; padding: 1px 6px; border-radius: 3px; margin-bottom: 4px; }
.api-turn { border-top: 1px dashed #21262d; }
.api-turn.turn-first { border-top: none; }
.api-turn .api-arrow { color: #21262d; font-size: 1em; }
.api-turn.turn-first .api-req::before,
.api-turn.turn-mid .api-req::before,
.api-turn.turn-last .api-req::before { content: ''; position: absolute; left: -1px; top: 0; bottom: 0; width: 2px; background: linear-gradient(180deg, #58a6ff33, #58a6ff11); }
.api-turn .api-req { position: relative; }
.view-toggle { display: flex; gap: 0; margin-bottom: 14px; }
.view-toggle button { background: #21262d; color: #8b949e; border: 1px solid #30363d; padding: 5px 14px; font-size: 0.82em; cursor: pointer; transition: all 0.15s; }
.view-toggle button:first-child { border-radius: 6px 0 0 6px; }
.view-toggle button:not(:first-child):not(:last-child) { border-radius: 0; }
.view-toggle button:last-child { border-radius: 0 6px 6px 0; }
.view-toggle button.active { background: #1f6feb22; color: #58a6ff; border-color: #1f6feb; }

/* Floating nav toolbar */
.nav-toolbar { position: fixed; right: 20px; bottom: 20px; display: flex; flex-direction: column; gap: 6px; z-index: 100; opacity: 0; pointer-events: none; transition: opacity 0.2s; }
.nav-toolbar.visible { opacity: 1; pointer-events: auto; }
.nav-btn { width: 40px; height: 40px; border-radius: 10px; background: #21262d; border: 1px solid #30363d; color: #8b949e; cursor: pointer; display: flex; align-items: center; justify-content: center; font-size: 16px; transition: all 0.15s; position: relative; }
.nav-btn:hover { background: #30363d; color: #c9d1d9; border-color: #484f58; }
.nav-btn:hover .nav-tip { opacity: 1; transform: translateX(0); pointer-events: auto; }
.nav-tip { position: absolute; right: 50px; background: #161b22; border: 1px solid #30363d; color: #8b949e; padding: 3px 8px; border-radius: 4px; font-size: 0.72em; white-space: nowrap; opacity: 0; transform: translateX(6px); transition: all 0.15s; pointer-events: none; }

/* Scroll progress bar */
.scroll-progress { position: fixed; top: 0; left: 300px; right: 0; height: 3px; background: #21262d; z-index: 50; }
.scroll-progress-bar { height: 100%; background: linear-gradient(90deg, #58a6ff, #a371f7); width: 0%; transition: width 0.1s; border-radius: 0 2px 2px 0; }

/* Entry position indicator */
.position-indicator { position: fixed; right: 20px; top: 20px; background: #161b22; border: 1px solid #30363d; border-radius: 6px; padding: 4px 10px; font-size: 0.75em; color: #484f58; z-index: 50; opacity: 0; transition: opacity 0.3s; pointer-events: none; }
.position-indicator.visible { opacity: 1; }

/* Minimap */
.minimap { position: fixed; right: 4px; top: 60px; bottom: 80px; width: 10px; background: #0d1117; border-radius: 4px; z-index: 50; opacity: 0; transition: opacity 0.2s; cursor: pointer; }
.minimap.visible { opacity: 1; }
.minimap:hover { width: 14px; }
.minimap-dot { position: absolute; left: 1px; right: 1px; height: 2px; border-radius: 1px; }
.minimap-dot.mm-user { background: #3fb950; }
.minimap-dot.mm-assistant { background: #58a6ff; }
.minimap-dot.mm-tool { background: #d29922; }
.minimap-dot.mm-compact { background: #a371f7; }
.minimap-viewport { position: absolute; left: -1px; right: -1px; background: #c9d1d915; border: 1px solid #58a6ff44; border-radius: 2px; pointer-events: none; }

/* Sticky filters bar */
.filters { margin-bottom: 14px; display: flex; gap: 6px; flex-wrap: wrap; align-items: center; position: sticky; top: 0; background: #0d1117; z-index: 20; padding: 8px 0; border-bottom: 1px solid #21262d; }

/* Keyboard shortcut hint */
.kbd { display: inline-block; background: #21262d; border: 1px solid #30363d; border-radius: 3px; padding: 0 4px; font-size: 0.7em; font-family: monospace; color: #6e7681; margin-left: 4px; }

/* Welcome */
.welcome { text-align: center; margin-top: 100px; color: #484f58; }
.welcome h2 { color: #58a6ff; font-size: 1.6em; margin-bottom: 8px; }
.welcome p { font-size: 0.95em; }
.welcome .logo { font-size: 3em; margin-bottom: 12px; opacity: 0.6; }

/* Loading */
.loading { text-align: center; color: #8b949e; margin-top: 60px; }

/* Jump-to highlight — double blink */
.entry.highlight-jump, .api-msg.highlight-jump { animation: doubleBlink 1s ease-out; }
@keyframes doubleBlink {
  0%   { box-shadow: 0 0 0 3px #58a6ff88; background-color: #58a6ff15; }
  25%  { box-shadow: none; background-color: transparent; }
  40%  { box-shadow: 0 0 0 3px #58a6ff88; background-color: #58a6ff15; }
  70%  { box-shadow: none; background-color: transparent; }
  100% { box-shadow: none; background-color: transparent; }
}

/* Clickable tool links */
.api-clickable { cursor: pointer; transition: all 0.15s; }
.api-clickable:hover { filter: brightness(1.2); box-shadow: 0 0 0 1px #58a6ff44; }
.api-clickable .api-tid, .api-clickable .tid { text-decoration: underline; text-decoration-style: dashed; text-underline-offset: 2px; }

/* Scrollbar */
::-webkit-scrollbar { width: 6px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #30363d; border-radius: 3px; }

/* Stats View */
.stats-cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 20px; }
.stat-card { background: #161b22; border: 1px solid #21262d; border-radius: 10px; padding: 16px; text-align: center; }
.stat-card .stat-value { font-size: 1.6em; font-weight: 700; color: #58a6ff; }
.stat-card .stat-label { font-size: 0.78em; color: #8b949e; margin-top: 4px; }
.stat-card.cost .stat-value { color: #3fb950; }
.stat-card.duration .stat-value { color: #d29922; }
.stat-card.tokens .stat-value { color: #a371f7; }
.stats-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-bottom: 20px; }
.stats-chart-box { background: #161b22; border: 1px solid #21262d; border-radius: 10px; padding: 16px; }
.stats-chart-box h3 { font-size: 0.9em; color: #8b949e; margin-bottom: 12px; }
.stats-chart-box.full-width { grid-column: 1 / -1; }
.stats-chart-box svg { width: 100%; }

/* Secret Warning */
.secret-banner { background: #da363322; border: 1px solid #f8514966; border-radius: 8px; padding: 10px 14px; margin-bottom: 14px; color: #ff7b72; font-size: 0.88em; display: none; }
.secret-banner.visible { display: block; }
.secret-banner strong { color: #f85149; }
.secret-badge { display: inline-block; background: #da3633; color: #fff; font-size: 0.65em; padding: 1px 5px; border-radius: 8px; margin-left: 4px; font-weight: 700; vertical-align: middle; }
.secret-list { margin-top: 8px; font-family: monospace; font-size: 0.85em; }
.secret-list div { padding: 2px 0; }
.secret-type { color: #f85149; font-weight: 600; margin-right: 6px; }
.secret-masked { color: #d29922; }
.entry[data-has-secret="1"] { box-shadow: inset 0 0 0 1px #f8514944; }

/* Timeline View */
.timeline-container { position: relative; overflow-x: auto; overflow-y: hidden; background: #0d1117; border: 1px solid #21262d; border-radius: 10px; padding: 20px 10px 10px; min-height: 300px; cursor: grab; user-select: none; }
.timeline-container.dragging { cursor: grabbing; }
.timeline-inner { position: relative; min-height: 260px; }
.timeline-axis { position: absolute; bottom: 0; left: 0; right: 0; height: 28px; border-top: 1px solid #30363d; }
.timeline-tick { position: absolute; bottom: 0; color: #484f58; font-size: 0.7em; font-family: monospace; text-align: center; transform: translateX(-50%); }
.timeline-tick::before { content: ''; position: absolute; top: -6px; left: 50%; width: 1px; height: 6px; background: #30363d; }
.timeline-card { position: absolute; width: 80px; padding: 4px 6px; border-radius: 5px; font-size: 0.68em; line-height: 1.3; cursor: pointer; overflow: hidden; white-space: nowrap; text-overflow: ellipsis; border: 1px solid transparent; transition: all 0.12s; z-index: 1; }
.timeline-card:hover { z-index: 10; filter: brightness(1.3); border-color: #58a6ff88; overflow: visible; white-space: normal; width: auto; min-width: 80px; max-width: 200px; }
.timeline-card.tc-user { background: #23863644; color: #3fb950; border-color: #23863633; }
.timeline-card.tc-assistant { background: #1f6feb33; color: #79c0ff; border-color: #1f6feb33; }
.timeline-card.tc-tool { background: #9e6a0333; color: #e3b341; border-color: #9e6a0333; }
.timeline-card.tc-thinking { background: #30363d88; color: #8b949e; border-color: #30363d; }
.timeline-card.tc-system { background: #da363322; color: #ff7b72; border-color: #da363333; }
.timeline-card.tc-progress { background: #21262d; color: #6e7681; border-color: #21262d; }
.timeline-cursor { position: absolute; top: 0; bottom: 28px; width: 1px; background: #58a6ff55; pointer-events: none; z-index: 20; }
.timeline-cursor-label { position: absolute; top: -18px; left: 50%; transform: translateX(-50%); font-size: 0.65em; color: #58a6ff; font-family: monospace; white-space: nowrap; background: #0d1117cc; padding: 1px 4px; border-radius: 3px; }
.timeline-controls { display: flex; gap: 6px; margin-bottom: 8px; align-items: center; }
.timeline-controls button { background: #21262d; color: #c9d1d9; border: 1px solid #30363d; padding: 3px 10px; border-radius: 6px; cursor: pointer; font-size: 0.82em; }
.timeline-controls button:hover { border-color: #484f58; }
.timeline-controls .tl-legend { display: flex; gap: 10px; margin-left: 16px; font-size: 0.72em; color: #8b949e; }
.timeline-controls .tl-legend span::before { content: ''; display: inline-block; width: 8px; height: 8px; border-radius: 2px; margin-right: 3px; vertical-align: middle; }
.timeline-controls .tl-legend .lg-user::before { background: #3fb950; }
.timeline-controls .tl-legend .lg-assistant::before { background: #58a6ff; }
.timeline-controls .tl-legend .lg-tool::before { background: #d29922; }
.timeline-controls .tl-legend .lg-thinking::before { background: #6e7681; }
.timeline-controls .tl-legend .lg-system::before { background: #f85149; }

/* Drop zone */
.drop-zone { border: 2px dashed #30363d; border-radius: 12px; padding: 40px 20px; text-align: center; color: #484f58; margin-top: 30px; transition: all 0.2s; cursor: default; }
.drop-zone.drag-over { border-color: #58a6ff; background: #1f6feb11; color: #58a6ff; }
.drop-zone p { font-size: 0.95em; margin-bottom: 6px; }
.drop-zone .drop-icon { font-size: 2em; margin-bottom: 8px; opacity: 0.5; }
.drop-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: #0d111799; z-index: 1000; align-items: center; justify-content: center; }
.drop-overlay.visible { display: flex; }
.drop-overlay-inner { border: 3px dashed #58a6ff; border-radius: 16px; padding: 60px 80px; text-align: center; color: #58a6ff; font-size: 1.2em; background: #161b22ee; }

/* Global Search Results */
.search-results { padding: 0; }
.search-results h2 { color: #58a6ff; font-size: 1.2em; margin-bottom: 12px; }
.search-results .sr-meta { color: #484f58; font-size: 0.82em; margin-bottom: 16px; }
.search-result { background: #161b22; border: 1px solid #21262d; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px; cursor: pointer; transition: all 0.15s; }
.search-result:hover { border-color: #388bfd; background: #1f6feb08; }
.search-result .sr-header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; font-size: 0.78em; flex-wrap: wrap; }
.search-result .sr-folder { color: #8b949e; }
.search-result .sr-type { display: inline-block; font-size: 0.75em; padding: 1px 5px; border-radius: 3px; font-weight: 600; }
.search-result .sr-type-user { background: #238636; color: #fff; }
.search-result .sr-type-assistant { background: #1f6feb; color: #fff; }
.search-result .sr-time { color: #484f58; }
.search-result .sr-preview { color: #c9d1d9; font-size: 0.88em; line-height: 1.5; }
.search-result .sr-preview mark { background: #d2992244; color: #e3b341; padding: 0 2px; border-radius: 2px; }
.search-result .sr-session { color: #484f58; font-size: 0.72em; margin-top: 4px; font-family: monospace; }

/* Live Watch */
.watch-toggle { background: none; border: 1px solid #30363d; color: #484f58; border-radius: 4px; cursor: pointer; font-size: 0.75em; padding: 2px 8px; margin-left: 8px; transition: all 0.15s; display: inline-flex; align-items: center; gap: 4px; }
.watch-toggle:hover { border-color: #484f58; color: #8b949e; }
.watch-toggle.active { border-color: #3fb950; color: #3fb950; }
.watch-dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: #484f58; }
.watch-toggle.active .watch-dot { background: #3fb950; animation: watchPulse 1.5s infinite; }
@keyframes watchPulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.3; } }
.session-badge { display: inline-block; font-size: 0.6em; padding: 0 4px; border-radius: 3px; font-weight: 700; margin-left: 4px; vertical-align: middle; }
.session-badge-new { background: #23863644; color: #3fb950; border: 1px solid #23863666; }
.session-badge-upd { background: #d2992244; color: #e3b341; border: 1px solid #d2992266; }
.reload-banner { background: #1f6feb22; border: 1px solid #388bfd66; border-radius: 8px; padding: 8px 14px; margin-bottom: 12px; color: #79c0ff; font-size: 0.85em; cursor: pointer; display: none; transition: all 0.15s; }
.reload-banner:hover { background: #1f6feb33; }

/* Export dropdown */
.export-dropdown { position: relative; display: inline-block; }
.export-dropdown-btn { background: #21262d; color: #8b949e; border: 1px solid #30363d; padding: 5px 14px; font-size: 0.82em; cursor: pointer; border-radius: 6px; }
.export-dropdown-btn:hover { border-color: #484f58; color: #c9d1d9; }
.export-menu { display: none; position: absolute; top: 100%; left: 0; background: #161b22; border: 1px solid #30363d; border-radius: 6px; z-index: 30; min-width: 140px; margin-top: 4px; overflow: hidden; }
.export-menu.open { display: block; }
.export-menu a { display: block; padding: 8px 14px; color: #c9d1d9; font-size: 0.82em; text-decoration: none; }
.export-menu a:hover { background: #21262d; }
"""

def compute_timeline(filepath):
    """Parse JSONL and return timeline entries with timestamp, type, summary, and id."""
    entries = []
    idx = 0
    with open(filepath) as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                idx += 1
                continue
            ts = r.get("timestamp", "")
            rtype = r.get("type", "")
            summary = ""

            if rtype == "user":
                msg = r.get("message", {})
                c = msg.get("content", "")
                if isinstance(c, list):
                    parts = []
                    for block in c:
                        if isinstance(block, dict):
                            if block.get("type") == "text":
                                parts.append(block.get("text", ""))
                            elif block.get("type") == "tool_result":
                                parts.append("[tool_result]")
                    summary = " ".join(parts)[:60]
                else:
                    summary = str(c)[:60]
                etype = "user"
            elif rtype == "assistant":
                msg = r.get("message", {})
                blocks = msg.get("content", [])
                etype = "assistant"
                for block in blocks:
                    if not isinstance(block, dict):
                        continue
                    bt = block.get("type", "")
                    if bt == "text":
                        summary = block.get("text", "")[:60]
                        break
                    elif bt == "thinking":
                        summary = "thinking"
                        etype = "thinking"
                        break
                    elif bt == "tool_use":
                        summary = block.get("name", "tool")
                        etype = "tool"
                        break
            elif rtype == "system":
                etype = "system"
                summary = r.get("subtype", "system")[:60]
            elif rtype == "progress":
                etype = "progress"
                data = r.get("data", {})
                summary = data.get("type", "progress")[:60]
            else:
                idx += 1
                continue

            if ts:
                entries.append({
                    "ts": ts,
                    "type": etype,
                    "summary": summary.replace("\n", " "),
                    "id": idx,
                })
            idx += 1

    entries.sort(key=lambda e: e["ts"])
    return entries


UPLOAD_DIR = os.path.join(tempfile.gettempdir(), "csv_uploads")


# ─── Handler ───
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        params = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            self.send_html(self.page_index())
        elif path == "/api/folders":
            self.send_json(self.api_folders())
        elif path == "/api/sessions":
            folder = params.get("folder", [""])[0]
            self.send_json(self.api_sessions(folder))
        elif path == "/api/view":
            file = params.get("file", [""])[0]
            self.send_html_fragment(self.api_view(file))
        elif path == "/api/apiview":
            file = params.get("file", [""])[0]
            self.send_html_fragment(self.api_apiview(file))
        elif path == "/api/stats":
            file = params.get("file", [""])[0]
            if not file or not os.path.isfile(file):
                self.send_json({"error": "file not found"})
            else:
                try:
                    self.send_json(compute_stats(file))
                except Exception as e:
                    self.send_json({"error": str(e)})
        elif path == "/api/secrets":
            file = params.get("file", [""])[0]
            if not file or not os.path.isfile(file):
                self.send_json({"error": "file not found"})
            else:
                try:
                    self.send_json(scan_secrets(file))
                except Exception as e:
                    self.send_json({"error": str(e)})
        elif path == "/api/search":
            q = params.get("q", [""])[0]
            folder = params.get("folder", [""])[0] or None
            if not q:
                self.send_json([])
            else:
                try:
                    self.send_json(search_sessions(q, folder))
                except Exception as e:
                    self.send_json({"error": str(e)})
        elif path == "/api/poll":
            since = float(params.get("since", ["0"])[0])
            try:
                self.send_json(poll_changes(since))
            except Exception as e:
                self.send_json({"error": str(e)})
        elif path == "/api/timeline":
            file = params.get("file", [""])[0]
            if not file or not os.path.isfile(file):
                self.send_json({"error": "file not found"})
            else:
                try:
                    self.send_json(compute_timeline(file))
                except Exception as e:
                    self.send_json({"error": str(e)})
        elif path == "/api/export":
            file = params.get("file", [""])[0]
            fmt = params.get("fmt", ["md"])[0]
            if not file or not os.path.isfile(file):
                self.send_error(404)
            else:
                try:
                    sid = Path(file).stem[:20]
                    if fmt == "html":
                        content = export_html(file)
                        self.send_response(200)
                        self.send_header("Content-Type", "text/html; charset=utf-8")
                        self.send_header("Content-Disposition", f'attachment; filename="session_{sid}.html"')
                        self.end_headers()
                        self.wfile.write(content.encode())
                    else:
                        content = export_markdown(file)
                        self.send_response(200)
                        self.send_header("Content-Type", "text/markdown; charset=utf-8")
                        self.send_header("Content-Disposition", f'attachment; filename="session_{sid}.md"')
                        self.end_headers()
                        self.wfile.write(content.encode())
                except Exception as e:
                    self.send_error(500, str(e))
        else:
            self.send_error(404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/upload":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                # Validate each line is valid JSON
                lines = body.strip().split("\n")
                for i, line in enumerate(lines):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        json.loads(line)
                    except json.JSONDecodeError:
                        self.send_json({"ok": False, "error": f"Invalid JSON at line {i + 1}"})
                        return
                # Save to temp dir
                os.makedirs(UPLOAD_DIR, exist_ok=True)
                fname = f"upload_{int(datetime.now().timestamp())}_{os.getpid()}.jsonl"
                fpath = os.path.join(UPLOAD_DIR, fname)
                with open(fpath, "w") as f:
                    f.write(body)
                self.send_json({"ok": True, "path": fpath})
            except Exception as e:
                self.send_json({"ok": False, "error": str(e)})
        else:
            self.send_error(404)

    def send_html(self, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode())

    def send_html_fragment(self, content):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(content.encode())

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def api_folders(self):
        folders = []
        try:
            for name in sorted(os.listdir(BASE_DIR)):
                full = os.path.join(BASE_DIR, name)
                if os.path.isdir(full):
                    cnt = sum(1 for f in os.listdir(full) if f.endswith(".jsonl"))
                    if cnt > 0:
                        readable = name.replace("-", "/")
                        folders.append({"name": name, "readable": readable, "count": cnt})
        except Exception as e:
            return {"error": str(e)}
        return folders

    def api_sessions(self, folder):
        sessions = []
        folder_path = os.path.join(BASE_DIR, folder)
        if not os.path.isdir(folder_path):
            return {"error": "not found"}
        jsonl_files = sorted(
            [f for f in os.listdir(folder_path) if f.endswith(".jsonl")],
            key=lambda f: os.path.getmtime(os.path.join(folder_path, f)),
            reverse=True,
        )
        for fname in jsonl_files:
            fpath = os.path.join(folder_path, fname)
            summary = get_session_summary(fpath)
            sessions.append({
                "filename": fname,
                "session_id": fname.replace(".jsonl", ""),
                "path": fpath,
                **summary,
            })
        return sessions

    def api_view(self, filepath):
        if not filepath or not os.path.isfile(filepath):
            return '<div class="loading">File not found</div>'
        try:
            content, count, compact_total = render_session(filepath)
            sid = Path(filepath).stem
            compact_bar = ""
            if compact_total > 0:
                compact_bar = f'<div class="compact-summary"><strong>Compact Content Detected:</strong> {compact_total} entries contain compressed/summarized content (purple border + corner marker)</div>'
            return f'<div class="header"><h1>Session: {esc(sid[:20])}…</h1><div class="meta">{count} entries | {compact_total} compact | {esc(filepath)}</div></div>{compact_bar}{content}'
        except Exception as e:
            return f'<div class="loading">Error: {esc(str(e))}</div>'

    def api_apiview(self, filepath):
        if not filepath or not os.path.isfile(filepath):
            return '<div class="loading">File not found</div>'
        try:
            content, count = render_api_view(filepath)
            sid = Path(filepath).stem
            return f'<div class="header"><h1>API View: {esc(sid[:20])}…</h1><div class="meta">{count} API calls | {esc(filepath)}</div></div>{content}'
        except Exception as e:
            import traceback
            return f'<div class="loading">Error: {esc(str(e))}<br><pre>{esc(traceback.format_exc())}</pre></div>'

    def page_index(self):
        return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="UTF-8">
<title>Claude Session Viewer</title>
<style>{CSS}</style>
</head>
<body>

<div class="sidebar">
  <h2><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#58a6ff" stroke-width="2"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg> Sessions <button class="watch-toggle" id="watch-toggle" onclick="toggleWatch()" title="Live Watch — auto-detect new/modified sessions"><span class="watch-dot"></span>Watch</button></h2>
  <input type="text" id="folder-search" placeholder="搜尋資料夾… / Enter=全域搜尋 / 以 / 開頭=全域搜尋" onkeydown="handleSearchKey(event)" oninput="filterFolders(this.value)">
  <div id="folder-list"></div>
</div>

<!-- Drop overlay for file upload -->
<div class="drop-overlay" id="drop-overlay"><div class="drop-overlay-inner">Drop .jsonl file here</div></div>

<!-- Scroll progress -->
<div class="scroll-progress"><div class="scroll-progress-bar" id="scroll-bar"></div></div>

<!-- Position indicator -->
<div class="position-indicator" id="pos-indicator"></div>

<!-- Minimap -->
<div class="minimap" id="minimap"><div class="minimap-viewport" id="minimap-vp"></div></div>

<!-- Floating nav toolbar -->
<div class="nav-toolbar" id="nav-toolbar">
  <button class="nav-btn" onclick="goTop()" title="回到頂端"><span class="nav-tip">頂端 <span class="kbd">T</span></span>&#x25B2;</button>
  <button class="nav-btn" onclick="goBottom()" title="跳到底部"><span class="nav-tip">底部 <span class="kbd">B</span></span>&#x25BC;</button>
  <button class="nav-btn" onclick="jumpPrev()" title="上一個 User"><span class="nav-tip">上一個 User <span class="kbd">K</span></span>&#x25C0;</button>
  <button class="nav-btn" onclick="jumpNext()" title="下一個 User"><span class="nav-tip">下一個 User <span class="kbd">J</span></span>&#x25B6;</button>
  <button class="nav-btn" onclick="jumpPrevCompact()" title="上一個 Compact"><span class="nav-tip">上一個 Compact <span class="kbd">P</span></span><span style="color:#a371f7">&#x25C0;</span></button>
  <button class="nav-btn" onclick="jumpNextCompact()" title="下一個 Compact"><span class="nav-tip">下一個 Compact <span class="kbd">N</span></span><span style="color:#a371f7">&#x25B6;</span></button>
</div>

<div class="main">
  <div class="reload-banner" id="reload-banner" onclick="reloadCurrentSession()">Session updated — click to reload</div>
  <div id="viewer">
    <div class="welcome">
      <div class="logo">
        <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="#30363d" stroke-width="1.5"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
      </div>
      <h2>Claude Code Session Viewer</h2>
      <p>從左側選擇專案和 session 開始瀏覽</p>
      <div class="drop-zone" id="welcome-drop-zone">
        <div class="drop-icon">&#x1F4C4;</div>
        <p>Drag &amp; drop a .jsonl file to view</p>
        <p style="font-size:0.78em;color:#30363d">Or select a session from the sidebar</p>
      </div>
      <div style="margin-top:20px;color:#30363d;font-size:0.8em">
        快捷鍵：<span class="kbd">T</span> 頂端 <span class="kbd">B</span> 底部 <span class="kbd">J</span>/<span class="kbd">K</span> 下/上一個 User <span class="kbd">N</span>/<span class="kbd">P</span> 下/上一個 Compact <span class="kbd">/</span> 全域搜尋
      </div>
    </div>
  </div>
</div>

<script>
let currentFolder = null;
let currentFile = null;

async function loadFolders() {{
  const res = await fetch('/api/folders');
  const folders = await res.json();
  const list = document.getElementById('folder-list');
  list.innerHTML = '';
  folders.forEach(f => {{
    const div = document.createElement('div');
    div.className = 'folder';
    div.dataset.name = f.name;
    div.dataset.readable = f.readable;
    div.innerHTML = `
      <div class="folder-name" onclick="toggleFolder(this, '${{f.name}}')" title="${{f.readable}}">
        ${{f.readable.split('/').pop() || f.readable}} <span style="color:#484f58;font-size:0.85em">(${{f.count}})</span>
      </div>
      <div class="sessions" id="sessions-${{f.name}}"></div>
    `;
    list.appendChild(div);
  }});
}}

async function toggleFolder(el, folder) {{
  const sessDiv = document.getElementById('sessions-' + folder);
  if (sessDiv.classList.contains('open')) {{
    sessDiv.classList.remove('open');
    el.classList.remove('active');
    return;
  }}
  document.querySelectorAll('.sessions.open').forEach(s => s.classList.remove('open'));
  document.querySelectorAll('.folder-name.active').forEach(e => e.classList.remove('active'));
  el.classList.add('active');
  sessDiv.classList.add('open');
  sessDiv.innerHTML = '<div style="color:#484f58;font-size:0.8em;padding:4px">Loading...</div>';

  const res = await fetch('/api/sessions?folder=' + encodeURIComponent(folder));
  const sessions = await res.json();
  sessDiv.innerHTML = '';
  sessions.forEach(s => {{
    const item = document.createElement('div');
    item.className = 'session-item';
    item.dataset.path = s.path;
    item.onclick = () => loadSession(s.path, item);
    const compactTag = s.has_compact ? `<span class="compact-indicator">C:${{s.compact_count}}</span>` : '';
    item.innerHTML = `
      <div class="session-preview">${{escHtml(s.preview)}}${{compactTag}}</div>
      <div class="session-meta">${{s.start}} · ${{s.records}} records · ${{s.model || '?'}}</div>
    `;
    sessDiv.appendChild(item);
  }});
}}

async function loadSession(filepath, el) {{
  document.querySelectorAll('.session-item.active').forEach(e => e.classList.remove('active'));
  if (el) el.classList.add('active');
  currentFile = filepath;

  const viewer = document.getElementById('viewer');
  viewer.innerHTML = '<div class="loading">載入中...</div>';

  const res = await fetch('/api/view?file=' + encodeURIComponent(filepath));
  const html = await res.text();

  viewer.innerHTML = `
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:14px;flex-wrap:wrap">
      <div class="view-toggle">
        <button class="active" onclick="switchView('chat', this)">Chat View</button>
        <button onclick="switchView('api', this)">API View</button>
        <button onclick="switchView('stats', this)">Stats</button>
        <button onclick="switchView('timeline', this)">Timeline</button>
      </div>
      <div class="export-dropdown">
        <button class="export-dropdown-btn" onclick="toggleExportMenu()">Export &#x25BE;</button>
        <div class="export-menu" id="export-menu">
          <a href="#" onclick="doExport('md');return false">Markdown (.md)</a>
          <a href="#" onclick="doExport('html');return false">HTML (.html)</a>
        </div>
      </div>
      <span id="secret-badge-area"></span>
    </div>
    <div class="secret-banner" id="secret-banner"></div>
    <div id="chat-view">
      <div class="filters">
        <button class="active" data-f="user" onclick="tf(this)">User</button>
        <button class="active" data-f="assistant" onclick="tf(this)">Assistant</button>
        <button class="active" data-f="tool" onclick="tf(this)">Tool</button>
        <button data-f="thinking" onclick="tf(this)">Thinking</button>
        <button data-f="system" onclick="tf(this)">System</button>
        <button data-f="progress" onclick="tf(this)">Progress</button>
        <button class="compact-filter" data-f="compact" onclick="toggleCompactOnly(this)">Only Compact</button>
        <input type="text" id="search" placeholder="搜尋..." oninput="doSearch(this.value)">
        <button onclick="foldAll()" style="margin-left:auto">全部摺疊</button>
        <button onclick="unfoldAll()">全部展開</button>
      </div>
      <div id="entries">${{html}}</div>
    </div>
    <div id="api-view" style="display:none"></div>
    <div id="stats-view" style="display:none"></div>
    <div id="timeline-view" style="display:none"></div>
  `;
  applyFilters();
  checkSecrets();
}}

const activeFilters = new Set(['user', 'assistant', 'tool']);
let searchVal = '';
let compactOnly = false;

function tf(btn) {{
  const f = btn.dataset.f;
  if (activeFilters.has(f)) {{
    activeFilters.delete(f);
    btn.classList.remove('active');
  }} else {{
    activeFilters.add(f);
    btn.classList.add('active');
  }}
  const showHidden = activeFilters.has('thinking') || activeFilters.has('system') || activeFilters.has('progress');
  document.getElementById('entries').classList.toggle('show-hidden', showHidden);
  applyFilters();
}}

function toggleCompactOnly(btn) {{
  compactOnly = !compactOnly;
  btn.classList.toggle('active', compactOnly);
  document.getElementById('entries').classList.toggle('only-compact', compactOnly);
}}

function doSearch(val) {{
  searchVal = val.toLowerCase();
  applyFilters();
}}

function applyFilters() {{
  document.querySelectorAll('#entries > .entry').forEach(el => {{
    const t = el.dataset.type;
    const matchF = activeFilters.has(t);
    const matchS = !searchVal || el.textContent.toLowerCase().includes(searchVal);
    el.classList.toggle('filtered-out', !(matchF && matchS));
  }});
}}

function fold(btn) {{
  btn.closest('.entry').classList.toggle('folded');
}}
function foldAll() {{
  document.querySelectorAll('#entries > .entry').forEach(e => e.classList.add('folded'));
}}
function unfoldAll() {{
  document.querySelectorAll('#entries > .entry').forEach(e => e.classList.remove('folded'));
}}

function filterFolders(val) {{
  val = val.toLowerCase();
  document.querySelectorAll('.folder').forEach(f => {{
    const match = f.dataset.readable.toLowerCase().includes(val) || f.dataset.name.toLowerCase().includes(val);
    f.style.display = match ? '' : 'none';
  }});
}}

function escHtml(s) {{
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}}

// ─── Tool ID Jump ───
function getActiveView() {{
  // Return the currently visible view container
  const apiDiv = document.getElementById('api-view');
  if (apiDiv && apiDiv.style.display !== 'none' && apiDiv.innerHTML.trim()) {{
    return apiDiv;
  }}
  return document.getElementById('chat-view') || document;
}}

function blinkTarget(target) {{
  // Unfold if folded
  if (target.classList.contains('folded')) target.classList.remove('folded');
  // If inside a closed <details>, open it
  const details = target.closest('details');
  if (details && !details.open) details.open = true;
  // Scroll and double-blink
  target.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  target.classList.remove('highlight-jump');
  void target.offsetWidth;
  target.classList.add('highlight-jump');
  setTimeout(() => target.classList.remove('highlight-jump'), 1100);
}}

function jumpToResult(el) {{
  const callId = el.dataset.callId;
  if (!callId) return;
  // Search within the currently active view first
  const view = getActiveView();
  let target = view.querySelector(`[data-result-id="${{callId}}"]`);
  // Fallback: search entire document
  if (!target) target = document.querySelector(`[data-result-id="${{callId}}"]`);
  if (target) {{
    blinkTarget(target);
  }} else {{
    // Not found — flash red dashed outline on the source
    el.style.outline = '2px dashed #f85149';
    el.style.outlineOffset = '2px';
    setTimeout(() => {{ el.style.outline = ''; el.style.outlineOffset = ''; }}, 1000);
  }}
}}

function jumpToCall(el) {{
  const resultId = el.dataset.resultId;
  if (!resultId) return;
  const view = getActiveView();
  let target = view.querySelector(`[data-call-id="${{resultId}}"]`);
  if (!target) target = document.querySelector(`[data-call-id="${{resultId}}"]`);
  if (target) {{
    blinkTarget(target);
  }} else {{
    el.style.outline = '2px dashed #f85149';
    el.style.outlineOffset = '2px';
    setTimeout(() => {{ el.style.outline = ''; el.style.outlineOffset = ''; }}, 1000);
  }}
}}

// ─── View Toggle ───
let apiViewLoaded = false;
let apiStatsLoaded = false;
let apiTimelineLoaded = false;

async function switchView(view, btn) {{
  document.querySelectorAll('.view-toggle button').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');

  const chatDiv = document.getElementById('chat-view');
  const apiDiv = document.getElementById('api-view');
  const statsDiv = document.getElementById('stats-view');
  const timelineDiv = document.getElementById('timeline-view');

  chatDiv.style.display = 'none';
  apiDiv.style.display = 'none';
  if (statsDiv) statsDiv.style.display = 'none';
  if (timelineDiv) timelineDiv.style.display = 'none';

  if (view === 'chat') {{
    chatDiv.style.display = '';
  }} else if (view === 'api') {{
    apiDiv.style.display = '';
    if (!apiViewLoaded && currentFile) {{
      apiDiv.innerHTML = '<div class="loading">載入 API View...</div>';
      const res = await fetch('/api/apiview?file=' + encodeURIComponent(currentFile));
      apiDiv.innerHTML = await res.text();
      apiViewLoaded = true;
    }}
  }} else if (view === 'stats') {{
    if (statsDiv) {{
      statsDiv.style.display = '';
      if (!apiStatsLoaded && currentFile) {{
        statsDiv.innerHTML = '<div class="loading">載入 Stats...</div>';
        const res = await fetch('/api/stats?file=' + encodeURIComponent(currentFile));
        const data = await res.json();
        statsDiv.innerHTML = renderStats(data);
        apiStatsLoaded = true;
      }}
    }}
  }} else if (view === 'timeline') {{
    if (timelineDiv) {{
      timelineDiv.style.display = '';
      if (!apiTimelineLoaded && currentFile) {{
        timelineDiv.innerHTML = '<div class="loading">載入 Timeline...</div>';
        const res = await fetch('/api/timeline?file=' + encodeURIComponent(currentFile));
        const data = await res.json();
        timelineDiv.innerHTML = renderTimeline(data);
        initTimelineDrag();
        apiTimelineLoaded = true;
      }}
    }}
  }}
}}

// ─── Stats Rendering ───
function fmtDuration(sec) {{
  if (sec < 60) return sec + 's';
  if (sec < 3600) return Math.floor(sec/60) + 'm ' + (sec%60) + 's';
  return Math.floor(sec/3600) + 'h ' + Math.floor((sec%3600)/60) + 'm';
}}

function renderStats(data) {{
  if (data.error) return '<div class="loading">Error: ' + escHtml(data.error) + '</div>';

  const totalTokens = data.total_input + data.total_output + data.total_cache_read + data.total_cache_create;

  // Stat cards
  let html = '<div class="stats-cards">';
  html += '<div class="stat-card cost"><div class="stat-value">$' + data.total_cost.toFixed(4) + '</div><div class="stat-label">Estimated Cost</div></div>';
  html += '<div class="stat-card tokens"><div class="stat-value">' + totalTokens.toLocaleString() + '</div><div class="stat-label">Total Tokens</div></div>';
  html += '<div class="stat-card"><div class="stat-value">' + data.api_call_count + '</div><div class="stat-label">API Calls</div></div>';
  html += '<div class="stat-card duration"><div class="stat-value">' + fmtDuration(data.duration_sec) + '</div><div class="stat-label">Duration</div></div>';
  html += '</div>';

  // Message counts info
  html += '<div style="color:#8b949e;font-size:0.82em;margin-bottom:16px">Messages: ' + data.user_msg_count + ' user, ' + data.assistant_msg_count + ' assistant, ' + data.tool_msg_count + ' tool results</div>';

  html += '<div class="stats-charts">';

  // Tool usage bar chart
  const tools = Object.entries(data.tool_usage).sort((a,b) => b[1] - a[1]);
  if (tools.length > 0) {{
    const maxCount = tools[0][1];
    const barH = 24;
    const gap = 6;
    const svgH = tools.length * (barH + gap) + 10;
    const labelW = 100;
    const chartW = 500;
    let svg = '<svg viewBox="0 0 ' + (labelW + chartW + 60) + ' ' + svgH + '" xmlns="http://www.w3.org/2000/svg">';
    const colors = ['#58a6ff','#3fb950','#d29922','#a371f7','#f85149','#79c0ff','#7ee787','#e3b341','#d2a8ff','#ff7b72'];
    tools.forEach((t, i) => {{
      const y = i * (barH + gap) + 5;
      const w = maxCount > 0 ? (t[1] / maxCount) * chartW : 0;
      const c = colors[i % colors.length];
      svg += '<text x="' + (labelW - 4) + '" y="' + (y + barH/2 + 4) + '" fill="#8b949e" font-size="12" text-anchor="end" font-family="monospace">' + escHtml(t[0]) + '</text>';
      svg += '<rect x="' + labelW + '" y="' + y + '" width="' + w + '" height="' + barH + '" rx="4" fill="' + c + '" opacity="0.7"/>';
      svg += '<text x="' + (labelW + w + 6) + '" y="' + (y + barH/2 + 4) + '" fill="#c9d1d9" font-size="12" font-family="monospace">' + t[1] + '</text>';
    }});
    svg += '</svg>';
    html += '<div class="stats-chart-box"><h3>Tool Usage Distribution</h3>' + svg + '</div>';
  }}

  // Token type donut chart
  const tokenParts = [
    {{label: 'Input', val: data.total_input, color: '#58a6ff'}},
    {{label: 'Output', val: data.total_output, color: '#3fb950'}},
    {{label: 'Cache Read', val: data.total_cache_read, color: '#d29922'}},
    {{label: 'Cache Create', val: data.total_cache_create, color: '#a371f7'}},
  ].filter(p => p.val > 0);
  if (tokenParts.length > 0) {{
    const total = tokenParts.reduce((s,p) => s + p.val, 0);
    let donut = '<svg viewBox="0 0 300 220" xmlns="http://www.w3.org/2000/svg">';
    const cx = 110, cy = 110, r = 80, ir = 50;
    let angle = -Math.PI / 2;
    tokenParts.forEach(p => {{
      const frac = p.val / total;
      const a1 = angle;
      const a2 = angle + frac * 2 * Math.PI;
      const large = frac > 0.5 ? 1 : 0;
      const x1o = cx + r * Math.cos(a1), y1o = cy + r * Math.sin(a1);
      const x2o = cx + r * Math.cos(a2), y2o = cy + r * Math.sin(a2);
      const x1i = cx + ir * Math.cos(a2), y1i = cy + ir * Math.sin(a2);
      const x2i = cx + ir * Math.cos(a1), y2i = cy + ir * Math.sin(a1);
      donut += '<path d="M' + x1o + ',' + y1o + ' A' + r + ',' + r + ' 0 ' + large + ' 1 ' + x2o + ',' + y2o + ' L' + x1i + ',' + y1i + ' A' + ir + ',' + ir + ' 0 ' + large + ' 0 ' + x2i + ',' + y2i + ' Z" fill="' + p.color + '" opacity="0.8"/>';
      angle = a2;
    }});
    // Legend
    tokenParts.forEach((p, i) => {{
      const ly = 20 + i * 22;
      donut += '<rect x="220" y="' + ly + '" width="12" height="12" rx="2" fill="' + p.color + '" opacity="0.8"/>';
      donut += '<text x="238" y="' + (ly + 10) + '" fill="#8b949e" font-size="11" font-family="sans-serif">' + p.label + ': ' + p.val.toLocaleString() + '</text>';
    }});
    // Center text
    donut += '<text x="' + cx + '" y="' + (cy - 4) + '" fill="#c9d1d9" font-size="14" text-anchor="middle" font-weight="700">' + totalTokens.toLocaleString() + '</text>';
    donut += '<text x="' + cx + '" y="' + (cy + 14) + '" fill="#8b949e" font-size="10" text-anchor="middle">total</text>';
    donut += '</svg>';
    html += '<div class="stats-chart-box"><h3>Token Type Breakdown</h3>' + donut + '</div>';
  }}

  html += '</div>';

  // Cumulative cost line chart
  if (data.per_call_cost.length > 1) {{
    let cumCost = [];
    let running = 0;
    data.per_call_cost.forEach(c => {{ running += c; cumCost.push(running); }});
    const maxCost = cumCost[cumCost.length - 1] || 1;
    const chartW = 700, chartH = 200, padL = 60, padR = 20, padT = 10, padB = 30;
    const plotW = chartW - padL - padR, plotH = chartH - padT - padB;
    let points = cumCost.map((c, i) => {{
      const x = padL + (i / (cumCost.length - 1)) * plotW;
      const y = padT + plotH - (c / maxCost) * plotH;
      return x + ',' + y;
    }});
    let lineChart = '<svg viewBox="0 0 ' + chartW + ' ' + chartH + '" xmlns="http://www.w3.org/2000/svg">';
    // Grid lines
    for (let i = 0; i <= 4; i++) {{
      const y = padT + (i/4) * plotH;
      const val = ((4-i)/4 * maxCost).toFixed(4);
      lineChart += '<line x1="' + padL + '" y1="' + y + '" x2="' + (chartW - padR) + '" y2="' + y + '" stroke="#21262d" stroke-width="1"/>';
      lineChart += '<text x="' + (padL - 6) + '" y="' + (y + 4) + '" fill="#484f58" font-size="9" text-anchor="end" font-family="monospace">$' + val + '</text>';
    }}
    // X axis labels
    lineChart += '<text x="' + padL + '" y="' + (chartH - 4) + '" fill="#484f58" font-size="9" text-anchor="start">1</text>';
    lineChart += '<text x="' + (chartW - padR) + '" y="' + (chartH - 4) + '" fill="#484f58" font-size="9" text-anchor="end">' + cumCost.length + '</text>';
    lineChart += '<text x="' + (padL + plotW/2) + '" y="' + (chartH - 4) + '" fill="#484f58" font-size="9" text-anchor="middle">API Calls</text>';
    // Area fill
    lineChart += '<polygon points="' + padL + ',' + (padT + plotH) + ' ' + points.join(' ') + ' ' + (chartW - padR) + ',' + (padT + plotH) + '" fill="#58a6ff" opacity="0.1"/>';
    // Line
    lineChart += '<polyline points="' + points.join(' ') + '" fill="none" stroke="#58a6ff" stroke-width="2"/>';
    // End dot
    if (points.length > 0) {{
      const last = points[points.length - 1].split(',');
      lineChart += '<circle cx="' + last[0] + '" cy="' + last[1] + '" r="3" fill="#58a6ff"/>';
    }}
    lineChart += '</svg>';
    html += '<div class="stats-chart-box full-width" style="margin-top:0"><h3>Cumulative Cost Over API Calls</h3>' + lineChart + '</div>';
  }}

  return html;
}}

// ─── Secrets Detection ───
async function checkSecrets() {{
  if (!currentFile) return;
  try {{
    const res = await fetch('/api/secrets?file=' + encodeURIComponent(currentFile));
    const secrets = await res.json();
    const banner = document.getElementById('secret-banner');
    const badgeArea = document.getElementById('secret-badge-area');
    if (!banner || !badgeArea) return;
    if (secrets.length > 0) {{
      banner.classList.add('visible');
      let inner = '<strong>&#x26A0; ' + secrets.length + ' potential secret(s) detected!</strong>';
      inner += '<div class="secret-list">';
      secrets.forEach(s => {{
        inner += '<div><span class="secret-type">' + escHtml(s.type) + '</span> <span class="secret-masked">' + escHtml(s.masked) + '</span> <span style="color:#484f58">(line ' + s.line + ')</span></div>';
      }});
      inner += '</div>';
      banner.innerHTML = inner;
      badgeArea.innerHTML = '<span class="secret-badge">' + secrets.length + ' secret' + (secrets.length > 1 ? 's' : '') + '</span>';
    }} else {{
      banner.classList.remove('visible');
      banner.innerHTML = '';
      badgeArea.innerHTML = '';
    }}
  }} catch(e) {{
    // ignore
  }}
}}

// ─── Export ───
function toggleExportMenu() {{
  const menu = document.getElementById('export-menu');
  if (menu) menu.classList.toggle('open');
}}

function doExport(fmt) {{
  if (!currentFile) return;
  const url = '/api/export?file=' + encodeURIComponent(currentFile) + '&fmt=' + fmt;
  window.open(url, '_blank');
  const menu = document.getElementById('export-menu');
  if (menu) menu.classList.remove('open');
}}

// Close export menu on outside click
document.addEventListener('click', e => {{
  const dd = document.querySelector('.export-dropdown');
  const menu = document.getElementById('export-menu');
  if (menu && dd && !dd.contains(e.target)) {{
    menu.classList.remove('open');
  }}
}});

// ─── Navigation ───
let sessionLoaded = false;

function goTop() {{ window.scrollTo({{top: 0, behavior: 'smooth'}}); }}
function goBottom() {{ window.scrollTo({{top: document.body.scrollHeight, behavior: 'smooth'}}); }}

function getVisibleEntries(selector) {{
  return [...document.querySelectorAll(selector)].filter(el => {{
    return !el.classList.contains('filtered-out') && !el.classList.contains('hidden-default') && el.offsetParent !== null;
  }});
}}

function jumpToEntry(el) {{
  if (!el) return;
  el.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
  el.classList.add('highlight-jump');
  setTimeout(() => el.classList.remove('highlight-jump'), 1200);
}}

function getCurrentEntryIndex(entries) {{
  const scrollY = window.scrollY + window.innerHeight / 3;
  for (let i = entries.length - 1; i >= 0; i--) {{
    if (entries[i].offsetTop <= scrollY) return i;
  }}
  return -1;
}}

function jumpNext() {{
  const entries = getVisibleEntries('#entries > .entry[data-type="user"]');
  const idx = getCurrentEntryIndex(entries);
  if (idx < entries.length - 1) jumpToEntry(entries[idx + 1]);
}}
function jumpPrev() {{
  const entries = getVisibleEntries('#entries > .entry[data-type="user"]');
  const idx = getCurrentEntryIndex(entries);
  if (idx > 0) jumpToEntry(entries[idx - 1]);
  else if (entries.length) jumpToEntry(entries[0]);
}}
function jumpNextCompact() {{
  const entries = getVisibleEntries('#entries > .entry.compact');
  const idx = getCurrentEntryIndex(entries);
  if (idx < entries.length - 1) jumpToEntry(entries[idx + 1]);
  else if (entries.length && idx === -1) jumpToEntry(entries[0]);
}}
function jumpPrevCompact() {{
  const entries = getVisibleEntries('#entries > .entry.compact');
  const idx = getCurrentEntryIndex(entries);
  if (idx > 0) jumpToEntry(entries[idx - 1]);
  else if (entries.length) jumpToEntry(entries[0]);
}}

// Keyboard shortcuts
document.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (!sessionLoaded) return;
  switch(e.key.toLowerCase()) {{
    case 't': goTop(); e.preventDefault(); break;
    case 'b': goBottom(); e.preventDefault(); break;
    case 'j': jumpNext(); e.preventDefault(); break;
    case 'k': jumpPrev(); e.preventDefault(); break;
    case 'n': jumpNextCompact(); e.preventDefault(); break;
    case 'p': jumpPrevCompact(); e.preventDefault(); break;
    case 'f': if (!e.metaKey && !e.ctrlKey) {{ document.getElementById('search')?.focus(); e.preventDefault(); }} break;
  }}
}});

// Scroll tracking
let scrollTick = false;
window.addEventListener('scroll', () => {{
  if (!scrollTick) {{
    requestAnimationFrame(() => {{
      updateScrollUI();
      scrollTick = false;
    }});
    scrollTick = true;
  }}
}});

function updateScrollUI() {{
  const scrollTop = window.scrollY;
  const docHeight = document.body.scrollHeight - window.innerHeight;
  const pct = docHeight > 0 ? (scrollTop / docHeight) * 100 : 0;

  // Progress bar
  const bar = document.getElementById('scroll-bar');
  if (bar) bar.style.width = pct + '%';

  // Show/hide toolbar
  const toolbar = document.getElementById('nav-toolbar');
  if (toolbar) toolbar.classList.toggle('visible', sessionLoaded && scrollTop > 200);

  // Minimap viewport
  const vp = document.getElementById('minimap-vp');
  const mm = document.getElementById('minimap');
  if (vp && mm && sessionLoaded) {{
    const mmH = mm.offsetHeight;
    const vpH = Math.max(20, (window.innerHeight / document.body.scrollHeight) * mmH);
    const vpTop = (pct / 100) * (mmH - vpH);
    vp.style.top = vpTop + 'px';
    vp.style.height = vpH + 'px';
  }}

  // Position indicator
  if (sessionLoaded) {{
    const entries = document.querySelectorAll('#entries > .entry:not(.filtered-out)');
    const total = entries.length;
    if (total > 0) {{
      const scrollY = window.scrollY + window.innerHeight / 3;
      let current = 0;
      for (let i = entries.length - 1; i >= 0; i--) {{
        if (entries[i].offsetTop <= scrollY) {{ current = i + 1; break; }}
      }}
      const ind = document.getElementById('pos-indicator');
      if (ind) {{
        ind.textContent = current + ' / ' + total;
        ind.classList.add('visible');
      }}
    }}
  }}
}}

// Build minimap dots
function buildMinimap() {{
  const mm = document.getElementById('minimap');
  if (!mm) return;
  // Remove old dots
  mm.querySelectorAll('.minimap-dot').forEach(d => d.remove());

  const entries = document.querySelectorAll('#entries > .entry');
  const total = entries.length;
  if (total === 0) {{ mm.classList.remove('visible'); return; }}
  mm.classList.add('visible');

  const mmH = mm.offsetHeight;
  entries.forEach((el, i) => {{
    const dot = document.createElement('div');
    dot.className = 'minimap-dot';
    const y = (i / total) * mmH;
    dot.style.top = y + 'px';
    const t = el.dataset.type;
    const isCompact = el.classList.contains('compact');
    if (isCompact) dot.classList.add('mm-compact');
    else if (t === 'user') dot.classList.add('mm-user');
    else if (t === 'assistant') dot.classList.add('mm-assistant');
    else if (t === 'tool') dot.classList.add('mm-tool');
    else {{ dot.style.background = '#30363d'; }}
    mm.appendChild(dot);
  }});
}}

// Minimap click to scroll
document.getElementById('minimap')?.addEventListener('click', e => {{
  const mm = document.getElementById('minimap');
  const rect = mm.getBoundingClientRect();
  const pct = (e.clientY - rect.top) / rect.height;
  window.scrollTo({{ top: pct * (document.body.scrollHeight - window.innerHeight), behavior: 'smooth' }});
}});

// Patch loadSession to set flag and build minimap
const _origLoadSession = loadSession;
loadSession = async function(filepath, el) {{
  await _origLoadSession(filepath, el);
  sessionLoaded = true;
  apiViewLoaded = false;
  apiStatsLoaded = false;
  apiTimelineLoaded = false;
  setTimeout(buildMinimap, 100);
  updateScrollUI();
}};

// ─── Global Search ───
function handleSearchKey(e) {{
  const input = e.target;
  const val = input.value.trim();
  if (e.key === 'Enter' && val.length > 0) {{
    e.preventDefault();
    const query = val.startsWith('/') ? val.slice(1).trim() : val;
    if (query.length > 0) {{
      doGlobalSearch(query);
    }}
    return;
  }}
  // Normal folder filtering on other keys
}}

async function doGlobalSearch(query) {{
  const viewer = document.getElementById('viewer');
  viewer.innerHTML = '<div class="loading">搜尋中...</div>';
  sessionLoaded = false;

  try {{
    const res = await fetch('/api/search?q=' + encodeURIComponent(query));
    const results = await res.json();

    if (results.error) {{
      viewer.innerHTML = '<div class="loading">Error: ' + escHtml(results.error) + '</div>';
      return;
    }}

    let html = '<div class="search-results">';
    html += '<h2>Search: "' + escHtml(query) + '"</h2>';
    html += '<div class="sr-meta">' + results.length + ' result' + (results.length !== 1 ? 's' : '') + (results.length >= 50 ? ' (limit reached)' : '') + '</div>';

    if (results.length === 0) {{
      html += '<div style="color:#484f58;text-align:center;margin-top:40px">No results found.</div>';
    }} else {{
      results.forEach(r => {{
        const typeClass = r.record_type === 'user' ? 'sr-type-user' : 'sr-type-assistant';
        const preview = escHtml(r.preview).replace(
          new RegExp('(' + escRegex(escHtml(r.match_text)) + ')', 'gi'),
          '<mark>$1</mark>'
        );
        html += '<div class="search-result" onclick="loadSearchResult(\'' + escAttr(r.path) + '\')">';
        html += '<div class="sr-header">';
        html += '<span class="sr-type ' + typeClass + '">' + escHtml(r.record_type) + '</span>';
        html += '<span class="sr-folder">' + escHtml(r.folder.replace(/-/g, '/')) + '</span>';
        html += '<span class="sr-time">' + escHtml(r.timestamp) + '</span>';
        html += '</div>';
        html += '<div class="sr-preview">' + preview + '</div>';
        html += '<div class="sr-session">' + escHtml(r.session_id) + '</div>';
        html += '</div>';
      }});
    }}
    html += '</div>';
    viewer.innerHTML = html;
  }} catch(err) {{
    viewer.innerHTML = '<div class="loading">Search error: ' + escHtml(err.message) + '</div>';
  }}
}}

function escRegex(s) {{
  return s.replace(/[.*+?^${{}}()|[\\]\\\\]/g, '\\\\$&');
}}

function escAttr(s) {{
  return s.replace(/\\\\/g, '\\\\\\\\').replace(/'/g, "\\\\'");
}}

function loadSearchResult(path) {{
  // Find the session item in sidebar if visible, otherwise just load directly
  const items = document.querySelectorAll('.session-item');
  let found = null;
  items.forEach(item => {{
    if (item.dataset.path === path) found = item;
  }});
  loadSession(path, found);
}}

// ─── Live Watch ───
let watchActive = false;
let watchTimer = null;
let lastPollTime = Date.now() / 1000;
let knownMtimes = {{}};

function toggleWatch() {{
  watchActive = !watchActive;
  const btn = document.getElementById('watch-toggle');
  btn.classList.toggle('active', watchActive);
  if (watchActive) {{
    lastPollTime = Date.now() / 1000;
    pollNow();
    watchTimer = setInterval(pollNow, 5000);
  }} else {{
    if (watchTimer) clearInterval(watchTimer);
    watchTimer = null;
    // Clear all badges
    document.querySelectorAll('.session-badge').forEach(b => b.remove());
    const banner = document.getElementById('reload-banner');
    if (banner) banner.style.display = 'none';
  }}
}}

async function pollNow() {{
  if (!watchActive) return;
  try {{
    const res = await fetch('/api/poll?since=' + lastPollTime);
    const changed = await res.json();
    if (changed.error || !Array.isArray(changed) || changed.length === 0) return;

    changed.forEach(c => {{
      const key = c.folder + '/' + c.filename;
      const isNew = !knownMtimes[key];
      knownMtimes[key] = c.mtime;

      // Add badge to session item if visible
      const items = document.querySelectorAll('.session-item');
      items.forEach(item => {{
        if (item.dataset.path === c.path) {{
          // Remove existing badge first
          const old = item.querySelector('.session-badge');
          if (old) old.remove();
          const badge = document.createElement('span');
          badge.className = 'session-badge ' + (isNew ? 'session-badge-new' : 'session-badge-upd');
          badge.textContent = isNew ? 'NEW' : 'UPD';
          const preview = item.querySelector('.session-preview');
          if (preview) preview.appendChild(badge);
        }}
      }});

      // If currently viewed session was modified, show reload banner
      if (currentFile === c.path) {{
        const banner = document.getElementById('reload-banner');
        if (banner) banner.style.display = 'block';
      }}
    }});

    lastPollTime = Date.now() / 1000;
  }} catch(e) {{
    // ignore poll errors
  }}
}}

function reloadCurrentSession() {{
  const banner = document.getElementById('reload-banner');
  if (banner) banner.style.display = 'none';
  if (currentFile) {{
    const active = document.querySelector('.session-item.active');
    loadSession(currentFile, active);
  }}
}}

// ─── Keyboard shortcut: / to focus search ───
const _origKeyHandler = document.onkeydown;
document.addEventListener('keydown', e => {{
  if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
  if (e.key === '/') {{
    e.preventDefault();
    const input = document.getElementById('folder-search');
    if (input) {{
      input.focus();
      input.select();
    }}
  }}
}});

// Initialize known mtimes on load
async function initKnownMtimes() {{
  try {{
    const res = await fetch('/api/poll?since=0');
    const all = await res.json();
    if (Array.isArray(all)) {{
      all.forEach(c => {{
        knownMtimes[c.folder + '/' + c.filename] = c.mtime;
      }});
    }}
  }} catch(e) {{}}
}}
initKnownMtimes();

// ─── Timeline View ───
let tlScale = 1.0; // pixels per second

function renderTimeline(data) {{
  if (data.error) return '<div class="loading">Error: ' + escHtml(data.error) + '</div>';
  if (!data.length) return '<div class="loading">No timeline entries</div>';

  // Parse timestamps
  const entries = data.map(e => ({{
    ...e,
    dt: new Date(e.ts.replace('Z', '+00:00')),
  }}));
  const t0 = entries[0].dt.getTime();
  const tN = entries[entries.length - 1].dt.getTime();
  const durationSec = Math.max((tN - t0) / 1000, 1);

  // Default scale: try to fit ~1200px for the whole session, min 0.5 px/sec
  tlScale = Math.max(1200 / durationSec, 0.5);

  let html = '<div class="timeline-controls">';
  html += '<button onclick="tlZoom(1.5)">+ Zoom In</button>';
  html += '<button onclick="tlZoom(0.67)">- Zoom Out</button>';
  html += '<button onclick="tlFit()">Fit</button>';
  html += '<span style="color:#484f58;font-size:0.78em;margin-left:10px" id="tl-info">' + entries.length + ' events, ' + fmtDuration(Math.round(durationSec)) + '</span>';
  html += '<div class="tl-legend"><span class="lg-user">user</span><span class="lg-assistant">assistant</span><span class="lg-tool">tool</span><span class="lg-thinking">thinking</span><span class="lg-system">system</span></div>';
  html += '</div>';

  html += '<div class="timeline-container" id="tl-container" data-t0="' + t0 + '" data-tn="' + tN + '" data-duration="' + durationSec + '">';
  html += '<div class="timeline-inner" id="tl-inner">';
  html += '<div class="timeline-cursor" id="tl-cursor" style="display:none"><div class="timeline-cursor-label" id="tl-cursor-label"></div></div>';
  html += '</div>';
  html += '<div class="timeline-axis" id="tl-axis"></div>';
  html += '</div>';

  // We build cards and axis ticks via JS after render for dynamic sizing
  setTimeout(() => buildTimelineCards(entries), 0);
  return html;
}}

function buildTimelineCards(entries) {{
  const container = document.getElementById('tl-container');
  const inner = document.getElementById('tl-inner');
  const axis = document.getElementById('tl-axis');
  if (!container || !inner || !axis) return;

  const t0 = parseFloat(container.dataset.t0);
  const tN = parseFloat(container.dataset.tn);
  const durationSec = parseFloat(container.dataset.duration);
  const totalW = Math.max(durationSec * tlScale + 100, container.clientWidth);
  inner.style.width = totalW + 'px';
  axis.style.width = totalW + 'px';

  // Clear old cards
  inner.querySelectorAll('.timeline-card').forEach(c => c.remove());
  axis.querySelectorAll('.timeline-tick').forEach(t => t.remove());

  // Collision tracking: columns of occupied y-positions
  const CARD_H = 32;
  const CARD_W = 80;
  const GAP = 4;
  const columns = []; // array of {{x, y, xEnd}}

  const typeClass = {{ user: 'tc-user', assistant: 'tc-assistant', tool: 'tc-tool', thinking: 'tc-thinking', system: 'tc-system', progress: 'tc-progress' }};

  entries.forEach(e => {{
    const dt = new Date(e.ts.replace('Z', '+00:00'));
    const sec = (dt.getTime() - t0) / 1000;
    const x = Math.round(sec * tlScale + 20);

    // Find vertical slot (stack if overlap)
    let row = 0;
    for (let r = 0; r < 50; r++) {{
      let overlap = false;
      for (const col of columns) {{
        if (x < col.xEnd && (x + CARD_W) > col.x && col.row === r) {{
          overlap = true;
          break;
        }}
      }}
      if (!overlap) {{ row = r; break; }}
    }}
    columns.push({{ x, xEnd: x + CARD_W + GAP, row }});

    const y = 10 + row * (CARD_H + GAP);
    const cls = typeClass[e.type] || 'tc-progress';
    const card = document.createElement('div');
    card.className = 'timeline-card ' + cls;
    card.style.left = x + 'px';
    card.style.top = y + 'px';
    card.dataset.entryId = e.id;
    card.title = e.summary + '\\n' + dt.toLocaleTimeString();
    card.textContent = e.summary || e.type;
    card.onclick = () => {{
      // Switch to chat view and scroll to that entry
      const chatBtn = document.querySelector('.view-toggle button:first-child');
      if (chatBtn) switchView('chat', chatBtn);
      setTimeout(() => {{
        const target = document.getElementById('e' + e.id);
        if (target) blinkTarget(target);
      }}, 200);
    }};
    inner.appendChild(card);
  }});

  // Adjust inner height
  const maxRow = columns.reduce((m, c) => Math.max(m, c.row), 0);
  inner.style.minHeight = (10 + (maxRow + 1) * (CARD_H + GAP) + 40) + 'px';

  // Build axis ticks
  const tickInterval = getTickInterval(durationSec);
  const startDate = new Date(t0);
  // Round to next tick
  const startSec = Math.ceil((startDate.getTime() / 1000) / tickInterval) * tickInterval;
  for (let ts = startSec; ts <= tN / 1000; ts += tickInterval) {{
    const sec = ts - t0 / 1000;
    const x = Math.round(sec * tlScale + 20);
    const dt = new Date(ts * 1000);
    const tick = document.createElement('div');
    tick.className = 'timeline-tick';
    tick.style.left = x + 'px';
    tick.textContent = dt.toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit' }});
    axis.appendChild(tick);
  }}

  // Mouse cursor line
  container.onmousemove = (ev) => {{
    const rect = container.getBoundingClientRect();
    const scrollLeft = container.scrollLeft;
    const mx = ev.clientX - rect.left + scrollLeft;
    const cursor = document.getElementById('tl-cursor');
    const label = document.getElementById('tl-cursor-label');
    if (cursor) {{
      cursor.style.display = '';
      cursor.style.left = mx + 'px';
    }}
    if (label) {{
      const sec = (mx - 20) / tlScale;
      const dt = new Date(t0 + sec * 1000);
      label.textContent = dt.toLocaleTimeString([], {{ hour: '2-digit', minute: '2-digit', second: '2-digit' }});
    }}
  }};
  container.onmouseleave = () => {{
    const cursor = document.getElementById('tl-cursor');
    if (cursor) cursor.style.display = 'none';
  }};
}}

function getTickInterval(durationSec) {{
  // Choose a nice tick interval in seconds
  if (durationSec < 60) return 10;
  if (durationSec < 300) return 30;
  if (durationSec < 600) return 60;
  if (durationSec < 1800) return 300;
  if (durationSec < 7200) return 600;
  if (durationSec < 14400) return 1800;
  return 3600;
}}

let _tlEntries = null;
function tlZoom(factor) {{
  tlScale *= factor;
  tlScale = Math.max(0.05, Math.min(tlScale, 50));
  // Re-render with cached data
  const container = document.getElementById('tl-container');
  if (!container) return;
  const timelineDiv = document.getElementById('timeline-view');
  if (timelineDiv && currentFile) {{
    // Re-fetch and rebuild
    fetch('/api/timeline?file=' + encodeURIComponent(currentFile))
      .then(r => r.json())
      .then(data => {{
        const entries = data.map(e => ({{ ...e, dt: new Date(e.ts.replace('Z', '+00:00')) }}));
        buildTimelineCards(entries);
      }});
  }}
}}

function tlFit() {{
  const container = document.getElementById('tl-container');
  if (!container) return;
  const durationSec = parseFloat(container.dataset.duration);
  tlScale = Math.max((container.clientWidth - 60) / durationSec, 0.05);
  tlZoom(1); // rebuild
}}

function initTimelineDrag() {{
  const container = document.getElementById('tl-container');
  if (!container) return;
  let isDragging = false;
  let startX = 0;
  let scrollStart = 0;

  container.addEventListener('mousedown', (e) => {{
    if (e.target.classList.contains('timeline-card')) return;
    isDragging = true;
    startX = e.clientX;
    scrollStart = container.scrollLeft;
    container.classList.add('dragging');
    e.preventDefault();
  }});
  document.addEventListener('mousemove', (e) => {{
    if (!isDragging) return;
    container.scrollLeft = scrollStart - (e.clientX - startX);
  }});
  document.addEventListener('mouseup', () => {{
    isDragging = false;
    container.classList.remove('dragging');
  }});
}}

// ─── JSONL Upload / Drag & Drop ───
function setupDropZone() {{
  // Welcome page drop zone
  const welcomeZone = document.getElementById('welcome-drop-zone');
  if (welcomeZone) {{
    welcomeZone.addEventListener('dragover', (e) => {{
      e.preventDefault();
      welcomeZone.classList.add('drag-over');
    }});
    welcomeZone.addEventListener('dragleave', () => {{
      welcomeZone.classList.remove('drag-over');
    }});
    welcomeZone.addEventListener('drop', (e) => {{
      e.preventDefault();
      welcomeZone.classList.remove('drag-over');
      handleFileDrop(e.dataTransfer.files);
    }});
  }}

  // Global overlay for sidebar / anywhere
  const overlay = document.getElementById('drop-overlay');
  let dragCount = 0;

  document.addEventListener('dragenter', (e) => {{
    e.preventDefault();
    dragCount++;
    if (overlay) overlay.classList.add('visible');
  }});
  document.addEventListener('dragleave', (e) => {{
    e.preventDefault();
    dragCount--;
    if (dragCount <= 0) {{
      dragCount = 0;
      if (overlay) overlay.classList.remove('visible');
    }}
  }});
  document.addEventListener('dragover', (e) => {{
    e.preventDefault();
  }});
  document.addEventListener('drop', (e) => {{
    e.preventDefault();
    dragCount = 0;
    if (overlay) overlay.classList.remove('visible');
    // Only handle if dropped on overlay or welcome zone (not on other interactive elements)
    if (e.target === overlay || overlay.contains(e.target) ||
        e.target.closest('.sidebar') || e.target.closest('.drop-zone')) {{
      handleFileDrop(e.dataTransfer.files);
    }}
  }});
}}

async function handleFileDrop(files) {{
  if (!files || files.length === 0) return;
  const file = files[0];
  if (!file.name.endsWith('.jsonl')) {{
    alert('Please drop a .jsonl file');
    return;
  }}

  const viewer = document.getElementById('viewer');
  if (viewer) viewer.innerHTML = '<div class="loading">Uploading...</div>';

  try {{
    const text = await file.text();
    const res = await fetch('/api/upload', {{
      method: 'POST',
      headers: {{ 'Content-Type': 'text/plain' }},
      body: text,
    }});
    const data = await res.json();
    if (data.ok) {{
      loadSession(data.path, null);
    }} else {{
      if (viewer) viewer.innerHTML = '<div class="loading">Upload error: ' + escHtml(data.error || 'unknown') + '</div>';
    }}
  }} catch (err) {{
    if (viewer) viewer.innerHTML = '<div class="loading">Upload error: ' + escHtml(err.message) + '</div>';
  }}
}}

setTimeout(setupDropZone, 100);

loadFolders();
</script>
</body>
</html>"""


def main():
    server = http.server.HTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Session Viewer running at http://127.0.0.1:{PORT}")
    threading.Timer(0.5, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
