"""Scan and parse Claude Code session files under ~/.claude/projects/."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, List, Optional

from .models import SessionMeta


def get_projects_root() -> Path:
    """Return the directory where Claude Code stores session files."""
    return Path.home() / ".claude" / "projects"


def extract_text(content: Any) -> str:
    """Flatten a message `content` (string or list of blocks) into plain text.

    Only user-visible text is kept; thinking / tool blocks are summarised so the
    list subtitle stays readable.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                parts.append(str(block))
                continue
            btype = block.get("type")
            if btype == "text":
                parts.append(block.get("text", ""))
            elif btype == "thinking":
                parts.append(block.get("thinking", ""))
            elif btype == "tool_use":
                parts.append(f"[tool: {block.get('name', '?')}]")
            elif btype == "tool_result":
                parts.append(extract_text(block.get("content")))
        return "\n".join(p for p in parts if p)
    return str(content)


def _first_line(text: str, limit: int = 120) -> str:
    text = (text or "").strip().replace("\r", " ").replace("\n", " ")
    return text[:limit] + ("…" if len(text) > limit else "")


def parse_meta(path: Path) -> Optional[SessionMeta]:
    """Read a single `.jsonl` file and extract lightweight metadata.

    Returns ``None`` if the file can't be read at all. Malformed individual
    lines are skipped silently so a partially-written session still shows up.
    """
    try:
        stat = path.stat()
    except OSError:
        return None

    title: Optional[str] = None
    project_dir: Optional[str] = None
    first_prompt: Optional[str] = None
    last_prompt: Optional[str] = None
    created: Optional[str] = None
    count = 0

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue

                btype = obj.get("type")
                if btype == "ai-title":
                    title = obj.get("aiTitle") or title
                elif btype == "last-prompt":
                    last_prompt = obj.get("lastPrompt") or last_prompt
                elif btype in ("user", "assistant"):
                    count += 1
                    if project_dir is None and obj.get("cwd"):
                        project_dir = obj.get("cwd")
                    if created is None and obj.get("timestamp"):
                        created = obj.get("timestamp")
                    if (
                        btype == "user"
                        and first_prompt is None
                        and isinstance(obj.get("message"), dict)
                    ):
                        first_prompt = extract_text(obj["message"].get("content"))
    except OSError:
        return None

    # Title fallback chain: ai-title -> first user prompt -> last-prompt -> id.
    display_title = (
        title
        or _first_line(first_prompt or "")
        or _first_line(last_prompt or "")
        or path.stem
    )

    if project_dir:
        project_name = Path(project_dir).name or project_dir
    else:
        project_name = path.parent.name

    preview = _first_line(first_prompt or last_prompt or "", 160)

    return SessionMeta(
        session_id=path.stem,
        file_path=path,
        storage_dir=path.parent,
        title=display_title,
        project_dir=project_dir,
        project_name=project_name,
        created_at=created,
        modified_at=stat.st_mtime,
        size=stat.st_size,
        message_count=count,
        preview=preview,
    )


def scan_sessions(root: Optional[Path] = None) -> List[SessionMeta]:
    """Scan the projects root and return all sessions, newest first."""
    root = root or get_projects_root()
    sessions: List[SessionMeta] = []
    if not root.exists():
        return sessions
    for proj_dir in root.iterdir():
        if not proj_dir.is_dir():
            continue
        for f in proj_dir.glob("*.jsonl"):
            meta = parse_meta(f)
            if meta is not None:
                sessions.append(meta)
    sessions.sort(key=lambda s: s.modified_at, reverse=True)
    return sessions
