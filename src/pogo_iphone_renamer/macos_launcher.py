"""Route every macOS entry point through the native SwiftUI application."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    argv = list(argv or [])
    if sys.platform != "darwin":
        from .legacy_gui import main as tkinter_main

        return tkinter_main(argv)
    if "--self-check" in argv:
        from .legacy_gui import main as tkinter_main

        return tkinter_main(["--self-check"])

    root = Path(__file__).resolve().parents[2]
    launcher = root / "启动-PokemonGO-整理助手-macOS.command"
    if not launcher.is_file():
        raise RuntimeError(f"missing macOS launcher: {launcher}")
    environment = os.environ.copy()
    environment.setdefault("POGO_APP_ROOT", str(root))
    return subprocess.call(["/bin/zsh", str(launcher)], cwd=root, env=environment)
