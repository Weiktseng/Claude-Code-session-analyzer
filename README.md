# Claude Code Session Analyzer

A local web-based viewer for browsing and analyzing [Claude Code](https://docs.anthropic.com/en/docs/claude-code) session logs (`.jsonl` files stored in `~/.claude/projects/`).

**Zero dependencies** — single Python file, uses only stdlib. No Node.js, no Electron, no Rust toolchain.

![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)
![No Dependencies](https://img.shields.io/badge/dependencies-none-green)
![License: MIT](https://img.shields.io/badge/license-MIT-yellow)

## Quick Start

```bash
python3 claude_session_viewer.py
```

Opens `http://127.0.0.1:18923` in your browser. Select a project folder and session from the sidebar. You can also **drag & drop** any `.jsonl` file onto the page.

## Features

### 4 View Modes

| View | What it shows |
|------|---------------|
| **Chat View** | Color-coded conversation with filters, fold/unfold, compact detection |
| **API View** | Reconstructed API call boundaries — request/response pairs with token bars |
| **Stats** | Token totals, cost trends (SVG line chart), tool usage (bar chart), token breakdown (donut chart) |
| **Timeline** | Horizontal interactive timeline — drag to pan, zoom in/out, click to jump |

### API Call Analysis (unique to this tool)
- Reconstructs actual API call boundaries by grouping `message.id`
- Visualizes **server-side tool loops**: each turn within a streaming connection shown separately
- Token usage breakdown per call: input, output, cache read, cache create
- Visual token bar + estimated cost (based on [Anthropic public pricing](https://www.anthropic.com/pricing))

### Tool ID Linking (unique to this tool)
- Click any `tool_use` → jumps to its matching `tool_result` (double-blink highlight)
- Click any `tool_result` → jumps back to its `tool_use`
- Works across both Chat View and API View

### Compact Content Detection
- Automatically identifies compressed/summarized content with colored badges:
  - **Curator** compression records (purple)
  - **Context continuation** summaries (red)
  - **/compact** command output (green)
  - **Session summaries** (blue)
- "Only Compact" filter to isolate these entries

### Stats Dashboard
- 4 metric cards: total cost, total tokens, API call count, session duration
- **SVG bar chart**: tool usage distribution (Bash, Read, Write, etc.)
- **SVG line chart**: cumulative cost over API calls
- **SVG donut chart**: token type breakdown (input / output / cache read / cache create)
- All charts are pure SVG — no chart.js, no d3

### Timeline View
- Horizontal scrollable timeline with color-coded entry cards
- Time axis with auto-scaled tick marks
- **Drag to pan**, zoom +/- buttons, "Fit" to auto-fit
- Vertical cursor shows timestamp at mouse position
- Click any entry → switches to Chat View and scrolls to it

### Global Search
- Cross-session fuzzy search from the sidebar
- Type a query and press Enter → searches all sessions across all projects
- Results show matched text with highlighting, clickable to load that session
- Keyboard shortcut: `/` to focus search

### Credential Detection
- Automatically scans for potential secrets: OpenAI keys, Anthropic keys, GitHub PATs, AWS keys, private key headers
- Red warning banner when secrets are detected
- Masked display (first 6 + last 4 chars visible)

### Live Watch
- Toggle in sidebar — polls for new/modified sessions every 5 seconds
- Pulsing green indicator when active
- "NEW" / "UPD" badges on changed sessions
- Auto-reload banner when current session is modified

### Export
- Download current session as **Markdown** (.md) or **HTML** (.html)
- HTML export includes all inline CSS for standalone viewing
- Markdown export with proper headers, code blocks, and structure

### Navigation
- Floating toolbar: Top / Bottom / Next User / Prev User / Next Compact / Prev Compact
- Keyboard shortcuts: `T` `B` `J` `K` `N` `P` `F` `/`
- Scroll progress bar + minimap with color-coded dots
- Position indicator (current / total entries)
- Sticky filter bar

### Drag & Drop Upload
- Drop any `.jsonl` file onto the page to view it instantly
- Works on the welcome page and as a full-page overlay

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CSV_PORT` | `18923` | Server port |
| `CSV_BASE_DIR` | `~/.claude/projects` | Path to Claude Code session logs |
| `CSV_TZ_OFFSET` | `8` | Timezone offset from UTC (e.g., `8` for UTC+8, `-5` for EST) |

```bash
CSV_PORT=8080 CSV_TZ_OFFSET=-5 python3 claude_session_viewer.py
```

## macOS Desktop App (Optional)

```bash
mkdir -p ~/Desktop/"Claude Session Viewer.app"/Contents/MacOS
cat > ~/Desktop/"Claude Session Viewer.app"/Contents/MacOS/launch << 'EOF'
#!/bin/bash
exec /usr/bin/python3 /path/to/claude_session_viewer.py
EOF
chmod +x ~/Desktop/"Claude Session Viewer.app"/Contents/MacOS/launch
```

## How It Works

Claude Code stores session logs as JSONL files in `~/.claude/projects/<project-folder>/`. Each line is a JSON record with types like `user`, `assistant`, `system`, `progress`, etc.

This tool:
1. Scans the project folders to list available sessions
2. Parses the JSONL records to reconstruct the conversation
3. Groups assistant messages by `message.id` to identify API call boundaries
4. Detects compressed/compact content using regex patterns
5. Scans for potential credential leaks
6. Renders everything as an interactive web UI served locally

### Understanding API View

Each API call to Claude is identified by a unique `message.id`. Within a single API call, Claude Code may execute a **server-side tool loop**:

```
Model outputs tool_use → Claude Code executes tool → feeds result back → Model continues
(all within one HTTP streaming connection, one message.id)
```

The API View shows each turn within this loop as a separate **REQUEST → RESPONSE** pair, making it clear which tool results the model saw before making its next decision.

## Comparison with Other Tools

| Feature | This tool | claude-code-log | CCHV | claude-code-viewer | Mantra |
|---------|-----------|-----------------|------|--------------------|--------|
| Zero dependencies | **Yes** (stdlib only) | No (Python pkg) | No (Electron/Tauri) | No (Node.js) | No (Desktop) |
| API call reconstruction | **Yes** | No | No | No | No |
| Tool ID linking | **Yes** | No | No | No | No |
| Compact detection | **Yes** | No | No | No | No |
| Stats/charts | **Yes** (pure SVG) | Token counts | Yes (Electron) | No | No |
| Timeline | **Yes** | No | No | No | Yes |
| Global search | **Yes** | No | Yes | Yes | No |
| Credential detection | **Yes** | No | No | No | Yes |
| Live watch | **Yes** | No | Yes | Yes | No |
| Export (MD/HTML) | **Yes** | Yes (HTML) | No | No | No |
| Drag & drop upload | **Yes** | No | No | No | No |
| Multi-tool support | Claude Code only | Claude Code only | Claude/Codex/OpenCode | Claude Code | 4 tools |
| Start new sessions | No | No | No | Yes | No |

## License

MIT
