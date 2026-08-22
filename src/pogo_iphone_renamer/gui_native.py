from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

from .gui import AppSettings, collect_status, save_settings
from .gui_hdpi import HiDpiRenamerApp, enable_per_monitor_dpi, project_root, self_check


def find_python_launcher() -> str | None:
    if os.name == "nt":
        return shutil.which("py.exe") or shutil.which("py")
    return sys.executable or shutil.which("python3")


def python_worker_command(launcher: str, *, platform_name: str | None = None) -> list[str]:
    platform_name = os.name if platform_name is None else platform_name
    if platform_name == "nt" and Path(launcher).name.casefold() in {"py", "py.exe"}:
        return [launcher, "-3.13"]
    return [launcher]


def collect_native_status(settings: AppSettings) -> dict[str, Any]:
    status = collect_status(settings)
    launcher = find_python_launcher()
    status["opencode"] = {
        "ok": bool(launcher),
        "detail": "Ollama 原生工具引擎" if launcher else "找不到 Python 启动器",
        "path": launcher,
    }
    return status


def friendly_native_event(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped
    if not isinstance(event, dict):
        return stripped
    event_type = str(event.get("type", ""))
    if event_type == "tool":
        return f"正在调用：{event.get('name', '未知工具')}"
    if event_type == "tool_result":
        return f"工具完成：{event.get('name', '未知工具')}"
    if event_type == "tool_error":
        return f"工具被拒绝：{event.get('name', '未知工具')} · {event.get('message', '')}"
    if event_type == "assistant":
        return str(event.get("text", "")).strip() or None
    if event_type in {"status", "thinking", "finished", "error"}:
        return str(event.get("message", "")).strip() or None
    return None


class NativeRenamerApp(HiDpiRenamerApp):
    def _build_ui(self) -> None:
        super()._build_ui()
        self._append_log("执行引擎：Ollama 原生 /api/chat；已绕过 OpenAI 兼容层。")

    def _status_worker(self) -> None:
        self.events.put(("status", collect_native_status(self.settings)))

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
            "pogo_iphone_renamer.native_agent",
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
    NativeRenamerApp(window, root)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
