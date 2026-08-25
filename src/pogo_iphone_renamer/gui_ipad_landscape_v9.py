from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from .background_batch_runner import background_run_is_active, request_background_stop
from .batch_pause import BatchPauseFile
from .gui import AppSettings, save_settings
from .gui_hdpi import enable_per_monitor_dpi, project_root
from .gui_ipad_landscape import friendly_ipad_landscape_event
from .gui_ipad_landscape_v6 import collect_deterministic_status
from .gui_ipad_landscape_v8 import IPadLandscapeRenamerAppV8
from .gui_native import python_worker_command
from .live_activity import live_activity_paths


def batch_progress_event(line: str) -> dict[str, object] | None:
    try:
        event = json.loads(line.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(event, dict) or event.get("type") != "progress":
        return None
    return event


def background_runner_command(
    python_command: list[str], *, mode: str, root: Path
) -> list[str]:
    """Start a runner that is independent from the lifetime of the Tk window."""

    return [
        *python_command,
        "-u",
        "-m",
        "pogo_iphone_renamer.background_batch_runner",
        "--mode",
        mode,
        "--root",
        str(root),
    ]


class IPadLandscapeRenamerAppV9(IPadLandscapeRenamerAppV8):
    _LIVE_LOG_NAME = "gui-live.log"

    def _batch_state_path(self) -> Path:
        return self.root / ".pogo-data" / "batch-state.json"

    def _background_log_path(self) -> Path:
        return self.root / ".pogo-data" / "background-worker.log"

    def _live_activity_paths(self) -> tuple[Path, Path]:
        return live_activity_paths(self.root)

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
        self._append_log("连续黑帧只读等待；为保留当前详情页，不会返回主屏幕、重新打开或重启游戏。")
        self._append_log("截图方向自动兼容：新版 MCP 原生 1366×1024 与旧版旋转帧均可识别。")
        self._append_log("锁屏即安全挂起：即使锁屏截图不是黑色，也会等待你手动解锁后原位继续。")
        self._append_log("批次由独立后台进程持有；Mac 锁屏或关闭窗口后继续运行，结束或停止后恢复电源策略。")
        self._install_live_monitor()
        self._install_accessible_batch_controls()
        if background_run_is_active(self._batch_state_path()):
            self._set_running(True, True)
            self._append_log("检测到已在后台运行的批量任务；本窗口可查看记录或立即停止。")
        self.window.after(250, self._refresh_live_monitor)

    def _install_live_monitor(self) -> None:
        """Add a live, non-interactive mirror of the active iPad work."""

        self.live_run_var = self.tk.StringVar(value="任务：读取后台状态…")
        self.live_pokemon_var = self.tk.StringVar(value="当前宝可梦：等待详情身份确认")
        self.live_page_var = self.tk.StringVar(value="当前画面：等待 iPad 截图")
        self.live_step_var = self.tk.StringVar(value="当前步骤：等待工作进程")
        self.live_iv_var = self.tk.StringVar(value="IV / 昵称：尚未读取")
        self.live_updated_var = self.tk.StringVar(value="画面更新时间：尚未收到")
        self._live_preview_mtime = -1
        self._live_preview_photo = None

        # ``gui.RenamerApp`` intentionally keeps its log card local; the
        # existing Text widget is the stable public handle to that container.
        log_card = self.log.master
        monitor = self.ttk.Frame(log_card, style="Card.TFrame", padding=10)
        monitor.pack(fill="x", pady=(0, 10), before=self.log)
        monitor.columnconfigure(0, minsize=184)
        monitor.columnconfigure(1, weight=1)
        self.live_preview_label = self.tk.Label(
            monitor,
            text="等待工作进程\n首张 iPad 画面…",
            bg="#07101f",
            fg="#94a3b8",
            justify="center",
            width=23,
            height=12,
        )
        self.live_preview_label.grid(row=0, column=0, rowspan=6, sticky="nsw", padx=(0, 14))
        self.ttk.Label(monitor, text="实时 iPad 画面与操作", style="CardTitle.TLabel").grid(
            row=0, column=1, sticky="w"
        )
        for row, variable in enumerate(
            (
                self.live_run_var,
                self.live_pokemon_var,
                self.live_page_var,
                self.live_step_var,
                self.live_iv_var,
                self.live_updated_var,
            ),
            start=1,
        ):
            self.ttk.Label(
                monitor,
                textvariable=variable,
                style="CardText.TLabel",
                wraplength=610,
                justify="left",
            ).grid(row=row, column=1, sticky="w", pady=(3, 0))

    @staticmethod
    def _read_live_json(path: Path) -> dict[str, object]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _screen_text(value: object) -> str:
        states = {
            "DETAIL": "宝可梦详情页",
            "DETAIL_MENU": "详情菜单",
            "APPRAISAL": "鉴定页",
            "APPRAISAL_BARS": "鉴定条页面",
            "RENAME_DIALOG": "改名输入框",
            "MAP": "游戏地图",
            "MAIN_MENU": "精灵球主菜单",
            "INVENTORY": "宝可梦盒",
        }
        raw = str(value or "等待识别")
        return states.get(raw, raw)

    def _refresh_live_monitor(self) -> None:
        """Poll only local worker artifacts; this never asks MCP for another frame."""

        activity_path, preview_path = self._live_activity_paths()
        activity = self._read_live_json(activity_path)
        state = self._read_live_json(self._batch_state_path())
        progress = activity.get("progress")
        if not isinstance(progress, dict):
            progress = state.get("progress") if isinstance(state.get("progress"), dict) else {}
        current = progress.get("current")
        phase = progress.get("phase")
        state_text = str(state.get("status", "待机"))
        position = f"第 {current} 只" if current else "尚未开始"
        self.live_run_var.set(f"任务：{state_text} · {position} · {phase or '等待下一步'}")
        if current:
            counts = (
                f"改名 {int(progress.get('renamed', 0) or 0)} · "
                f"已命名跳过 {int(progress.get('skipped', 0) or 0)} · "
                f"暂不可读保留 {int(progress.get('unreadable', 0) or 0)}"
            )
            self.progress_var.set(f"进度：{position} · {phase or '处理中'} · {counts}")

        pokemon = activity.get("pokemon")
        if isinstance(pokemon, dict):
            name = str(pokemon.get("name", "等待详情身份确认"))
            kind = "默认名" if pokemon.get("is_default") else "已有自定义昵称"
            self.live_pokemon_var.set(f"当前宝可梦：{name}（{kind}）")
        self.live_page_var.set(f"当前画面：{self._screen_text(activity.get('screen'))}")
        step = str(activity.get("step", "等待工作进程事件")).strip()
        self.live_step_var.set(f"当前步骤：{step}")

        iv = activity.get("iv")
        nickname = str(activity.get("nickname", "")).strip()
        if isinstance(iv, dict) and all(key in iv for key in ("attack", "defense", "stamina")):
            summary = f"A/D/S={iv['attack']}/{iv['defense']}/{iv['stamina']}"
            if iv.get("percent") is not None:
                summary += f" · IV={iv['percent']}%"
            if nickname:
                summary += f" · 目标昵称：{nickname}"
            self.live_iv_var.set(f"IV / 昵称：{summary}")
        elif nickname:
            self.live_iv_var.set(f"IV / 昵称：{nickname}")

        updated = str(activity.get("updated_at", "")).replace("T", " ").replace("+00:00", " UTC")
        self.live_updated_var.set(f"画面/状态更新时间：{updated or '等待首张截图'}")
        try:
            modified = preview_path.stat().st_mtime_ns
            if modified != self._live_preview_mtime:
                from PIL import Image, ImageTk

                with Image.open(preview_path) as source:
                    image = source.copy()
                image.thumbnail((170, 228), Image.Resampling.LANCZOS)
                self._live_preview_photo = ImageTk.PhotoImage(image)
                self.live_preview_label.configure(image=self._live_preview_photo, text="", width=1, height=1)
                self._live_preview_mtime = modified
        except OSError:
            pass
        try:
            self.window.after(750, self._refresh_live_monitor)
        except self.tk.TclError:
            return

    def _append_log(self, message: str) -> None:
        """Show a message in Tk and preserve an inspectable local run record.

        macOS Accessibility can expose the native menu while omitting Tk's
        scrolling text widget.  Mirroring the same user-visible line gives a
        launcher or support session a reliable audit trail without changing the
        batch worker or its safety decisions.
        """

        super()._append_log(message)
        try:
            log_path = self.root / ".pogo-data" / self._LIVE_LOG_NAME
            log_path.parent.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%H:%M:%S")
            with log_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"[{timestamp}] {message}\n")
        except OSError:
            # Failure to preserve diagnostics must never prevent safe UI use.
            pass

    def _install_accessible_batch_controls(self) -> None:
        """Expose batch commands through the native macOS menu bar.

        Some Tk builds omit child widgets from macOS Accessibility, while menu
        items remain reliably visible to keyboard and computer-control tools.
        These commands use the exact same guarded start/pause code as the
        visible buttons; they are an accessible UI path, not a separate runner.
        """

        menu_bar = self.tk.Menu(self.window)
        batch_menu = self.tk.Menu(menu_bar, tearoff=False)
        batch_menu.add_command(
            label="开始不限量批量改名",
            accelerator="⌃⇧R",
            command=self._start_unlimited_from_menu,
        )
        batch_menu.add_command(
            label="开始不限量只读扫描",
            accelerator="⌃⇧S",
            command=self._start_unlimited_scan_from_menu,
        )
        batch_menu.add_separator()
        batch_menu.add_command(label="安全暂停／继续", command=self.toggle_pause)
        menu_bar.add_cascade(label="批量", menu=batch_menu)
        self.window.configure(menu=menu_bar)
        self.window.bind_all("<Control-Shift-R>", self._on_unlimited_rename_shortcut)
        self.window.bind_all("<Control-Shift-S>", self._on_unlimited_scan_shortcut)

    def _on_unlimited_rename_shortcut(self, _event=None) -> str:
        self._start_unlimited_from_menu()
        return "break"

    def _on_unlimited_scan_shortcut(self, _event=None) -> str:
        self._start_unlimited_scan_from_menu()
        return "break"

    def _start_unlimited_from_menu(self) -> None:
        self.unlimited_var.set(True)
        self.start_run(True)

    def _start_unlimited_scan_from_menu(self) -> None:
        self.unlimited_var.set(True)
        self.start_run(False)

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
        if background_run_is_active(self._batch_state_path()):
            self.messagebox.showinfo(
                "后台任务正在运行",
                "已有批量任务在后台持续运行。锁屏或关闭窗口不会停止它；"
                "请先重新打开控制窗口后查看记录，或使用“立即停止”。",
                parent=self.window,
            )
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
        try:
            status = collect_deterministic_status(self.settings)
            self.events.put(("status", status))
            missing = [name for name in ("mcp", "opencode") if not status[name]["ok"]]
            if missing:
                self.events.put(("fatal", "启动失败：" + "、".join(missing) + " 尚未就绪。"))
                return
            mode = "rename" if write_enabled else "scan"
            command = background_runner_command(
                python_worker_command(str(status["opencode"]["path"])),
                mode=mode,
                root=self.root,
            )
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
                "POGO_BACKGROUND_LOG": str(self._background_log_path()),
                "POGO_BATCH_STATE": str(self._batch_state_path()),
                "POGO_LIVE_ACTIVITY_PATH": str(self._live_activity_paths()[0]),
                "POGO_LIVE_PREVIEW_PATH": str(self._live_activity_paths()[1]),
                # The visible app uses the same direct-detail route as the
                # headless continuation: never leave the user-opened Pokémon
                # detail page and never relaunch the game after a transient
                # MCP interruption.
                "POGO_START_FROM_CURRENT_DETAIL": "true",
                "POGO_ALLOW_GAME_RESTART": "false",
                "POGO_PERSIST_CAPTURE_WAIT": "true",
                }
            )
            background_log = self._background_log_path()
            background_log.parent.mkdir(parents=True, exist_ok=True)
            background_log.touch(exist_ok=True)
            cursor = background_log.stat().st_size
            creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            if os.name == "nt":
                creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            self.process = subprocess.Popen(
                command,
                cwd=self.root,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creation_flags,
                start_new_session=os.name != "nt",
            )
            self.events.put(
                (
                    "log",
                    "后台批量进程已独立启动；Mac 锁屏或关闭此窗口后任务仍会继续。",
                )
            )
            threading.Thread(
                target=self._relay_background_log,
                args=(self.process, background_log, cursor),
                daemon=True,
            ).start()
            code = self.process.wait()
            self.events.put(("finished", code))
        except OSError as exc:
            self.events.put(("fatal", f"无法启动纯 Python 批量流程：{exc}"))
        finally:
            self.pause_control.resume()
            self.process = None

    def _relay_background_log(
        self, process: subprocess.Popen[str], path: Path, cursor: int
    ) -> None:
        """Mirror detached-runner records into the visible Tk log while open."""

        try:
            with path.open("r", encoding="utf-8", errors="replace") as handle:
                handle.seek(cursor)
                while True:
                    line = handle.readline()
                    if line:
                        _, separator, message = line.partition("] ")
                        self.events.put(("log", (message if separator else line).rstrip()))
                        continue
                    if process.poll() is not None:
                        return
                    time.sleep(0.15)
        except OSError:
            # The detached worker remains authoritative even if the display
            # relay cannot reopen its diagnostic file.
            return

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
        if self.process and self.process.poll() is None:
            super().stop_run()
            return
        if request_background_stop(self._batch_state_path()):
            self._append_log("已请求后台任务安全停止；它会关闭当前控制并恢复电源策略。")
            self._set_running(False, False)

    def _on_close(self) -> None:
        if self.process and self.process.poll() is None:
            keep_running = self.messagebox.askyesno(
                "后台任务仍在运行",
                "关闭窗口不会停止当前批量任务；它会在锁屏后继续运行。\n\n关闭控制窗口吗？",
                parent=self.window,
            )
            if not keep_running:
                return
        self.window.destroy()


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
