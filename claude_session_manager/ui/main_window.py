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
from PyQt5.QtGui import (
    QColor,
    QFont,
    QIcon,
    QKeySequence,
    QTextCharFormat,
    QTextCursor,
)
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QGraphicsDropShadowEffect,
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
    QStyle,
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
from ..resources import ICON_PATH
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
/* Palette — warm Fluent light + Claude clay accent
   surface #faf9f7 · nav #f3f1ee · card #ffffff · hairline #e9e6e1
   text #1f1d1b · sub #6b6660 · accent #c15f3c · accent-line #cc785c
   selection #f4e8e2 · hover #efe9e3 */

* { outline: 0; }

QWidget { background-color: #faf9f7; color: #1f1d1b; }

QSplitter::handle { background: #e9e6e1; }
QSplitter::handle:horizontal { width: 1px; }

#headerBar {
    background: #faf9f7;
    border-bottom: 1px solid #e9e6e1;
}
#headerTitle { font-weight: 600; font-size: 15px; padding-left: 4px; color: #1f1d1b; }

/* Nav rail + session list share one state-layer language */
#nav { background: #f3f1ee; border: none; }
#nav::item, #sessionList::item {
    margin: 3px 8px;
    border-radius: 8px;
    border-left: 3px solid transparent;
    color: #1f1d1b;
}
#nav::item { padding: 8px 12px; }
#nav::item:hover, #sessionList::item:hover { background: #efe7e0; }
#nav::item:selected, #sessionList::item:selected {
    background: #f4e8e2;
    color: #b5563a;
    border-left: 3px solid #cc785c;
}

/* Session list */
#sessionList { background: #ffffff; border: none; }

/* Inputs */
QLineEdit {
    background: #ffffff;
    border: 1px solid #e0dcd5;
    border-radius: 6px;
    padding: 5px 9px;
    selection-background-color: #cc785c;
    selection-color: #ffffff;
}
QLineEdit:focus { border: 1px solid #c15f3c; }

/* Buttons — Material outlined, pill-shaped */
QPushButton {
    background: transparent;
    border: 1px solid #d8d2c9;
    border-radius: 16px;
    padding: 7px 16px;
    color: #b5563a;
    font-weight: 500;
}
QPushButton:hover { background: #f4e8e2; border-color: #d8c3b8; }
QPushButton:pressed { background: #eeddd4; }
QPushButton:disabled { color: #bcb6ac; background: transparent; border-color: #ececec; }
/* Borderless circular icon button (Material icon button) */
#iconBtn {
    border: none; background: transparent; padding: 0;
    font-size: 16px; border-radius: 18px; color: #1f1d1b;
}
#iconBtn:hover { background: #efe7e0; }
#iconBtn:pressed { background: #e6ddd4; }
/* Destructive action */
#dangerBtn { color: #b42318; border-color: #e6cfc9; }
#dangerBtn:hover { color: #b42318; border-color: #e6b4ad; background: #fdf3f1; }
#dangerBtn:disabled { color: #bcb6ac; border-color: #ececec; background: transparent; }

/* Preview */
QTextBrowser {
    background: #ffffff;
    border: 1px solid #e9e6e1;
    border-radius: 8px;
    padding: 4px 8px;
    selection-background-color: #cc785c;
    selection-color: #ffffff;
}

/* Find bar */
#findBar {
    background: #ffffff;
    border: 1px solid #e0dcd5;
    border-radius: 8px;
}
#findBar QLineEdit { border: none; background: transparent; padding: 2px; }
#findBar QPushButton {
    border: none; background: transparent; padding: 2px 6px; font-size: 14px;
}
#findBar QPushButton:hover { background: #efe9e3; border-radius: 6px; }
#findBar QPushButton:disabled { color: #c4bdb3; background: transparent; }
#findCount { color: #6b6660; font-size: 13px; min-width: 64px; }
#statusLabel { color: #6b6660; font-size: 13px; padding: 0 10px 8px; }
#metaLabel { color: #6b6660; font-size: 13px; padding: 0 2px; }

/* Context menu */
QMenu {
    background: #ffffff;
    border: 1px solid #e0dcd5;
    border-radius: 8px;
    padding: 4px;
}
QMenu::item { padding: 6px 22px 6px 14px; border-radius: 6px; }
QMenu::item:selected { background: #f4e8e2; color: #b5563a; }
QMenu::separator { height: 1px; background: #ececec; margin: 4px 8px; }

/* Scrollbars */
QScrollBar:vertical { background: transparent; width: 12px; margin: 2px; }
QScrollBar::handle:vertical {
    background: #d8d2c9; min-height: 28px; border-radius: 5px;
}
QScrollBar::handle:vertical:hover { background: #c3bcb0; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QScrollBar:horizontal { background: transparent; height: 12px; margin: 2px; }
QScrollBar::handle:horizontal {
    background: #d8d2c9; min-width: 28px; border-radius: 5px;
}
QScrollBar::handle:horizontal:hover { background: #c3bcb0; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }

/* Tooltip */
QToolTip {
    background: #ffffff; color: #1f1d1b;
    border: 1px solid #e0dcd5; border-radius: 6px; padding: 4px 8px;
}
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

    _TITLE_COLOR = QColor("#1f1d1b")
    _TITLE_SELECTED = QColor("#b5563a")
    _SUB_COLOR = QColor("#6b6660")
    _LEFT = 16
    _RIGHT = 12
    _ICON = "💬"
    _ICON_GAP = 28  # horizontal space reserved for the leading icon

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
        selected = bool(option.state & QStyle.State_Selected)
        rect = option.rect
        text_left = rect.left() + self._LEFT + self._ICON_GAP
        width = rect.right() - text_left - self._RIGHT

        painter.save()

        # Leading icon, vertically centred in the row.
        icon_font = QFont(option.font)
        icon_font.setPointSize(option.font.pointSize() + 2)
        painter.setFont(icon_font)
        painter.drawText(
            QRect(rect.left() + self._LEFT, rect.top(), self._ICON_GAP, rect.height()),
            Qt.AlignVCenter | Qt.AlignLeft,
            self._ICON,
        )

        title_font = QFont(option.font)
        title_font.setPointSize(option.font.pointSize() + 1)
        painter.setFont(title_font)
        painter.setPen(self._TITLE_SELECTED if selected else self._TITLE_COLOR)
        tfm = painter.fontMetrics()
        ty = rect.top() + 9 + tfm.ascent()
        painter.drawText(text_left, ty, tfm.elidedText(title, Qt.ElideRight, width))

        sub_font = QFont(option.font)
        sub_font.setPointSize(max(option.font.pointSize() - 2, 8))
        painter.setFont(sub_font)
        painter.setPen(self._SUB_COLOR)
        sfm = painter.fontMetrics()
        sy = rect.top() + 9 + tfm.height() + 3 + sfm.ascent()
        painter.drawText(text_left, sy, sfm.elidedText(subtitle, Qt.ElideRight, width))

        painter.restore()


def _apply_elevation(widget: QWidget, blur: int = 14, y: int = 2, alpha: int = 32) -> QWidget:
    """Attach a soft Material-style drop shadow (elevation) to *widget*."""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setXOffset(0)
    effect.setYOffset(y)
    effect.setColor(QColor(0, 0, 0, alpha))
    widget.setGraphicsEffect(effect)
    return widget


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
    _apply_elevation(bar, blur=12, y=2, alpha=28)  # top-app-bar elevation
    return bar


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Claude Session Manager")
        self.setWindowIcon(QIcon(ICON_PATH))
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
        refresh_btn.setObjectName("iconBtn")
        refresh_btn.setToolTip("Rescan sessions")
        refresh_btn.setFixedWidth(36)
        refresh_btn.clicked.connect(self.reload)
        layout.addWidget(_make_header(self.search_box, refresh_btn))

        layout.addSpacing(6)  # breathing room above the list
        self.list = QListWidget()
        self.list.setObjectName("sessionList")
        # ExtendedSelection lets users Ctrl/Shift-click to multi-select for
        # bulk actions like delete; preview still tracks the "current" item.
        self.list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setEditTriggers(QAbstractItemView.NoEditTriggers)  # edit only via F2 / menu
        self.list.setItemDelegate(
            _SessionItemDelegate(self.list, on_rename=self._apply_inline_rename)
        )
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        self.list.currentItemChanged.connect(self._on_selection_changed)
        self.list.itemSelectionChanged.connect(self._on_multi_selection_changed)
        layout.addWidget(self.list, 1)

        rename_sc = QShortcut(QKeySequence(Qt.Key_F2), self.list)
        rename_sc.setContext(Qt.WidgetWithChildrenShortcut)
        rename_sc.activated.connect(self._rename_selected)

        layout.addSpacing(6)  # breathing room below the list
        btn_bar = QHBoxLayout()
        btn_bar.setContentsMargins(12, 8, 12, 8)  # L/R 12 > inter-button 10; roomy top
        btn_bar.setSpacing(10)
        self.open_proj_btn = QPushButton("Open project folder")
        self.open_store_btn = QPushButton("Open storage folder")
        self.delete_btn = QPushButton("Delete")
        self.delete_btn.setObjectName("dangerBtn")
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
        self.status_label.setObjectName("statusLabel")
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
        self.meta_label.setObjectName("metaLabel")
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

        _apply_elevation(bar, blur=18, y=3, alpha=46)  # floating card elevation
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
            lf.setPointSize(lf.pointSize() + 1)
            lf.setBold(True)
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

    def _selected_metas(self) -> List[SessionMeta]:
        """Every selected row's SessionMeta (may be empty)."""
        out: List[SessionMeta] = []
        for item in self.list.selectedItems():
            data = item.data(_META_ROLE)
            if isinstance(data, SessionMeta):
                out.append(data)
        return out

    def _on_multi_selection_changed(self) -> None:
        """Update the Delete button label + enabled state as selection grows."""
        n = len(self.list.selectedItems())
        self.delete_btn.setEnabled(n > 0)
        self.delete_btn.setText("Delete" if n <= 1 else f"Delete ({n})")

    def _on_selection_changed(self) -> None:
        meta = self._current_meta()
        has = meta is not None
        self.open_proj_btn.setEnabled(has and bool(meta and meta.project_dir))
        self.open_store_btn.setEnabled(has)
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
        # If the clicked row isn't part of the current multi-selection, treat
        # this as a single-row action and reset selection to just that row.
        if not item.isSelected():
            self.list.clearSelection()
            self.list.setCurrentItem(item)
            item.setSelected(True)
        selected = self._selected_metas()
        multi = len(selected) > 1
        menu = QMenu(self)
        act_rename = menu.addAction("Rename… (F2)")
        act_rename.setEnabled(not multi)
        menu.addSeparator()
        act_proj = menu.addAction("Open project folder")
        act_proj.setEnabled(not multi and bool(data.project_dir))
        act_store = menu.addAction("Open storage folder")
        act_store.setEnabled(not multi)
        menu.addSeparator()
        del_label = (
            f"Delete {len(selected)} sessions (to Recycle Bin)"
            if multi else "Delete (to Recycle Bin)"
        )
        act_del = menu.addAction(del_label)
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
        metas = self._selected_metas()
        if not metas:
            return
        if len(metas) == 1:
            m = metas[0]
            prompt = (
                f"Move this session to the Recycle Bin?\n\n{m.title}\n{m.filename}"
            )
            title = "Delete session"
        else:
            preview_lines = "\n".join(f"• {m.title}" for m in metas[:5])
            more = "" if len(metas) <= 5 else f"\n… and {len(metas) - 5} more"
            prompt = (
                f"Move {len(metas)} sessions to the Recycle Bin?\n\n"
                f"{preview_lines}{more}"
            )
            title = "Delete sessions"
        resp = QMessageBox.question(
            self, title, prompt, QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if resp != QMessageBox.Yes:
            return
        if send2trash is None:
            QMessageBox.critical(
                self, "Delete failed", "send2trash is not installed."
            )
            return
        failures: List[Tuple[SessionMeta, str]] = []
        for m in metas:
            try:
                send2trash(str(m.file_path))
            except Exception as exc:  # noqa: BLE001 - collect per-file failures
                failures.append((m, str(exc)))
        if failures:
            detail = "\n".join(f"{m.filename}: {err}" for m, err in failures[:10])
            more = "" if len(failures) <= 10 else f"\n… and {len(failures) - 10} more"
            QMessageBox.critical(
                self,
                "Delete failed",
                f"{len(failures)} of {len(metas)} could not be deleted:\n\n"
                f"{detail}{more}",
            )
        self.reload()


def run() -> int:
    # On Windows, tell the shell this process is its own application (not just a
    # generic Python host) so the taskbar groups it under — and shows — our icon
    # rather than the Python interpreter's. Must happen before any window shows.
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "ClaudeSessionManager"
            )
        except Exception:  # pragma: no cover - cosmetic only, never fatal
            pass

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon(ICON_PATH))
    # Base application font: Segoe UI for Latin, falling back to Microsoft YaHei
    # for CJK so Chinese renders in the clean system UI font (not serif SimSun).
    base_font = QFont("Segoe UI", 11)
    base_font.setFamilies(["Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei"])
    app.setFont(base_font)
    window = MainWindow()
    window.show()
    return app.exec_()
