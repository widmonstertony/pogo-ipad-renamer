from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .gui_appraisal import AppraisalRenamerApp, STATE_TEXT, friendly_appraisal_event
from .gui_hdpi import enable_per_monitor_dpi, project_root, self_check
from .gui_native import collect_native_status


def friendly_ipad_landscape_event(line: str) -> str | None:
    stripped = line.strip()
    if not stripped:
        return None
    if stripped.startswith("[INFO]") and "RapidOCR" in stripped:
        return None
    try:
        event = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return friendly_appraisal_event(line)
    if not isinstance(event, dict):
        return friendly_appraisal_event(line)
    event_type = str(event.get("type", ""))
    if event_type == "device":
        return (
            f"实际连接：{event.get('name')} {event.get('machine')} · "
            f"{event.get('system')} {event.get('version')} · "
            f"MCP 触控空间 {event.get('width')}×{event.get('height')}"
        )
    if event_type == "navigation":
        state = str(event.get("state", "UNKNOWN"))
        return STATE_TEXT.get(state, f"横屏状态：{state}")
    if event_type == "iv_measurement":
        return (
            f"像素鉴定条：A/D/S={event.get('attack')}/{event.get('defense')}/"
            f"{event.get('stamina')} · 置信度={float(event.get('confidence', 0)):.1%}"
        )
    if event_type == "pokemon":
        return (
            f"识别完成：{event.get('species')}  "
            f"A/D/S={event.get('attack')}/{event.get('defense')}/{event.get('stamina')}  "
            f"IV={event.get('percent')}%  →  {event.get('nickname')}  "
            f"名称={float(event.get('name_confidence', 0)):.1%} · "
            f"鉴定条={float(event.get('confidence', 0)):.1%}"
        )
    return friendly_appraisal_event(line)


class IPadLandscapeRenamerApp(AppraisalRenamerApp):
    def _build_ui(self) -> None:
        super()._build_ui()
        self.batch_var.set(1)
        self._clear_log()
        self._append_log("就绪。已兼容 iPad14,6 横屏；不需要旋转设备。")
        self._append_log("导航使用真机校准锚点；名称使用本地离线 OCR；IV 使用鉴定条像素测量。")
        self._append_log("扫描只读取当前一只并返回详情页，不打开键盘；改名仍需单独确认。")

    def _apply_status(self, status: dict[str, Any]) -> None:
        self._set_status(self.mcp_status_var, status["mcp"]["ok"], status["mcp"]["detail"])
        self._set_status(self.ollama_status_var, True, "确定性横屏流程无需调用模型")
        self._set_status(self.opencode_status_var, status["opencode"]["ok"], status["opencode"]["detail"])
        self.check_button.configure(state="normal" if not self.process else "disabled")
        if status["mcp"]["ok"] and status["opencode"]["ok"]:
            self._append_log("连接检查通过：iOS MCP、本地离线识别和 Python 执行引擎均已就绪。")
        else:
            self._append_log("连接检查未完全通过，请查看顶部状态。")

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
            "pogo_iphone_renamer.ipad_landscape_agent_v9",
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

    parser = argparse.ArgumentParser(description="Pokémon GO iPad 横屏整理助手")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    root: Path = project_root()
    if args.self_check:
        return self_check(root)
    enable_per_monitor_dpi()
    import tkinter as tk

    window = tk.Tk()
    IPadLandscapeRenamerApp(window, root)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
