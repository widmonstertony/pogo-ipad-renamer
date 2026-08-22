from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .gui_hdpi import enable_per_monitor_dpi, project_root
from .gui_ipad_landscape import friendly_ipad_landscape_event
from .gui_ipad_landscape_v6 import (
    IPadLandscapeRenamerAppV6,
    collect_deterministic_status,
)


class IPadLandscapeRenamerAppV8(IPadLandscapeRenamerAppV6):
    def _build_ui(self) -> None:
        super()._build_ui()
        self._append_log("设备全局锁已启用：第二个窗口或测试进程不能同时触控 iPad。")
        self._append_log("OK/取消按当前截图 OCR 定位；输入层与遗留弹窗均可安全恢复。")
        self._append_log("鉴定对白最多推进一次；之后只做有限只读重测。")

    def _run_worker(self, write_enabled: bool) -> None:
        status = collect_deterministic_status(self.settings)
        self.events.put(("status", status))
        missing = [name for name in ("mcp", "opencode") if not status[name]["ok"]]
        if missing:
            self.events.put(("fatal", "启动失败：" + "、".join(missing) + " 尚未就绪。"))
            return
        command = [
            str(status["opencode"]["path"]),
            "-3.13",
            "-u",
            "-m",
            "pogo_iphone_renamer.ipad_landscape_agent_v25",
            "--mode",
            "rename" if write_enabled else "scan",
        ]
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(self.root / "src"),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "OC_DISABLE_DOT_ACCESS_WARNING": "1",
                "IPHONE_MCP_URL": self.settings.mcp_url,
                "IPHONE_MCP_HEALTH_URL": self.settings.health_url,
                "IPHONE_MCP_PROTOCOL_VERSION": "2025-11-25",
                "POKEMON_GO_BUNDLE_ID": "com.nianticlabs.pokemongo",
                "POGO_WRITE_ENABLED": "true",
                "POGO_BATCH_LIMIT": "1",
                "POGO_OBSERVATION_TTL_SECONDS": "120",
                "POGO_JOURNAL_PATH": str(self.root / ".pogo-data" / "actions.jsonl"),
            }
        )
        try:
            self.process = subprocess.Popen(
                command,
                cwd=self.root,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                message = friendly_ipad_landscape_event(line)
                if message:
                    self.events.put(("log", message))
            code = self.process.wait()
            self.events.put(("finished", code))
        except OSError as exc:
            self.events.put(("fatal", f"无法启动纯 Python 鉴定流程：{exc}"))
        finally:
            self.process = None


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tkinter as tk

    parser = argparse.ArgumentParser(description="Pokémon GO 纯 Python 横屏整理助手 v25")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    root: Path = project_root()
    if args.self_check:
        from .gui import load_settings

        status = collect_deterministic_status(load_settings(root))
        print(status)
        return 0 if status["mcp"]["ok"] and status["opencode"]["ok"] else 1
    enable_per_monitor_dpi()
    window = tk.Tk()
    IPadLandscapeRenamerAppV8(window, root)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
