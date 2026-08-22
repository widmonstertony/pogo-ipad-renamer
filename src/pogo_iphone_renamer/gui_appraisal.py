from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from typing import Any

from .gui import save_settings
from .gui_hdpi import HiDpiRenamerApp, enable_per_monitor_dpi, project_root, self_check
from .gui_native import collect_native_status


STATE_TEXT = {
    "MAP": "识别到地图，正在打开主菜单…",
    "MAIN_MENU": "正在进入宝可梦盒…",
    "INVENTORY": "正在打开第一只可见宝可梦…",
    "DETAIL": "已进入详情页，正在打开鉴定…",
    "DETAIL_MENU": "正在选择“鉴定”…",
    "APPRAISAL": "鉴定页已打开，正在读取攻击/防御/体力…",
    "RENAME_DIALOG": "改名窗口已打开，正在安全提交昵称…",
    "UNKNOWN": "无法确认当前页面。",
}


def friendly_appraisal_event(line: str) -> str | None:
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
    if event_type == "navigation":
        return STATE_TEXT.get(str(event.get("state", "UNKNOWN")), "正在识别游戏页面…")
    if event_type == "pokemon":
        return (
            f"识别完成：{event.get('species')}  "
            f"A/D/S={event.get('attack')}/{event.get('defense')}/{event.get('stamina')}  "
            f"IV={event.get('percent')}%  →  {event.get('nickname')}  "
            f"置信度={float(event.get('confidence', 0)):.0%}"
        )
    if event_type == "renamed":
        return f"✓ 改名并核验成功：{event.get('nickname')}"
    if event_type in {"status", "finished", "error"}:
        return str(event.get("message", "")).strip() or None
    return None


class AppraisalRenamerApp(HiDpiRenamerApp):
    def _replace_text(self, widget: Any) -> None:
        try:
            text = str(widget.cget("text"))
            if "Poke Genie 名称原样保留" in text:
                widget.configure(text="本地 Qwen 视觉模型 + Pokémon GO 自带鉴定页 · 不依赖 Poke Genie")
            elif text == "单批上限":
                widget.configure(text="计划批量")
        except Exception:
            pass
        for child in widget.winfo_children():
            self._replace_text(child)

    def _build_ui(self) -> None:
        super()._build_ui()
        self._replace_text(self.window)
        self.read_button.configure(text="扫描当前一只（不改名）")
        self.rename_button.configure(text="鉴定并改名当前一只")
        self._clear_log()
        self._append_log("就绪。扫描会在 Pokémon GO 内导航到“鉴定”页并读取 A/D/S；不需要 Poke Genie。")
        self._append_log("扫描模式只导航和读取，不输入昵称；改名模式需要单独确认。")

    def _status_worker(self) -> None:
        self.events.put(("status", collect_native_status(self.settings)))

    def start_run(self, write_enabled: bool) -> None:
        if self.process and self.process.poll() is None:
            self.messagebox.showinfo("任务正在运行", "请先停止当前任务。", parent=self.window)
            return
        try:
            self.settings = self._read_form()
            save_settings(self.root, self.settings)
        except (ValueError, OSError) as exc:
            self.messagebox.showerror("设置有误", str(exc), parent=self.window)
            return
        if write_enabled:
            confirmed = self.messagebox.askyesno(
                "确认鉴定并改名",
                "本次只处理当前一只宝可梦。\n\n"
                "程序会打开 Pokémon GO 自带鉴定页，读取 A/D/S，生成“物种名+圆圈IV+上标百分比”。\n"
                "已有自定义昵称会保留；不会传送、强化或进化。\n\n确认开始吗？",
                icon="warning",
                parent=self.window,
            )
            if not confirmed:
                return
        self._set_running(True, write_enabled)
        self._append_log("—" * 42)
        self._append_log("开始鉴定当前一只…")
        threading.Thread(target=self._run_worker, args=(write_enabled,), daemon=True).start()

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
            "pogo_iphone_renamer.appraisal_agent",
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
                # Scan needs navigation taps, but the scan worker has no code path
                # for text input and never receives a rename approval.
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
                message = friendly_appraisal_event(line)
                if message:
                    self.events.put(("log", message))
            code = self.process.wait()
            self.events.put(("finished", code))
        except OSError as exc:
            self.events.put(("fatal", f"无法启动鉴定流程：{exc}"))
        finally:
            self.process = None

    def _set_running(self, running: bool, write_enabled: bool) -> None:
        super()._set_running(running, write_enabled)
        if running:
            self.run_state_var.set("● 正在鉴定并改名" if write_enabled else "● 正在扫描鉴定")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Pokémon GO 鉴定改名助手")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args(argv)
    root: Path = project_root()
    if args.self_check:
        return self_check(root)
    enable_per_monitor_dpi()
    import tkinter as tk

    window = tk.Tk()
    AppraisalRenamerApp(window, root)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

