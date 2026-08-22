from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from typing import Any

from .gui import APP_TITLE, RenamerApp, project_root, self_check


def scale_for_display(width: int, height: int, dpi: int) -> float:
    """Return a readable scale even when a 4K panel is configured at 100%."""
    dpi_scale = max(1.0, dpi / 96.0)
    resolution_scale = min(width / 1920.0, height / 1080.0)
    resolution_scale = max(1.0, min(resolution_scale, 2.0))
    return round(max(dpi_scale, resolution_scale), 2)


def enable_per_monitor_dpi() -> None:
    if os.name != "nt":
        return
    try:
        import ctypes

        # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2. Using c_void_p preserves
        # the negative pseudo-handle on 64-bit Windows.
        context = ctypes.c_void_p(-4 & ((1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1))
        ctypes.windll.user32.SetProcessDpiAwarenessContext(context)
    except Exception:
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            pass


def window_dpi(window: Any) -> int:
    if os.name != "nt":
        return 96
    try:
        import ctypes

        window.update_idletasks()
        dpi = int(ctypes.windll.user32.GetDpiForWindow(window.winfo_id()))
        return dpi if dpi > 0 else 96
    except Exception:
        return 96


class HiDpiRenamerApp(RenamerApp):
    def _configure_window(self) -> None:
        super()._configure_window()

        screen_width = int(self.window.winfo_screenwidth())
        screen_height = int(self.window.winfo_screenheight())
        dpi = window_dpi(self.window)
        self.display_scale = scale_for_display(screen_width, screen_height, dpi)

        # Tk uses pixels per typographic point. 96 DPI corresponds to 4/3.
        tk_scale = (4.0 / 3.0) * self.display_scale
        self.window.tk.call("tk", "scaling", tk_scale)

        width = min(int(1120 * self.display_scale), screen_width - int(60 * self.display_scale))
        height = min(int(800 * self.display_scale), screen_height - int(90 * self.display_scale))
        width = max(900, width)
        height = max(620, height)
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.minsize(
            min(int(860 * self.display_scale), screen_width),
            min(int(600 * self.display_scale), screen_height),
        )

        style = self.ttk.Style(self.window)
        style.configure("Title.TLabel", font=("Segoe UI", 24, "bold"))
        style.configure("Subtitle.TLabel", font=("Segoe UI", 11))
        style.configure("CardTitle.TLabel", font=("Segoe UI", 12, "bold"))
        style.configure("CardText.TLabel", font=("Segoe UI", 11))
        style.configure("Good.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Bad.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Idle.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 12, "bold"))
        style.configure("Success.TButton", font=("Segoe UI", 12, "bold"))
        style.configure("Danger.TButton", font=("Segoe UI", 11, "bold"))
        style.configure("Secondary.TButton", font=("Segoe UI", 11))

    def _build_ui(self) -> None:
        super()._build_ui()
        self._append_log(
            f"4K 自动缩放已启用：{self.display_scale:.0%} "
            f"（{self.window.winfo_screenwidth()}×{self.window.winfo_screenheight()}）。"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    root: Path = project_root()
    if args.self_check:
        return self_check(root)

    enable_per_monitor_dpi()
    import tkinter as tk

    window = tk.Tk()
    HiDpiRenamerApp(window, root)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

