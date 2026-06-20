"""The main PyQt5 window for Claude Session Manager."""

from __future__ import annotations

import os
import subprocess
import sys
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTextBrowser,
    QTreeWidget,
    QTreeWidgetItem,
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

# Role used to stash the SessionMeta on a tree item.
_META_ROLE = Qt.UserRole


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


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Claude Session Manager")
        self.resize(1100, 720)
        self._all_sessions: List[SessionMeta] = []
        self._build_ui()
        self.reload()

    # ---- UI construction ------------------------------------------------
    def _build_ui(self) -> None:
        splitter = QSplitter(Qt.Horizontal, self)

        # Left side: search + tree + status.
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(6, 6, 6, 6)

        top_bar = QHBoxLayout()
        self.search_box = QLineEdit()
        self.search_box.setPlaceholderText("Search title / filename / path…")
        self.search_box.textChanged.connect(self._apply_filter)
        refresh_btn = QPushButton("⟳")
        refresh_btn.setToolTip("Rescan sessions")
        refresh_btn.setFixedWidth(34)
        refresh_btn.clicked.connect(self.reload)
        top_bar.addWidget(self.search_box)
        top_bar.addWidget(refresh_btn)
        left_layout.addLayout(top_bar)

        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        self.tree.currentItemChanged.connect(self._on_selection_changed)
        self.tree.setUniformRowHeights(False)
        left_layout.addWidget(self.tree, 1)

        btn_bar = QHBoxLayout()
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
        left_layout.addLayout(btn_bar)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #888; font-size: 13px;")
        left_layout.addWidget(self.status_label)

        # Right side: preview.
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(6, 6, 6, 6)
        self.header_label = QLabel("Select a session to preview")
        self.header_label.setStyleSheet(
            "font-weight: 600; font-size: 18px; padding: 4px;"
        )
        self.header_label.setWordWrap(True)
        self.meta_label = QLabel("")
        self.meta_label.setStyleSheet("color: #888; font-size: 13px; padding: 0 4px;")
        self.meta_label.setWordWrap(True)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        right_layout.addWidget(self.header_label)
        right_layout.addWidget(self.meta_label)
        right_layout.addWidget(self.preview, 1)

        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([420, 680])
        self.setCentralWidget(splitter)

    # ---- Data loading ---------------------------------------------------
    def reload(self) -> None:
        self._all_sessions = scan_sessions()
        self._apply_filter()

    def _apply_filter(self) -> None:
        term = self.search_box.text().strip().lower()
        if term:
            filtered = [
                s
                for s in self._all_sessions
                if term in s.title.lower()
                or term in s.filename.lower()
                or term in (s.cwd or "").lower()
                or term in s.project_name.lower()
            ]
        else:
            filtered = list(self._all_sessions)
        self._populate_tree(filtered)

    def _populate_tree(self, sessions: List[SessionMeta]) -> None:
        self.tree.clear()
        groups: Dict[str, List[SessionMeta]] = defaultdict(list)
        # Key groups by cwd (stable) but display the project name.
        for s in sessions:
            groups[s.cwd or s.storage_dir.name].append(s)

        # Sort groups by their newest session.
        ordered = sorted(
            groups.items(),
            key=lambda kv: max(s.modified_at for s in kv[1]),
            reverse=True,
        )

        for group_key, items in ordered:
            display_name = items[0].project_name
            group_item = QTreeWidgetItem(self.tree)
            group_item.setText(0, f"{display_name}  ({len(items)})")
            group_item.setToolTip(0, group_key)
            gf = group_item.font(0)
            gf.setBold(True)
            group_item.setFont(0, gf)
            group_item.setFlags(group_item.flags() & ~Qt.ItemIsSelectable)

            for s in items:
                child = QTreeWidgetItem(group_item)
                subtitle = f"{s.filename}"
                if s.cwd:
                    subtitle += f"  ·  {s.cwd}"
                child.setText(0, f"{s.title}\n{subtitle}")
                child.setToolTip(0, f"{s.title}\n{s.filename}\n{s.cwd or ''}")
                child.setData(0, _META_ROLE, s)

            group_item.setExpanded(True)

        total = len(sessions)
        self.status_label.setText(
            f"{total} session(s)  ·  {len(ordered)} project(s)"
        )

    # ---- Selection / preview -------------------------------------------
    def _current_meta(self) -> Optional[SessionMeta]:
        item = self.tree.currentItem()
        if item is None:
            return None
        data = item.data(0, _META_ROLE)
        return data if isinstance(data, SessionMeta) else None

    def _on_selection_changed(self) -> None:
        meta = self._current_meta()
        has = meta is not None
        self.open_proj_btn.setEnabled(has and bool(meta and meta.cwd))
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
            f"{size_kb:.1f} KB  ·  {meta.cwd or '(no cwd recorded)'}"
        )
        try:
            self.preview.setHtml(render_session_html(meta.file_path))
        except Exception as exc:  # noqa: BLE001 - surface any render error
            self.preview.setHtml(f"<p>Failed to render: {exc}</p>")

    # ---- Context menu ---------------------------------------------------
    def _show_context_menu(self, pos) -> None:
        item = self.tree.itemAt(pos)
        if item is None:
            return
        data = item.data(0, _META_ROLE)
        if not isinstance(data, SessionMeta):
            return
        self.tree.setCurrentItem(item)
        menu = QMenu(self)
        act_proj = menu.addAction("Open project folder")
        act_proj.setEnabled(bool(data.cwd))
        act_store = menu.addAction("Open storage folder")
        menu.addSeparator()
        act_del = menu.addAction("Delete (to Recycle Bin)")
        chosen = menu.exec_(self.tree.viewport().mapToGlobal(pos))
        if chosen == act_proj:
            self._open_project_folder()
        elif chosen == act_store:
            self._open_storage_folder()
        elif chosen == act_del:
            self._delete_selected()

    # ---- Actions --------------------------------------------------------
    def _open_project_folder(self) -> None:
        meta = self._current_meta()
        if meta is None or not meta.cwd:
            return
        try:
            _open_in_file_manager(meta.cwd)
        except FileNotFoundError:
            QMessageBox.warning(
                self,
                "Folder not found",
                f"The project folder no longer exists:\n{meta.cwd}",
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
