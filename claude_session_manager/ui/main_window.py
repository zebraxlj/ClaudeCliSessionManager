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

import os
import subprocess
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Tuple

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
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
    QSplitter,
    QTextBrowser,
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
    padding: 8px 10px;
    margin: 2px 6px;
    border-radius: 6px;
}
#sessionList::item:hover { background: #f0f6fc; }
#sessionList::item:selected { background: #cfe4fa; color: #000; }
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
        self.resize(1200, 740)
        self.setStyleSheet(STYLE)
        self._all_sessions: List[SessionMeta] = []
        self._current_scope: Optional[str] = None  # None / _SCOPE_ALL = all
        self._suppress_nav = False
        self._build_ui()
        self.reload()

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
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._show_context_menu)
        self.list.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list, 1)

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
        body_layout.addWidget(self.preview, 1)
        layout.addWidget(body, 1)
        return panel

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
            if show_project:
                subtitle = f"{s.project_name}  ·  {s.message_count} msgs  ·  {s.filename}"
            else:
                subtitle = f"{s.message_count} msgs  ·  {s.filename}"
            item = QListWidgetItem(f"{s.title}\n{subtitle}")
            item.setToolTip(f"{s.title}\n{s.filename}\n{s.project_dir or ''}")
            item.setData(_META_ROLE, s)
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
        act_proj = menu.addAction("Open project folder")
        act_proj.setEnabled(bool(data.project_dir))
        act_store = menu.addAction("Open storage folder")
        menu.addSeparator()
        act_del = menu.addAction("Delete (to Recycle Bin)")
        chosen = menu.exec_(self.list.viewport().mapToGlobal(pos))
        if chosen == act_proj:
            self._open_project_folder()
        elif chosen == act_store:
            self._open_storage_folder()
        elif chosen == act_del:
            self._delete_selected()

    # ---- Actions --------------------------------------------------------
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
    # Base application font (Windows default is ~9pt; bump ~30% for readability).
    app.setFont(QFont("Segoe UI", 12))
    window = MainWindow()
    window.show()
    return app.exec_()
