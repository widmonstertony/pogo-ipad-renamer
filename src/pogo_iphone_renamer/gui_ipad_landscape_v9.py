from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path

from .batch_pause import BatchPauseFile
from .gui import AppSettings, save_settings
from .gui_hdpi import enable_per_monitor_dpi, project_root
from .gui_ipad_landscape import friendly_ipad_landscape_event
from .gui_ipad_landscape_v6 import collect_deterministic_status
from .gui_ipad_landscape_v8 import IPadLandscapeRenamerAppV8
from .gui_native import python_worker_command
from .power_awake import AwakeGuard


def batch_progress_event(line: str) -> dict[str, object] | None:
    try:
        event = json.loads(line.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(event, dict) or event.get("type") != "progress":
        return None
    return event


class IPadLandscapeRenamerAppV9(IPadLandscapeRenamerAppV8):
    def _build_ui(self) -> None:
        super()._build_ui()
        # The inherited single-Pokemon UI deliberately forces this variable to
        # 1.  Batch v9 must restore the persisted value after that base setup;
        # otherwise every application launch silently rewrites a saved 100 to
        # 1 during the automatic connection check.
        self.batch_var.set(self.settings.batch_limit)
        self.pause_control = BatchPauseFile(
            self.root / ".pogo-data" / "batch.pause"
        )
        self.unlimited_var = self.tk.BooleanVar(value=self.settings.unlimited)
        self.batch_label.configure(text="停止数量")
        self.unlimited_check = self.ttk.Checkbutton(
            self.settings_card,
            text="不限（直到盒子末尾）",
            variable=self.unlimited_var,
            command=self._sync_limit_input,
        )
        self.unlimited_check.grid(row=0, column=6, sticky="w", padx=(12, 0))
        self._sync_limit_input()

        self.progress_var = self.tk.StringVar(value="进度：尚未开始")
        self.ttk.Label(
            self.log_header,
            textvariable=self.progress_var,
            style="CardText.TLabel",
        ).pack(side="left", padx=(18, 0))
        self.pause_button = self.ttk.Button(
            self.controls,
            text="安全暂停",
            style="Secondary.TButton",
            command=self.toggle_pause,
            state="disabled",
        )
        self.pause_button.pack(side="right", padx=(0, 10))
        self.read_button.configure(text="批量扫描（不改名）")
        self.rename_button.configure(text="批量鉴定并安全改名")
        self._append_log("批量模式：已有昵称保留并自动翻到下一只；默认名才进入改名流程。")
        self._append_log("默认不限数量；持续到盒子末尾、手动停止或安全条件不再满足。")
        self._append_log("安全暂停会先完成当前一只并回到详情页；继续时先复核同一只。")
        self._append_log("鉴定对白稳定且未显示 IV 条时只推进一次；绝不连续猜点。")
        self._append_log("IV 每格宽度由画面中的 5/10 分段白线动态反算；不再使用固定左边界。")
        self._append_log(
            "每只 IV 均由端点与 15 格占用双解码；只接受三张像素不同且未被上一只使用的帧。"
        )
        self._append_log("详情页与鉴定页必须独立读到同一默认物种名；MCP 旧缓存帧不能授权改名。")
        self._append_log("名称铅笔采用多尺度 OCR；缺框时使用 iPad14,6 真机字体标定后备。")
        self._append_log("昵称同时遵守 12 字符与 24 UTF-8 字节限制；长物种名会保留可提交的最长前缀。")
        self._append_log("单只鉴定条持续不可读时保留原名，安全返回详情并继续下一只。")
        self._append_log("OK 提交会先只读等待慢响应；重试前重新逐字核验昵称，最多四次。")
        self._append_log("连续黑帧先验证主屏；必要时仅做一次电源唤醒重置，再决定是否重启游戏。")
        self._append_log("截图方向自动兼容：新版 MCP 原生 1366×1024 与旧版旋转帧均可识别。")
        self._append_log("锁屏即安全挂起：即使锁屏截图不是黑色，也会等待你手动解锁后原位继续。")
        self._append_log("批次运行期间电脑与显示器保持唤醒；结束或停止后自动恢复原电源策略。")

    def _sync_limit_input(self) -> None:
        running = bool(getattr(self, "_batch_running", False))
        state = "disabled" if running or self.unlimited_var.get() else "normal"
        self.batch_input.configure(state=state)

    def _read_form(self) -> AppSettings:
        settings = AppSettings(
            mcp_url=self.mcp_var.get().strip().rstrip("/"),
            ollama_url=self.settings.ollama_url,
            model=self.model_var.get().strip(),
            batch_limit=int(self.batch_var.get()),
            unlimited=bool(self.unlimited_var.get()),
        )
        settings.validate()
        return settings

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
            scope = (
                "不限数量，直到盒子末尾或你停止"
                if self.settings.unlimited
                else f"最多检查 {self.settings.batch_limit} 只宝可梦"
            )
            confirmed = self.messagebox.askyesno(
                "确认批量鉴定并改名",
                f"本次{scope}。\n\n"
                "已有自定义/IV昵称会原样保留并自动继续下一只；"
                "只有完整繁中默认物种名才会鉴定并改名。\n"
                "每次翻页都会验证身份变化，不会传送、强化或进化。\n\n确认开始吗？",
                icon="warning",
                parent=self.window,
            )
            if not confirmed:
                return
        self.pause_control.resume()
        self.progress_var.set("进度：准备开始")
        self._set_running(True, write_enabled)
        self._append_log("—" * 42)
        scope = "不限数量" if self.settings.unlimited else f"最多 {self.settings.batch_limit} 只"
        self._append_log(
            f"开始批量{'鉴定改名' if write_enabled else '只读扫描'}，{scope}…"
        )
        threading.Thread(target=self._run_worker, args=(write_enabled,), daemon=True).start()

    def _run_worker(self, write_enabled: bool) -> None:
        awake = AwakeGuard()
        awake_description: str | None = None
        try:
            awake_description = awake.acquire()
            if awake_description:
                self.events.put(("log", awake_description + "。"))
        except OSError as exc:
            self.events.put(("fatal", f"无法启用批次防睡眠，任务未启动：{exc}"))
            return
        try:
            status = collect_deterministic_status(self.settings)
            self.events.put(("status", status))
            missing = [name for name in ("mcp", "opencode") if not status[name]["ok"]]
            if missing:
                self.events.put(("fatal", "启动失败：" + "、".join(missing) + " 尚未就绪。"))
                return
            command = python_worker_command(str(status["opencode"]["path"])) + [
            "-u",
            "-m",
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26",
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
                # The policy uses this switch for every touch, including read-only
                # navigation into the appraisal panel.  Scan mode stays safe because
                # its deterministic worker never exposes rename/input/submit calls.
                "POGO_WRITE_ENABLED": "true",
                "POGO_BATCH_LIMIT": "0" if self.settings.unlimited else str(self.settings.batch_limit),
                "POGO_OBSERVATION_TTL_SECONDS": "120",
                "POGO_JOURNAL_PATH": str(self.root / ".pogo-data" / "actions.jsonl"),
                "POGO_PAUSE_FILE": str(self.pause_control.path),
                }
            )
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
                progress = batch_progress_event(line)
                if progress is not None:
                    self.events.put(("progress", progress))
                message = friendly_ipad_landscape_event(line)
                if message:
                    self.events.put(("log", message))
            code = self.process.wait()
            self.events.put(("finished", code))
        except OSError as exc:
            self.events.put(("fatal", f"无法启动纯 Python 批量流程：{exc}"))
        finally:
            self.pause_control.resume()
            self.process = None
            awake.release()
            if awake_description:
                self.events.put(("log", "电脑防睡眠已释放；系统电源策略已恢复。"))

    def toggle_pause(self) -> None:
        if self.pause_control.requested:
            self.pause_control.resume()
            self.pause_button.configure(text="安全暂停")
            self.run_state_var.set("● 正在安全继续…")
            self._append_log("已请求继续；程序会先复核当前宝可梦身份。")
            return
        self.pause_control.request()
        self.pause_button.configure(text="继续运行")
        self.run_state_var.set("○ 等待当前一只完成后暂停")
        self._append_log("已请求安全暂停；不会停在键盘或改名弹窗中。")

    def _apply_progress(self, progress: dict[str, object]) -> None:
        current = int(progress.get("current", 0))
        limit = progress.get("limit")
        phase = str(progress.get("phase", "processing"))
        position = f"第 {current} 只" if limit is None else f"第 {current}/{int(limit)} 只"
        counts = (
            f"改名 {int(progress.get('renamed', 0))} · "
            f"已命名跳过 {int(progress.get('skipped', 0))} · "
            f"暂不可读保留 {int(progress.get('unreadable', 0))}"
        )
        phase_text = {
            "processing": "处理中",
            "completed": "已完成",
            "paused": "已暂停",
            "resumed": "已继续",
        }.get(phase, phase)
        self.progress_var.set(f"进度：{position} · {phase_text} · {counts}")
        if phase == "paused":
            self.run_state_var.set(f"● 已安全暂停在第 {current} 只后")
            self.pause_button.configure(text="继续运行")
        elif phase == "resumed":
            self.run_state_var.set("● 已继续批量任务")
            self.pause_button.configure(text="安全暂停")

    def _set_running(self, running: bool, write_enabled: bool) -> None:
        self._batch_running = running
        super()._set_running(running, write_enabled)
        if hasattr(self, "pause_button"):
            self.pause_button.configure(
                state="normal" if running else "disabled",
                text="安全暂停",
            )
        if hasattr(self, "unlimited_check"):
            self.unlimited_check.configure(state="disabled" if running else "normal")
            self._sync_limit_input()
        if not running and hasattr(self, "pause_control"):
            self.pause_control.resume()

    def stop_run(self) -> None:
        super().stop_run()


def main(argv: list[str] | None = None) -> int:
    import argparse
    import tkinter as tk

    parser = argparse.ArgumentParser(description="Pokémon GO 纯 Python 批量整理助手 v26")
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
    IPadLandscapeRenamerAppV9(window, root)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
