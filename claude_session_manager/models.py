"""Data models for Claude Code session files."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class SessionMeta:
    """Lightweight metadata about a single session `.jsonl` file.

    Built by :func:`claude_session_manager.scanner.parse_meta` from a quick
    pass over the file. Full conversation content is parsed lazily on demand
    by :mod:`claude_session_manager.preview`.
    """

    session_id: str
    """The session UUID (the `.jsonl` filename stem)."""

    file_path: Path
    """Absolute path to the `.jsonl` file."""

    storage_dir: Path
    """The `~/.claude/projects/<encoded>` directory holding the file."""

    title: str
    """Human-readable title (ai-title, falling back to first prompt / id)."""

    cwd: Optional[str]
    """The real working directory the session ran in, if recorded."""

    project_name: str
    """Display name for grouping (last path component of cwd, or dir name)."""

    created_at: Optional[str]
    """ISO timestamp of the first message, if recorded."""

    modified_at: float
    """File modification time (epoch seconds), used for sorting."""

    size: int
    """File size in bytes."""

    message_count: int
    """Number of user + assistant messages."""

    preview: str
    """Short one-line snippet (first prompt) for the list subtitle."""

    @property
    def filename(self) -> str:
        return self.file_path.name

    @property
    def project_dir_exists(self) -> bool:
        return bool(self.cwd) and Path(self.cwd).exists()
