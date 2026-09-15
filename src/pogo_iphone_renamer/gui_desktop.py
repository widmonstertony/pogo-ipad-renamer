"""Choose the native desktop presentation for the current platform."""

from __future__ import annotations

import sys


def main(argv: list[str] | None = None) -> int:
    if sys.platform == "darwin":
        from .macos_launcher import main as macos_main

        return macos_main(argv)
    from .gui_ipad_landscape_v9 import main as tkinter_main

    return tkinter_main(argv)
