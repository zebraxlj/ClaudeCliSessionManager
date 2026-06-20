"""Entry point: launch the Claude Session Manager GUI."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        # Normal case: launched as a module (``python -m claude_session_manager``
        # or the ``claude-session-manager`` entry point) — package context exists.
        from .ui.main_window import run
    except ImportError:
        # Fallback: the file was run directly as a script (e.g. PyCharm's
        # "run file"), so there is no parent package for relative imports.
        # Put the project root on sys.path and import absolutely.
        import os

        sys.path.insert(
            0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        from claude_session_manager.ui.main_window import run

    return run()


if __name__ == "__main__":
    sys.exit(main())
