# Claude Session Manager

A small PyQt5 desktop tool to **browse, preview, and delete** Claude Code CLI
session files stored under `~/.claude/projects/`.

Claude Code's built-in session UI only lets you browse sessions — you can't
delete them or jump to their folders. This tool fills that gap.

## Features

- **Left panel** — sessions grouped by project. Each row shows the session
  **title**, the **filename** (`<uuid>.jsonl`), and the real working directory.
- **Right panel** — a readable preview of the conversation (user / assistant
  messages, with thinking and tool calls shown compactly).
- **Search box** — filter by title, filename, or path.
- **Right-click / buttons**:
  - **Open project folder** — the real `cwd` where the session ran.
  - **Open storage folder** — the `~/.claude/projects/<encoded>` folder that
    holds the `.jsonl` files.
  - **Delete** — moves the session file to the system Recycle Bin (recoverable)
    and refreshes the list.

## Requirements

- Python >= 3.9
- [uv](https://docs.astral.sh/uv/)

## Setup & Run

```bash
uv sync
uv run claude-session-manager
```

or

```bash
uv run python -m claude_session_manager
```

## Layout

```
claude_session_manager/
  __init__.py
  __main__.py        # entry point
  models.py          # SessionMeta dataclass
  scanner.py         # scan ~/.claude/projects, parse .jsonl metadata
  preview.py         # render a session's conversation to HTML
  ui/
    __init__.py
    main_window.py   # the PyQt5 window
```
