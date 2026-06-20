"""Render a session `.jsonl` file into HTML for the preview pane."""

from __future__ import annotations

import html
import json
import re
from pathlib import Path
from typing import Any, List

_MAX_BLOCK_CHARS = 4000

# Markdown fenced code block: ```lang\n ... ``` (language is optional).
_FENCE_RE = re.compile(r"```[ \t]*([\w.+#-]*)[ \t]*\n?(.*?)```", re.DOTALL)
# Inline code span: `code` (single line, no embedded backticks).
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")


def _esc(text: str) -> str:
    return html.escape(text or "").replace("\n", "<br>")


def _truncate(text: str, limit: int = _MAX_BLOCK_CHARS) -> str:
    if text is None:
        return ""
    if len(text) > limit:
        return text[:limit] + f"\n… [truncated {len(text) - limit} chars]"
    return text


def _render_inline(text: str) -> str:
    """Escape text and turn inline `code` spans into <code> elements."""
    parts: List[str] = []
    last = 0
    for m in _INLINE_CODE_RE.finditer(text):
        parts.append(_esc(text[last:m.start()]))
        parts.append(f"<code>{html.escape(m.group(1))}</code>")
        last = m.end()
    parts.append(_esc(text[last:]))
    return "".join(parts)


def _render_text(raw: str) -> str:
    """Render message text, distinguishing fenced code blocks and inline code.

    Fenced ```blocks``` become a styled monospace box (with an optional language
    label); inline `code` becomes a <code> span; everything else is plain text
    with line breaks preserved.
    """
    raw = _truncate(raw)
    parts: List[str] = []
    last = 0
    for m in _FENCE_RE.finditer(raw):
        before = raw[last:m.start()]
        if before:
            parts.append(_render_inline(before))
        lang = (m.group(1) or "").strip()
        code = (m.group(2) or "").strip("\n")
        header = f'<div class="codelang">{html.escape(lang)}</div>' if lang else ""
        parts.append(
            f'<div class="codeblock">{header}'
            f'<pre class="code">{html.escape(code)}</pre></div>'
        )
        last = m.end()
    rest = raw[last:]
    if rest:
        parts.append(_render_inline(rest))
    return "".join(parts)


def _render_blocks(content: Any) -> List[str]:
    """Render the `content` of one message into a list of HTML fragments."""
    out: List[str] = []
    if content is None:
        return out
    if isinstance(content, str):
        if content.strip():
            out.append(f'<div class="text">{_render_text(content)}</div>')
        return out
    if not isinstance(content, list):
        out.append(f'<div class="text">{_render_text(str(content))}</div>')
        return out

    for block in content:
        if not isinstance(block, dict):
            out.append(f'<div class="text">{_esc(str(block))}</div>')
            continue
        btype = block.get("type")
        if btype == "text":
            txt = block.get("text", "")
            if txt.strip():
                out.append(f'<div class="text">{_render_text(txt)}</div>')
        elif btype == "thinking":
            txt = block.get("thinking", "")
            if txt.strip():
                out.append(
                    '<div class="thinking"><span class="tag">thinking</span>'
                    f"{_esc(_truncate(txt))}</div>"
                )
        elif btype == "tool_use":
            name = block.get("name", "?")
            try:
                args = json.dumps(block.get("input", {}), ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                args = str(block.get("input"))
            out.append(
                '<div class="tool"><span class="tag">tool ▸ '
                f"{_esc(name)}</span><pre>{_esc(_truncate(args, 1500))}</pre></div>"
            )
        elif btype == "tool_result":
            inner = block.get("content")
            if isinstance(inner, list):
                text = "\n".join(
                    b.get("text", "") for b in inner if isinstance(b, dict)
                )
            else:
                text = str(inner) if inner is not None else ""
            if text.strip():
                out.append(
                    '<div class="result"><span class="tag">result</span>'
                    f"<pre>{_esc(_truncate(text, 1500))}</pre></div>"
                )
    return out


def render_session_html(path: Path) -> str:
    """Parse a session file and return a full HTML document for QTextBrowser."""
    rows: List[str] = []
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
                if btype not in ("user", "assistant"):
                    continue
                message = obj.get("message")
                if not isinstance(message, dict):
                    continue
                fragments = _render_blocks(message.get("content"))
                if not fragments:
                    continue
                role = "user" if btype == "user" else "assistant"
                label = "You" if role == "user" else "Claude"
                ts = obj.get("timestamp", "")
                rows.append(
                    f'<div class="msg {role}">'
                    f'<div class="role">{label}'
                    f'<span class="ts">{_esc(ts)}</span></div>'
                    f'{"".join(fragments)}</div>'
                )
    except OSError as exc:
        return f"<html><body><p>Failed to read file: {_esc(str(exc))}</p></body></html>"

    if not rows:
        rows.append('<div class="empty">No displayable messages in this session.</div>')

    body = "\n".join(rows)
    return f"""<html><head><style>
body {{ font-family: 'Segoe UI', sans-serif; font-size: 16px; color: #1f2328; }}
.msg {{ margin: 0 0 14px 0; padding: 8px 12px; border-radius: 8px; }}
.msg.user {{ background: #eef4ff; border: 1px solid #d6e2ff; }}
.msg.assistant {{ background: #f6f6f4; border: 1px solid #e6e6e2; }}
.role {{ font-weight: 600; margin-bottom: 6px; color: #444; }}
.ts {{ font-weight: 400; color: #999; font-size: 13px; margin-left: 8px; }}
.text {{ margin: 4px 0; line-height: 1.5; }}
.codeblock {{ margin: 6px 0; }}
.codelang {{ display: inline-block; font-family: 'Consolas', monospace;
            font-size: 12px; color: #555; background: #e6e6e2;
            padding: 1px 8px; border-radius: 6px 6px 0 0; }}
pre.code {{ background: #f4f4f2; border: 1px solid #e0e0dc; border-radius: 6px;
           padding: 8px 10px; }}
code {{ font-family: 'Consolas', monospace; font-size: 14px; color: #b5005a;
       background: #f0f0f0; padding: 1px 4px; }}
.thinking {{ margin: 4px 0; padding: 6px 8px; background: #fafafa;
            border-left: 3px solid #c9c9c9; color: #777; font-style: italic; }}
.tool {{ margin: 4px 0; padding: 6px 8px; background: #fff8ec;
        border-left: 3px solid #e6b566; }}
.result {{ margin: 4px 0; padding: 6px 8px; background: #f0f7f0;
          border-left: 3px solid #9ac79a; }}
.tag {{ display: inline-block; font-size: 13px; font-weight: 600;
       color: #666; margin-right: 6px; }}
pre {{ white-space: pre-wrap; word-break: break-word; margin: 4px 0 0 0;
      font-family: 'Consolas', monospace; font-size: 15px; }}
.empty {{ color: #999; padding: 20px; }}
</style></head><body>{body}</body></html>"""
