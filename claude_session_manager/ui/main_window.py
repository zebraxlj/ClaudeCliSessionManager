"""The main PyQt5 window for Claude Session Manager.

PowerToys-style three-pane layout:

  ┌──────────┬─────────────────┬──────────────────┐
  │ nav rail │ session list    │ preview          │
  │ (scope)  │ (current scope) │                  │
  └──────────┴─────────────────┴──────────────────┘

The nav rail (left) is a flat single-level list of *scopes*: an "All sessions"
entry plus one entry per project. Selecting a scope drives what the middle list
shows and what the search box filters within.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import QRect, QSize, Qt, QTimer
from PyQt5.QtGui import QColor, QFont, QKeySequence, QTextCharFormat, QTextCursor
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QShortcut,
    QSplitter,
    QStyledItemDelegate,
    QTextBrowser,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

try:
    from send2trash import send2trash
except ImportError:  # pragma: no cover - dependency guaranteed by pyproject
    send2trash = None

from ..models import SessionMeta
from ..preview import render_session_html
from ..scanner import scan_sessions

# Role used to stash data on list items.
_META_ROLE = Qt.UserRole  # SessionMeta on a session list item.
_SCOPE_ROLE = Qt.UserRole + 1  # scope key (str) or None on a nav item.
_TITLE_ROLE = Qt.UserRole + 2  # session title (drawn by the delegate).
_SUB_ROLE = Qt.UserRole + 3  # session subtitle line (drawn by the delegate).

# Header band height shared by all three panes so their top edges line up.
_HEADER_H = 44

# Sentinel scope meaning "all projects".
_SCOPE_ALL = "__all__"

STYLE = """
QSplitter::handle { background: #e5e5e5; }

#headerBar {
    background: #fafafa;
    border-bottom: 1px solid #e5e5e5;
}
#headerTitle { font-weight: 600; font-size: 14px; padding-left: 4px; }

#nav {
    background: #f3f3f3;
    border: none;
    outline: 0;
}
#nav::item {
    padding: 7px 10px;
    margin: 2px 6px;
    border-radius: 6px;
    border-left: 3px solid transparent;
}
#nav::item:hover { background: #e9e9e9; }
#nav::item:selected {
    background: #e1e1e1;
    color: #000;
    border-left: 3px solid #0067c0;
}

#sessionList {
    background: #ffffff;
    border: none;
    outline: 0;
}
#sessionList::item {
    margin: 2px 6px;
    border-radius: 6px;
}
#sessionList::item:hover { background: #f0f6fc; }
#sessionList::item:selected { background: #cfe4fa; color: #000; }

#findBar {
    background: #fafafa;
    border: 1px solid #e0e0dc;
    border-radius: 6px;
}
#findBar QPushButton {
    border: none;
    background: transparent;
    padding: 2px 6px;
    font-size: 14px;
}
#findBar QPushButton:hover { background: #e6e6e2; border-radius: 4px; }
#findBar QPushButton:disabled { color: #bbb; }
#findCount { color: #888; font-size: 13px; min-width: 64px; }
"""


def _open_in_file_manager(path: str) -> None:
    """Open *path* in the OS file manager (cross-platform)."""
    if not path or not os.path.exists(path):
        raise FileNotFoundError(path)
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _session_group_key(s: SessionMeta) -> str:
    """Stable grouping key for a session (real project dir, else storage dir)."""
    return s.project_dir or s.storage_dir.name


def _append_jsonl_line(path, line: str) -> None:
    """Append a single JSONL *line* to *path*, ensuring a newline boundary.

    Uses append mode so the write lands at the true end of file even if another
    process (e.g. an active Claude Code session) is writing concurrently. Raises
    OSError if the file is locked / unwritable.
    """
    with open(path, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        need_nl = fh.tell() > 0
        if need_nl:
            fh.seek(-1, os.SEEK_END)
            need_nl = fh.read(1) != b"\n"
    prefix = b"\n" if need_nl else b""
    with open(path, "ab") as fh:
        fh.write(prefix + (line + "\n").encode("utf-8"))


def _format_updated(ts: float) -> str:
    """Relative time for recent edits, absolute date once older than a week."""
    delta = time.time() - ts
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta // 60)} min ago"
    if delta < 86400:
        return f"{int(delta // 3600)} h ago"
    if delta < 7 * 86400:
        return f"{int(delta // 86400)} d ago"
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


def _format_full(ts: float) -> str:
    """Full timestamp for tooltips."""
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")


class _SessionItemDelegate(QStyledItemDelegate):
    """Draws a session row as a bold dark title over a smaller grey subtitle.

    Supports inline renaming: an editor opens over the title line, pre-filled
    with the current title. On commit it calls *on_rename(meta, new_title)*.
    """

    _TITLE_COLOR = QColor("#1a1a1a")
    _SUB_COLOR = QColor("#888888")
    _LEFT = 16
    _RIGHT = 12

    def __init__(self, parent=None, on_rename=None) -> None:
        super().__init__(parent)
        self._on_rename = on_rename

    def sizeHint(self, option, index):  # noqa: N802 - Qt override
        return QSize(0, 52)

    # ---- Inline rename editor ----
    def createEditor(self, parent, option, index):  # noqa: N802 - Qt override
        editor = QLineEdit(parent)
        font = QFont(option.font)
        font.setPointSize(option.font.pointSize() + 1)
        font.setBold(True)
        editor.setFont(font)
        return editor

    def setEditorData(self, editor, index):  # noqa: N802 - Qt override
        editor.setText(index.data(_TITLE_ROLE) or "")
        editor.selectAll()

    def setModelData(self, editor, model, index):  # noqa: N802 - Qt override
        new_title = editor.text().strip()
        meta = index.data(_META_ROLE)
        if self._on_rename and isinstance(meta, SessionMeta):
            # Defer so the view finishes closing the editor before we reload.
            QTimer.singleShot(0, lambda m=meta, t=new_title: self._on_rename(m, t))

    def updateEditorGeometry(self, editor, option, index):  # noqa: N802 - Qt override
        rect = QRect(option.rect)
        rect.setLeft(rect.left() + self._LEFT - 2)
        rect.setRight(rect.right() - self._RIGHT)
        rect.setTop(rect.top() + 5)
        rect.setHeight(max(editor.sizeHint().height(), 26))
        editor.setGeometry(rect)

    def paint(self, painter, option, index):  # noqa: N802 - Qt override
        # Let the style paint the background + selection/hover (driven by QSS).
        super().paint(painter, option, index)

        title = index.data(_TITLE_ROLE) or ""
        subtitle = index.data(_SUB_ROLE) or ""
        rect = option.rect
        left = rect.left() + self._LEFT
        width = rect.width() - self._LEFT - self._RIGHT

        painter.save()

        title_font = QFont(option.font)
        title_font.setPointSize(option.font.pointSize() + 1)
        title_font.setBold(False)
        painter.setFont(title_font)
        painter.setPen(self._TITLE_COLOR)
        tfm = painter.fontMetrics()
        ty = rect.top() + 9 + tfm.ascent()
        painter.drawText(left, ty, tfm.elidedText(title, Qt.ElideRight, width))

        sub_font = QFont(option.font)
        sub_font.setPointSize(max(option.font.pointSize() - 2, 8))
        painter.setFont(sub_font)
        painter.setPen(self._SUB_COLOR)
        sfm = painter.fontMetrics()
        sy = rect.top() + 9 + tfm.height() + 3 + sfm.ascent()
        painter.drawText(left, sy, sfm.elidedText(subtitle, Qt.ElideRight, width))

        painter.restore()


def _make_header(*widgets: QWidget) -> QWidget:
    """Build a fixed-height header band holding *widgets* in a row."""
    bar = QWidget()
    bar.setObjectName("headerBar")
    bar.setFixedHeight(_HEADER_H)
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(8, 0, 8, 0)
    layout.setSpacing(6)
    for w in widgets:
        layout.addWidget(w)
    return bar


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Claude Session Manager")
        self.resize(1920, 1080)
        self.setStyleSheet(STYLE)
        self._all_sessions: List[SessionMeta] = []
        self._current_scope: Optional[str] = None  # None / _SCOPE_ALL = all
        self._suppress_nav = False
        self._laid_out = False
        self._build_ui()
        self.reload()

    def showEvent(self, event):  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._laid_out:
            self._laid_out = True
            # Defer one tick so the splitter has its final on-screen width.
            QTimer.singleShot(0, self._fit_nav_width)

    # ---- UI construction ------------------------------------------------
    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal, self)
        splitter.addWidget(self._build_nav_panel())
        splitter.addWidget(self._build_list_panel())
        splitter.addWidget(self._build_preview_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 2)
        splitter.setStretchFactor(2, 3)
        splitter.setSizes([220, 360, 620])
        self._splitter = splitter
        self.setCentralWidget(splitter)

    def _build_nav_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        title = QLabel("Claude Sessions")
        title.setObjectName("headerTitle")
        layout.addWidget(_make_header(title))

        self.nav = QListWidget()
        self.nav.setObjectName("nav")
        self.nav.setMinimumWidth(160)
        self.nav.currentItemChanged.connect(self._on_nav_changed)
        layout.addWidget(self.nav, 1)
        return panel

    def _build_list_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search title / filename / path…")
        self.search_box.setClearButtonEnabled(True)
        self.search_box.textChanged.connect(self._apply_filter)
        refresh_btn = QPushButton("⟳")
        refresh_btn.setToolTip("Rescan sessions")
        refresh_btn.setFixedWidth(34)
        refresh_btn.clicked.connect(self.reload)
        layout.addWidget(_make_header(self.search_box, refresh_btn))

        self.list = QListWidget()
        self.list.setObjectName("sessionList")
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)  # edit only via F2 / menu
        self.list.setItemDelegate(
            _SessionItemDelegate(self.list, on_rename=self._apply_inline_rename)
        )
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        self.list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list, 1)

        rename_sc = QShortcut(QKeySequence(Qt.Key_F2), self.list)
        rename_sc.setContext(Qt.WidgetWithChildrenShortcut)
        rename_sc.activated.connect(self._rename_selected)

        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(6, 4, 6, 4)
        self.open_proj_btn = QPushButton("Open project folder")
        self.open_store_btn = QPushButton("Open storage folder")
        self.delete_btn = QPushButton("Delete")
        for b in (self.open_proj_btn, self.open_store_btn, self.delete_btn):
            b.setEnabled(False)
        self.open_proj_btn.clicked.connect(self._open_project_folder)
        self.open_store_btn.clicked.connect(self._open_storage_folder)
        self.delete_btn.clicked.connect(self._delete_selected)
        btn_bar.addWidget(self.open_proj_btn)
        btn_bar.addWidget(self.open_store_btn)
        btn_bar.addWidget(self.delete_btn)
        layout.addLayout(btn_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; font-size: 13px; padding: 0 8px 6px;")
        layout.addWidget(self.status_label)
        return panel

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.header_label = QLabel("Select a session to preview")
        self.header_label.setObjectName("headerTitle")
        layout.addWidget(_make_header(self.header_label))

        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(8, 6, 8, 8)
        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet("color: #888; font-size: 13px; padding: 0 2px;")
        self.meta_label.setWordWrap(True)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        body_layout.addWidget(self.meta_label)
        body_layout.addWidget(self._build_find_bar())
        body_layout.addWidget(self.preview, 1)
        layout.addWidget(body, 1)

        # Ctrl+F opens the in-preview find bar; Esc (inside it) closes it.
        find_sc = QShortcut(QKeySequence.Find, self.preview)
        find_sc.setContext(Qt.WidgetWithChildrenShortcut)
        find_sc.activated.connect(self._show_find)
        return panel

    def _build_find_bar(self) -> QWidget:
        """A hidden find-in-preview bar (input + prev/next + match count)."""
        bar = QWidget()
        bar.setObjectName("findBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 4, 6, 4)
        row.setSpacing(4)

        self.find_input = QLineEdit()
        self.find_input.setPlaceholderText("Find in conversation…")
        self.find_input.setClearButtonEnabled(True)
        self.find_input.textChanged.connect(self._on_find_text_changed)
        self.find_input.returnPressed.connect(lambda: self._goto_match(1))

        self.find_count = QLabel("")
        self.find_count.setObjectName("findCount")
        self.find_count.setAlignment(Qt.AlignCenter)

        self.find_prev_btn = QPushButton("▲")
        self.find_prev_btn.setToolTip("Previous match")
        self.find_prev_btn.clicked.connect(lambda: self._goto_match(-1))
        self.find_next_btn = QPushButton("▼")
        self.find_next_btn.setToolTip("Next match")
        self.find_next_btn.clicked.connect(lambda: self._goto_match(1))
        close_btn = QPushButton("✕")
        close_btn.setToolTip("Close (Esc)")
        close_btn.clicked.connect(self._hide_find)

        row.addWidget(self.find_input, 1)
        row.addWidget(self.find_count)
        row.addWidget(self.find_prev_btn)
        row.addWidget(self.find_next_btn)
        row.addWidget(close_btn)

        esc_sc = QShortcut(QKeySequence(Qt.Key_Escape), bar)
        esc_sc.setContext(Qt.WidgetWithChildrenShortcut)
        esc_sc.activated.connect(self._hide_find)

        self.find_bar = bar
        self._find_matches: List[QTextCursor] = []
        self._find_idx = -1
        bar.hide()
        return bar

    # ---- Grouping helper ------------------------------------------------
    def _grouped_projects(self) -> List[Tuple[str, List[SessionMeta]]]:
        """Group all sessions by project, ordered by each group's newest session."""
        groups: Dict[str, List[SessionMeta]] = defaultdict(list)
        for s in self._all_sessions:
            groups[_session_group_key(s)].append(s)
        return sorted(
            groups.items(),
            key=lambda kv: max(s.modified_at for s in kv[1]),
            reverse=True,
        )

    # ---- Data loading ---------------------------------------------------
    def reload(self) -> None:
        self._all_sessions = scan_sessions()
        self._populate_nav()
        self._apply_filter()

    def _populate_nav(self) -> None:
        """Rebuild the nav rail, preserving the current scope selection if possible."""
        self._suppress_nav = True
        self.nav.clear()

        all_item = QListWidgetItem(f"📋  All sessions  ({len(self._all_sessions)})")
        all_item.setData(_SCOPE_ROLE, _SCOPE_ALL)
        self.nav.addItem(all_item)

        groups = self._grouped_projects()
        if groups:
            label = QListWidgetItem("PROJECTS")
            label.setFlags(Qt.NoItemFlags)  # non-selectable section header
            lf = label.font()
            lf.setPointSize(max(lf.pointSize() - 2, 8))
            label.setFont(lf)
            label.setForeground(Qt.gray)
            self.nav.addItem(label)

        target_row = 0  # default back to "All sessions"
        for group_key, items in groups:
            display_name = items[0].project_name
            it = QListWidgetItem(f"📁  {display_name}  ({len(items)})")
            it.setData(_SCOPE_ROLE, group_key)
            it.setToolTip(group_key)
            self.nav.addItem(it)
            if group_key == self._current_scope:
                target_row = self.nav.row(it)

        self._suppress_nav = False
        self.nav.setCurrentRow(target_row)

    def _fit_nav_width(self) -> None:
        """Lay out the panes at startup.

        The nav column widens to fit the widest project name; the session list
        then spans from the nav edge to the window's horizontal midpoint
        (i.e. its width = half the window width minus the nav width); the
        preview takes the remaining right half.
        """
        fm = self.nav.fontMetrics()
        max_text = max(
            (fm.horizontalAdvance(self.nav.item(i).text()) for i in range(self.nav.count())),
            default=0,
        )
        # Account for item padding (7*2) + margin (6*2) + accent border (3) + slack.
        desired = max_text + 7 * 2 + 6 * 2 + 3 + 24
        nav_w = max(self.nav.minimumWidth(), min(desired, 480))  # clamp range

        total = self._splitter.width()
        list_w = max(200, total // 2 - nav_w)
        preview_w = max(200, total - nav_w - list_w)
        self._splitter.setSizes([nav_w, list_w, preview_w])

    def _on_nav_changed(self) -> None:
        if self._suppress_nav:
            return
        item = self.nav.currentItem()
        scope = item.data(_SCOPE_ROLE) if item is not None else _SCOPE_ALL
        self._current_scope = None if scope == _SCOPE_ALL else scope
        self._apply_filter()

    def _apply_filter(self) -> None:
        # 1. Narrow to the current scope (a single project, or everything).
        if self._current_scope is None:
            scoped = list(self._all_sessions)
        else:
            scoped = [
                s for s in self._all_sessions
                if _session_group_key(s) == self._current_scope
            ]
        # 2. Apply the search term within that scope.
        term = self.search_box.text().strip().lower()
        if term:
            scoped = [
                s for s in scoped
                if term in s.title.lower()
                or term in s.filename.lower()
                or term in (s.project_dir or "").lower()
                or term in s.project_name.lower()
            ]
        self._populate_list(scoped)

    def _populate_list(self, sessions: List[SessionMeta]) -> None:
        self.list.clear()
        show_project = self._current_scope is None
        for s in sessions:
            updated = _format_updated(s.modified_at)
            if show_project:
                subtitle = f"Updated {updated}  ·  {s.message_count} msgs  ·  {s.project_name}"
            else:
                subtitle = f"Updated {updated}  ·  {s.message_count} msgs"
            item = QListWidgetItem()
            item.setFlags(item.flags() | Qt.ItemIsEditable)  # enable inline rename
            item.setData(_TITLE_ROLE, s.title)
            item.setData(_SUB_ROLE, subtitle)
            item.setData(_META_ROLE, s)
            item.setToolTip(
                f"{s.title}\n{s.filename}\n{s.project_dir or ''}\n"
                f"Updated {_format_full(s.modified_at)}"
            )
            self.list.addItem(item)

        scope_txt = "All projects" if show_project else self.nav.currentItem().text().strip()
        self.status_label.setText(f"{len(sessions)} session(s)  ·  scope: {scope_txt}")

    # ---- Selection / preview -------------------------------------------
    def _current_meta(self) -> Optional[SessionMeta]:
        item = self.list.currentItem()
        if item is None:
            return None
        data = item.data(_META_ROLE)
        return data if isinstance(data, SessionMeta) else None

    def _on_selection_changed(self) -> None:
        meta = self._current_meta()
        has = meta is not None
        self.open_proj_btn.setEnabled(has and bool(meta and meta.project_dir))
        self.open_store_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)
        if meta is None:
            self.header_label.setText("Select a session to preview")
            self.meta_label.setText("")
            self.preview.clear()
            if self.find_bar.isVisible():
                self._on_find_text_changed()
            return
        self.header_label.setText(meta.title)
        size_kb = meta.size / 1024
        self.meta_label.setText(
            f"{meta.filename}  ·  {meta.message_count} messages  ·  "
            f"{size_kb:.1f} KB  ·  {meta.project_dir or '(no project dir recorded)'}"
        )
        try:
            self.preview.setHtml(render_session_html(meta.file_path))
        except Exception as exc:  # noqa: BLE001 - surface any render error
            self.preview.setHtml(f"<p>Failed to render: {exc}</p>")
        # Re-run the active find against the freshly loaded document.
        if self.find_bar.isVisible():
            self._on_find_text_changed()

    # ---- Find in preview ------------------------------------------------
    _FIND_BG = QColor("#fff3a3")

    def _show_find(self) -> None:
        self.find_bar.show()
        self.find_input.setFocus()
        self.find_input.selectAll()
        self._on_find_text_changed()

    def _hide_find(self) -> None:
        self.find_bar.hide()
        self._find_matches = []
        self._find_idx = -1
        self.preview.setExtraSelections([])
        self.preview.setFocus()

    def _on_find_text_changed(self) -> None:
        """Recompute matches, highlight them all, and jump to the first one."""
        term = self.find_input.text()
        self._find_matches = []
        if term:
            doc = self.preview.document()
            cur = doc.find(term, 0)
            while not cur.isNull():
                self._find_matches.append(QTextCursor(cur))
                cur = doc.find(term, cur)

        selections = []
        fmt = QTextCharFormat()
        fmt.setBackground(self._FIND_BG)
        for c in self._find_matches:
            sel = QTextEdit.ExtraSelection()
            sel.cursor = c
            sel.format = fmt
            selections.append(sel)
        self.preview.setExtraSelections(selections)

        self._find_idx = -1
        if self._find_matches:
            self._goto_match(1)
        else:
            self.find_count.setText("No results" if term else "")
        has = bool(self._find_matches)
        self.find_prev_btn.setEnabled(has)
        self.find_next_btn.setEnabled(has)

    def _goto_match(self, delta: int) -> None:
        if not self._find_matches:
            return
        self._find_idx = (self._find_idx + delta) % len(self._find_matches)
        self.preview.setTextCursor(self._find_matches[self._find_idx])
        self.preview.ensureCursorVisible()
        self.find_count.setText(f"{self._find_idx + 1} / {len(self._find_matches)}")

    # ---- Context menu ---------------------------------------------------
    def _show_context_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        data = item.data(_META_ROLE)
        if not isinstance(data, SessionMeta):
            return
        self.list.setCurrentItem(item)
        menu = QMenu(self)
        act_rename = menu.addAction("Rename… (F2)")
        menu.addSeparator()
        act_proj = menu.addAction("Open project folder")
        act_proj.setEnabled(bool(data.project_dir))
        act_store = menu.addAction("Open storage folder")
        menu.addSeparator()
        act_del = menu.addAction("Delete (to Recycle Bin)")
        chosen = menu.exec_(self.list.viewport().mapToGlobal(pos))
        if chosen == act_rename:
            self._rename_selected()
        elif chosen == act_proj:
            self._open_project_folder()
        elif chosen == act_store:
            self._open_storage_folder()
        elif chosen == act_del:
            self._delete_selected()

    # ---- Actions --------------------------------------------------------
    def _select_session(self, session_id: str) -> None:
        """Re-select the list row for *session_id*, if present."""
        for i in range(self.list.count()):
            item = self.list.item(i)
            data = item.data(_META_ROLE)
            if isinstance(data, SessionMeta) and data.session_id == session_id:
                self.list.setCurrentItem(item)
                return

    def _rename_selected(self) -> None:
        """Open the inline editor over the selected row's title."""
        item = self.list.currentItem()
        if item is not None:
            self.list.editItem(item)

    def _apply_inline_rename(self, meta: SessionMeta, new_title: str) -> None:
        """Persist an inline rename, then refresh and re-select the row."""
        if not new_title or new_title == meta.title:
            return
        if not self._write_title_with_retry(meta, new_title):
            return
        session_id = meta.session_id
        self.reload()
        self._select_session(session_id)

    def _write_title_with_retry(
        self, meta: SessionMeta, new_title: str, attempts: int = 5, delay: float = 0.15
    ) -> bool:
        """Append an ai-title line, retrying on transient lock errors.

        Only pops an error dialog once every attempt has failed (e.g. the file
        is held open by a running terminal session).
        """
        record = json.dumps({"type": "ai-title", "aiTitle": new_title}, ensure_ascii=False)
        last_err: Optional[Exception] = None
        for _ in range(attempts):
            try:
                _append_jsonl_line(meta.file_path, record)
                return True
            except OSError as exc:
                last_err = exc
                time.sleep(delay)
        QMessageBox.critical(
            self,
            "Rename failed",
            f"Could not write the new title after {attempts} attempts.\n"
            f"The session file may be open in a running terminal — please try "
            f"again in a moment.\n\n{last_err}",
        )
        return False

    def _open_project_folder(self) -> None:
        meta = self._current_meta()
        if meta is None or not meta.project_dir:
            return
        try:
            _open_in_file_manager(meta.project_dir)
        except FileNotFoundError:
            QMessageBox.warning(
                self,
                "Folder not found",
                f"The project folder no longer exists:\n{meta.project_dir}",
            )

    def _open_storage_folder(self) -> None:
        meta = self._current_meta()
        if meta is None:
            return
        try:
            _open_in_file_manager(str(meta.storage_dir))
        except FileNotFoundError:
            QMessageBox.warning(
                self,
                "Folder not found",
                f"The storage folder no longer exists:\n{meta.storage_dir}",
            )

    def _delete_selected(self) -> None:
        meta = self._current_meta()
        if meta is None:
            return
        resp = QMessageBox.question(
            self,
            "Delete session",
            f"Move this session to the Recycle Bin?\n\n"
            f"{meta.title}\n{meta.filename}",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        try:
            if send2trash is None:
                raise RuntimeError("send2trash is not installed")
            send2trash(str(meta.file_path))
        except Exception as exc:  # noqa: BLE001 - report any deletion failure
            QMessageBox.critical(
                self, "Delete failed", f"Could not delete the file:\n{exc}"
            )
            return
        self.reload()


def run() -> int:
    app = QApplication(sys.argv)
    # Base application font: Segoe UI for Latin, falling back to Microsoft YaHei
    # for CJK so Chinese renders in the clean system UI font (not serif SimSun).
    base_font = QFont("Segoe UI", 11)
    base_font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
    app.setFont(base_font)
    window = MainWindow()
    window.show()
    return app.exec_()
