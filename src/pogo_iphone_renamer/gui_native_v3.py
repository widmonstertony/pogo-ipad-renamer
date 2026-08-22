from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .gui_native import collect_native_status, friendly_native_event
from .gui_native_v2 import ResilientNativeRenamerApp
from .gui_hdpi import enable_per_monitor_dpi, project_root, self_check


class QwenSafeRenamerApp(ResilientNativeRenamerApp):
    def _build_ui(self) -> None:
        super()._build_ui()
        self._append_log("Qwen 多轮工具兼容层已启用；工具结果会转换为合法的继续消息。")

    def _run_worker(self, write_enabled: bool) -> None:
        status = collect_native_status(self.settings)
        self.events.put(("status", status))
        missing = [name for name in ("mcp", "ollama", "opencode") if not status[name]["ok"]]
        if missing:
            self.events.put(("fatal", "启动失败：" + "、".join(missing) + " 尚未就绪。"))
            return
        python_launcher = str(status["opencode"]["path"])
        command = [
            python_launcher,
            "-3.13",
            "-u",
            "-m",
            "pogo_iphone_renamer.native_agent_v3",
            "--mode",
            "rename" if write_enabled else "readonly",
            "--ollama-url",
            self.settings.ollama_url,
            "--model",
            self.settings.model,
            "--max-steps",
            "40" if write_enabled else "15",
        ]
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(self.root / "src"),
                "PYTHONUTF8": "1",
                "PYTHONIOENCODING": "utf-8",
                "IPHONE_MCP_URL": self.settings.mcp_url,
                "IPHONE_MCP_HEALTH_URL": self.settings.health_url,
                "IPHONE_MCP_PROTOCOL_VERSION": "2025-11-25",
                "POKEMON_GO_BUNDLE_ID": "com.nianticlabs.pokemongo",
                "POGO_WRITE_ENABLED": "true" if write_enabled else "false",
                "POGO_BATCH_LIMIT": str(self.settings.batch_limit),
                "POGO_OBSERVATION_TTL_SECONDS": "20",
                "POGO_JOURNAL_PATH": str(self.root / ".pogo-data" / "actions.jsonl"),
            }
        )
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
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
                creationflags=creation_flags,
            )
            self.events.put(
                (
                    "log",
                    "原生本地 Agent 已启动。"
                    + ("改名权限仅对本次运行开启。" if write_enabled else "当前工具列表中不存在任何写工具。"),
                )
            )
            assert self.process.stdout is not None
            for line in self.process.stdout:
                message = friendly_native_event(line)
                if message:
                    self.events.put(("log", message))
            code = self.process.wait()
            self.events.put(("finished", code))
        except OSError as exc:
            self.events.put(("fatal", f"无法启动原生 Agent：{exc}"))
        finally:
            self.process = None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Pokémon GO 整理助手")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    root: Path = project_root()
    if args.self_check:
        return self_check(root)

    enable_per_monitor_dpi()
    import tkinter as tk

    window = tk.Tk()
    QwenSafeRenamerApp(window, root)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

