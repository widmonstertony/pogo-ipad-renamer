from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .gui_hdpi import enable_per_monitor_dpi, project_root, self_check
from .gui_ipad_landscape import IPadLandscapeRenamerApp, friendly_ipad_landscape_event
from .gui_native import collect_native_status


class IPadLandscapeRenamerAppV2(IPadLandscapeRenamerApp):
    def _build_ui(self) -> None:
        super()._build_ui()
        self._append_log("改名字段采用“按原名字符数退格 → 输入 → accessibility 逐字核验”。")

    def _run_worker(self, write_enabled: bool) -> None:
        status = collect_native_status(self.settings)
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
            "pogo_iphone_renamer.ipad_landscape_agent_v12",
            "--mode",
            "rename" if write_enabled else "scan",
            "--ollama-url",
            self.settings.ollama_url,
            "--model",
            self.settings.model,
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
            self.events.put(("fatal", f"无法启动横屏鉴定流程：{exc}"))
        finally:
            self.process = None


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tkinter as tk

    parser = argparse.ArgumentParser(description="Pokémon GO iPad 横屏整理助手 v12")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    root: Path = project_root()
    if args.self_check:
        return self_check(root)
    enable_per_monitor_dpi()
    window = tk.Tk()
    IPadLandscapeRenamerAppV2(window, root)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
