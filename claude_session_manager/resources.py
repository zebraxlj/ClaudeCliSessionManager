"""Central registry of bundled resource paths.

The ``assets/`` folder ships static files (icons, images) inside the package so
they travel with the wheel. This module anchors off the package directory and
exposes their absolute paths as plain string constants, ready to hand to Qt.
"""

from __future__ import annotations

from pathlib import Path

_PKG_DIR = Path(__file__).resolve().parent
_ASSETS_DIR = _PKG_DIR / "assets"

# Application icon. The multi-resolution ``.ico`` is what Qt uses (it picks the
# right size for the title bar / taskbar / Alt+Tab); ``claude.png`` is its
# source image, kept for reference and future re-exports.
ICON_PATH = str(_ASSETS_DIR / "claude.ico")
ICON_PNG_PATH = str(_ASSETS_DIR / "claude.png")
