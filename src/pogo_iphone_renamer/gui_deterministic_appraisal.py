from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .gui_appraisal import AppraisalRenamerApp, friendly_appraisal_event
from .gui_hdpi import enable_per_monitor_dpi, project_root, self_check
from .gui_native import collect_native_status


def friendly_deterministic_event(line: str) -> str | None:
    stripped = line.strip()
    try:
        event = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        return friendly_appraisal_event(line)
    if not isinstance(event, dict):
        return friendly_appraisal_event(line)
    if event.get("type") == "device":
        return (
            f"实际连接：{event.get('name')} {event.get('machine')} · "
            f"{event.get('system')} {event.get('version')} · "
            f"{event.get('width')}×{event.get('height')}"
        )
    if event.get("type") == "navigation":
        orientation = str(event.get("orientation", "UNKNOWN"))
        if orientation != "PORTRAIT_UPRIGHT":
            return f"检测到游戏内容方向异常：{orientation}；不会执行点击。"
    return friendly_appraisal_event(line)


class DeterministicAppraisalRenamerApp(AppraisalRenamerApp):
    def _build_ui(self) -> None:
        super()._build_ui()
        self.batch_var.set("1")
        self._clear_log()
        self._append_log("就绪。入口采用固定锚点/OCR，模型只识别页面和鉴定条，不再猜点击坐标。")
        self._append_log("启动后会显示 MCP 实际连接的设备；游戏必须为竖屏，方向不一致会在点击前停止。")

    def _apply_status(self, status: dict[str, Any]) -> None:
        self._set_status(self.mcp_status_var, status["mcp"]["ok"], status["mcp"]["detail"])
        self._set_status(self.ollama_status_var, status["ollama"]["ok"], status["ollama"]["detail"])
        self._set_status(self.opencode_status_var, status["opencode"]["ok"], status["opencode"]["detail"])
        self.check_button.configure(state="normal" if not self.process else "disabled")
        if all(status[name]["ok"] for name in ("mcp", "ollama", "opencode")):
            self._append_log("连接检查通过：iOS MCP、本地模型和 Python 执行引擎均已就绪。")
        else:
            self._append_log("连接检查未完全通过，请查看顶部状态。")

    def _run_worker(self, write_enabled: bool) -> None:
        status = collect_native_status(self.settings)
        self.events.put(("status", status))
        missing = [name for name in ("mcp", "ollama", "opencode") if not status[name]["ok"]]
        if missing:
            self.events.put(("fatal", "启动失败：" + "、".join(missing) + " 尚未就绪。"))
            return
        command = [
            str(status["opencode"]["path"]),
            "-3.13",
            "-u",
            "-m",
            "pogo_iphone_renamer.deterministic_appraisal_agent",
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
                message = friendly_deterministic_event(line)
                if message:
                    self.events.put(("log", message))
            code = self.process.wait()
            self.events.put(("finished", code))
        except OSError as exc:
            self.events.put(("fatal", f"无法启动鉴定流程：{exc}"))
        finally:
            self.process = None


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Pokémon GO 确定性鉴定改名助手")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    root: Path = project_root()
    if args.self_check:
        return self_check(root)
    enable_per_monitor_dpi()
    import tkinter as tk

    window = tk.Tk()
    DeterministicAppraisalRenamerApp(window, root)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
