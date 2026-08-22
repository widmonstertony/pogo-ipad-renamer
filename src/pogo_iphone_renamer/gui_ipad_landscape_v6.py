from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from .gui import fetch_json
from .gui_hdpi import enable_per_monitor_dpi, project_root, self_check
from .gui_ipad_landscape import friendly_ipad_landscape_event
from .gui_ipad_landscape_v5 import IPadLandscapeRenamerAppV5
from .gui_native import find_python_launcher


def collect_deterministic_status(settings: Any) -> dict[str, Any]:
    status: dict[str, Any] = {
        "mcp": {"ok": False, "detail": "未连接"},
        "ollama": {"ok": True, "detail": "RapidOCR + 像素测量"},
        "opencode": {"ok": False, "detail": "找不到 Python 3.13", "path": None},
    }
    try:
        health = fetch_json(settings.health_url)
        ok = health.get("status") == "ok"
        status["mcp"] = {
            "ok": ok,
            "detail": f"{health.get('server', 'iOS MCP')} {health.get('version', '')}".strip(),
        }
    except Exception as exc:
        status["mcp"]["detail"] = str(exc)
    launcher = find_python_launcher()
    if launcher:
        status["opencode"] = {
            "ok": True,
            "detail": (
                "Python 3.13 离线执行器"
                if os.name == "nt"
                else "本机 Python 离线执行器"
            ),
            "path": launcher,
        }
    return status


class IPadLandscapeRenamerAppV6(IPadLandscapeRenamerAppV5):
    def _rewrite_model_labels(self, widget: Any) -> None:
        try:
            text = str(widget.cget("text"))
            if text == "本地模型":
                widget.configure(text="本地识别")
            elif "本地 Qwen 视觉模型" in text:
                widget.configure(
                    text="纯 Python 离线识别 + Pokémon GO 自带鉴定页 · 不需要本地大模型"
                )
        except Exception:
            pass
        for child in widget.winfo_children():
            self._rewrite_model_labels(child)

    def _build_ui(self) -> None:
        super()._build_ui()
        self._rewrite_model_labels(self.window)
        self.model_var.set("RapidOCR + 像素测量（无需设置）")
        self._append_log("执行方式：纯 Python 本地 OCR + 像素测量；不启动 Ollama，不调用任何大模型。")
        self._append_log("名称旁铅笔按当前文字右边缘动态定位；三字、四字及更长名称不再共用固定横坐标。")

    def _status_worker(self) -> None:
        self.events.put(("status", collect_deterministic_status(self.settings)))

    def _apply_status(self, status: dict[str, Any]) -> None:
        self._set_status(self.mcp_status_var, status["mcp"]["ok"], status["mcp"]["detail"])
        self._set_status(
            self.ollama_status_var,
            True,
            "RapidOCR + 鉴定条像素测量",
        )
        self._set_status(
            self.opencode_status_var,
            status["opencode"]["ok"],
            status["opencode"]["detail"],
        )
        self.check_button.configure(state="normal" if not self.process else "disabled")
        if status["mcp"]["ok"] and status["opencode"]["ok"]:
            self._append_log("连接检查通过：iOS MCP、离线识别和 Python 执行器均已就绪。")
        else:
            self._append_log("连接检查未完全通过，请查看顶部状态。")

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
            "pogo_iphone_renamer.ipad_landscape_agent_v16",
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

    parser = argparse.ArgumentParser(description="Pokémon GO 纯 Python 横屏整理助手 v16")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    root: Path = project_root()
    if args.self_check:
        status = collect_deterministic_status(
            __import__("pogo_iphone_renamer.gui", fromlist=["load_settings"]).load_settings(root)
        )
        print(status)
        return 0 if status["mcp"]["ok"] and status["opencode"]["ok"] else 1
    enable_per_monitor_dpi()
    window = tk.Tk()
    IPadLandscapeRenamerAppV6(window, root)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
