from __future__ import annotations

import argparse
import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import BATCH_LIMIT_MIN
from .prompts import READ_ONLY_PROMPT, rename_prompt


APP_TITLE = "Pokémon GO 整理助手"
DEFAULT_MCP_URL = "http://192.168.68.67:8090/mcp"
DEFAULT_MODEL = "qwen3.8:27b"


def project_root() -> Path:
    override = os.getenv("POGO_APP_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    if getattr(sys, "frozen", False):
        executable_dir = Path(sys.executable).resolve().parent
        for candidate in (executable_dir, executable_dir.parent):
            if (candidate / "desktop" / "opencode.jsonc").is_file():
                return candidate
    return Path(__file__).resolve().parents[2]


@dataclass
class AppSettings:
    mcp_url: str = DEFAULT_MCP_URL
    ollama_url: str = "http://127.0.0.1:11434"
    model: str = DEFAULT_MODEL
    batch_limit: int = 20
    unlimited: bool = True

    @property
    def health_url(self) -> str:
        return self.mcp_url.rstrip("/").removesuffix("/mcp") + "/health"

    def validate(self) -> None:
        if not self.mcp_url.startswith(("http://", "https://")):
            raise ValueError("MCP 地址必须以 http:// 或 https:// 开头")
        if not self.mcp_url.rstrip("/").endswith("/mcp"):
            raise ValueError("MCP 地址必须以 /mcp 结尾")
        if not self.ollama_url.startswith(("http://", "https://")):
            raise ValueError("Ollama 地址无效")
        if not self.model.strip():
            raise ValueError("模型名称不能为空")
        if self.batch_limit < BATCH_LIMIT_MIN:
            raise ValueError(f"有限模式的停止数量必须至少为 {BATCH_LIMIT_MIN}")


def settings_path(root: Path) -> Path:
    return root / ".pogo-data" / "gui-settings.json"


def load_settings(root: Path) -> AppSettings:
    path = settings_path(root)
    if not path.exists():
        return AppSettings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        settings = AppSettings(**{k: data[k] for k in asdict(AppSettings()) if k in data})
        settings.validate()
        return settings
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return AppSettings()


def save_settings(root: Path, settings: AppSettings) -> None:
    settings.validate()
    path = settings_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(asdict(settings), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def fetch_json(url: str, timeout: float = 3.0) -> dict[str, Any]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def find_opencode_binary() -> str | None:
    direct = shutil.which("opencode.exe")
    if direct:
        return direct
    shim = shutil.which("opencode.cmd") or shutil.which("opencode")
    if not shim:
        return None
    shim_path = Path(shim).resolve()
    native = shim_path.parent / "node_modules" / "opencode-ai" / "bin" / "opencode.exe"
    if native.is_file():
        return str(native)
    return str(shim_path) if shim_path.suffix.lower() == ".exe" else None


def collect_status(settings: AppSettings) -> dict[str, Any]:
    result: dict[str, Any] = {
        "mcp": {"ok": False, "detail": "未连接"},
        "ollama": {"ok": False, "detail": "未连接"},
        "opencode": {"ok": False, "detail": "未安装"},
    }
    try:
        health = fetch_json(settings.health_url)
        ok = health.get("status") == "ok"
        result["mcp"] = {
            "ok": ok,
            "detail": f"{health.get('server', 'iOS MCP')} {health.get('version', '')}".strip(),
        }
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        result["mcp"]["detail"] = str(exc)

    try:
        tags = fetch_json(settings.ollama_url.rstrip("/") + "/api/tags")
        models = {
            str(item.get("name", ""))
            for item in tags.get("models", [])
            if isinstance(item, dict)
        }
        present = settings.model in models
        result["ollama"] = {
            "ok": present,
            "detail": f"{settings.model} 已就绪" if present else f"未找到 {settings.model}",
            "models": sorted(models),
        }
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError) as exc:
        result["ollama"]["detail"] = str(exc)

    binary = find_opencode_binary()
    if binary:
        result["opencode"] = {"ok": True, "detail": Path(binary).name, "path": binary}
    return result


def friendly_event(line: str) -> str | None:
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
    part = event.get("part") if isinstance(event.get("part"), dict) else {}
    text = part.get("text") or event.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    tool = part.get("tool") or event.get("tool") or part.get("name")
    if tool and event_type in {"tool_use", "tool", "tool-call", "tool_call"}:
        return f"正在调用：{tool}"
    if event_type in {"step_start", "step-start"}:
        return "模型正在分析当前屏幕…"
    if event_type in {"step_finish", "step-finish"}:
        return "本轮分析完成。"
    if event_type == "error":
        return "错误：" + str(event.get("error") or event.get("message") or event)
    return None


class RenamerApp:
    def __init__(self, root_window: Any, app_root: Path):
        import tkinter as tk
        from tkinter import messagebox, ttk

        self.tk = tk
        self.ttk = ttk
        self.messagebox = messagebox
        self.window = root_window
        self.root = app_root
        self.settings = load_settings(app_root)
        self.process: subprocess.Popen[str] | None = None
        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()

        self.mcp_var = tk.StringVar(value=self.settings.mcp_url)
        self.model_var = tk.StringVar(value=self.settings.model)
        self.batch_var = tk.IntVar(value=self.settings.batch_limit)
        self.run_state_var = tk.StringVar(value="待机 · 写操作关闭")
        self.mcp_status_var = tk.StringVar(value="○ 正在检查")
        self.ollama_status_var = tk.StringVar(value="○ 正在检查")
        self.opencode_status_var = tk.StringVar(value="○ 正在检查")

        self._configure_window()
        self._build_ui()
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.after(100, self._drain_events)
        self.check_connections()

    def _configure_window(self) -> None:
        self.window.title(APP_TITLE)
        self.window.geometry("1020x720")
        self.window.minsize(880, 620)
        self.window.configure(bg="#0b1220")
        try:
            self.window.tk.call("tk", "scaling", 1.25)
        except Exception:
            pass
        style = self.ttk.Style(self.window)
        style.theme_use("clam")
        style.configure("App.TFrame", background="#0b1220")
        style.configure("Card.TFrame", background="#152238")
        style.configure("Title.TLabel", background="#0b1220", foreground="#f8fafc", font=("Segoe UI", 22, "bold"))
        style.configure("Subtitle.TLabel", background="#0b1220", foreground="#94a3b8", font=("Segoe UI", 10))
        style.configure("CardTitle.TLabel", background="#152238", foreground="#e2e8f0", font=("Segoe UI", 11, "bold"))
        style.configure("CardText.TLabel", background="#152238", foreground="#a7b6ca", font=("Segoe UI", 10))
        style.configure("Good.TLabel", background="#152238", foreground="#34d399", font=("Segoe UI", 10, "bold"))
        style.configure("Bad.TLabel", background="#152238", foreground="#fb7185", font=("Segoe UI", 10, "bold"))
        style.configure("Idle.TLabel", background="#152238", foreground="#fbbf24", font=("Segoe UI", 10, "bold"))
        style.configure("Primary.TButton", font=("Segoe UI", 11, "bold"), padding=(18, 12), background="#2563eb", foreground="white")
        style.map("Primary.TButton", background=[("active", "#3b82f6"), ("disabled", "#334155")])
        style.configure("Success.TButton", font=("Segoe UI", 11, "bold"), padding=(18, 12), background="#059669", foreground="white")
        style.map("Success.TButton", background=[("active", "#10b981"), ("disabled", "#334155")])
        style.configure("Danger.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 10), background="#be123c", foreground="white")
        style.map("Danger.TButton", background=[("active", "#e11d48"), ("disabled", "#334155")])
        style.configure("Secondary.TButton", font=("Segoe UI", 10), padding=(12, 9), background="#334155", foreground="#e2e8f0")
        style.map("Secondary.TButton", background=[("active", "#475569")])
        style.configure("TEntry", fieldbackground="#0f172a", foreground="#e2e8f0", insertcolor="white", padding=8)
        style.configure("TSpinbox", fieldbackground="#0f172a", foreground="#e2e8f0", arrowsize=16, padding=8)

    def _build_ui(self) -> None:
        outer = self.ttk.Frame(self.window, style="App.TFrame", padding=24)
        outer.pack(fill="both", expand=True)

        self.ttk.Label(outer, text=APP_TITLE, style="Title.TLabel").pack(anchor="w")
        self.ttk.Label(
            outer,
            text="本地 Qwen 模型 + iPhone MCP · 只改默认名称 · Poke Genie 名称原样保留",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(3, 18))

        status_card = self.ttk.Frame(outer, style="Card.TFrame", padding=16)
        status_card.pack(fill="x")
        for column in range(4):
            status_card.columnconfigure(column, weight=1)
        self._status_block(status_card, 0, "iPhone MCP", self.mcp_status_var)
        self._status_block(status_card, 1, "本地模型", self.ollama_status_var)
        self._status_block(status_card, 2, "执行引擎", self.opencode_status_var)
        self._status_block(status_card, 3, "当前状态", self.run_state_var)

        self.settings_card = self.ttk.Frame(outer, style="Card.TFrame", padding=16)
        self.settings_card.pack(fill="x", pady=14)
        self.settings_card.columnconfigure(1, weight=3)
        self.settings_card.columnconfigure(3, weight=2)
        self.ttk.Label(self.settings_card, text="iPhone MCP", style="CardTitle.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 10))
        self.ttk.Entry(self.settings_card, textvariable=self.mcp_var).grid(row=0, column=1, sticky="ew", padx=(0, 18))
        self.ttk.Label(self.settings_card, text="本地模型", style="CardTitle.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 10))
        self.ttk.Entry(self.settings_card, textvariable=self.model_var, width=20).grid(row=0, column=3, sticky="ew", padx=(0, 18))
        self.batch_label = self.ttk.Label(self.settings_card, text="停止数量", style="CardTitle.TLabel")
        self.batch_label.grid(row=0, column=4, sticky="w", padx=(0, 10))
        self.batch_input = self.ttk.Entry(
            self.settings_card,
            textvariable=self.batch_var,
            width=8,
        )
        self.batch_input.grid(row=0, column=5, sticky="w")

        self.controls = self.ttk.Frame(outer, style="App.TFrame")
        self.controls.pack(fill="x", pady=(0, 14))
        self.check_button = self.ttk.Button(self.controls, text="重新检查连接", style="Secondary.TButton", command=self.check_connections)
        self.check_button.pack(side="left")
        self.read_button = self.ttk.Button(self.controls, text="只读预演", style="Primary.TButton", command=lambda: self.start_run(False))
        self.read_button.pack(side="left", padx=10)
        self.rename_button = self.ttk.Button(self.controls, text="开始安全改名", style="Success.TButton", command=lambda: self.start_run(True))
        self.rename_button.pack(side="left")
        self.stop_button = self.ttk.Button(self.controls, text="立即停止", style="Danger.TButton", command=self.stop_run, state="disabled")
        self.stop_button.pack(side="right")

        log_card = self.ttk.Frame(outer, style="Card.TFrame", padding=14)
        log_card.pack(fill="both", expand=True)
        self.log_header = self.ttk.Frame(log_card, style="Card.TFrame")
        self.log_header.pack(fill="x", pady=(0, 8))
        self.ttk.Label(self.log_header, text="运行记录", style="CardTitle.TLabel").pack(side="left")
        self.ttk.Button(self.log_header, text="清空显示", style="Secondary.TButton", command=self._clear_log).pack(side="right")
        self.log = self.tk.Text(
            log_card,
            bg="#07101f",
            fg="#cbd5e1",
            insertbackground="white",
            relief="flat",
            borderwidth=0,
            font=("Cascadia Mono", 10),
            padx=12,
            pady=10,
            wrap="word",
            state="disabled",
        )
        scrollbar = self.ttk.Scrollbar(log_card, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        self._append_log("助手已启动。写操作保持关闭，直到你点击“开始安全改名”并确认。")

    def _status_block(self, parent: Any, column: int, title: str, variable: Any) -> None:
        frame = self.ttk.Frame(parent, style="Card.TFrame")
        frame.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 10, 0))
        self.ttk.Label(frame, text=title, style="CardText.TLabel").pack(anchor="w")
        label = self.ttk.Label(frame, textvariable=variable, style="Idle.TLabel")
        label.pack(anchor="w", pady=(4, 0))
        variable._status_label = label

    def _read_form(self) -> AppSettings:
        settings = AppSettings(
            mcp_url=self.mcp_var.get().strip().rstrip("/"),
            ollama_url=self.settings.ollama_url,
            model=self.model_var.get().strip(),
            batch_limit=int(self.batch_var.get()),
            unlimited=self.settings.unlimited,
        )
        settings.validate()
        return settings

    def _set_status(self, variable: Any, ok: bool, detail: str) -> None:
        variable.set(("● " if ok else "× ") + detail)
        label = getattr(variable, "_status_label", None)
        if label is not None:
            label.configure(style="Good.TLabel" if ok else "Bad.TLabel")

    def check_connections(self) -> None:
        if self.process and self.process.poll() is None:
            return
        try:
            self.settings = self._read_form()
            save_settings(self.root, self.settings)
        except (ValueError, OSError) as exc:
            self.messagebox.showerror("设置有误", str(exc), parent=self.window)
            return
        self.check_button.configure(state="disabled")
        self.mcp_status_var.set("○ 正在检查")
        self.ollama_status_var.set("○ 正在检查")
        self.opencode_status_var.set("○ 正在检查")
        threading.Thread(target=self._status_worker, daemon=True).start()

    def _status_worker(self) -> None:
        self.events.put(("status", collect_status(self.settings)))

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
                "确认开始安全改名",
                f"本次最多处理 {self.settings.batch_limit} 只。\n\n"
                "仅处理仍为繁中物种默认名的宝可梦，昵称完全采用 Poke Genie 结果。\n"
                "程序没有传送权限，但仍建议你看着手机屏幕。\n\n确认开始吗？",
                icon="warning",
                parent=self.window,
            )
            if not confirmed:
                return
        self._set_running(True, write_enabled)
        self._append_log("—" * 56)
        self._append_log("正在执行启动前检查…")
        threading.Thread(target=self._run_worker, args=(write_enabled,), daemon=True).start()

    def _run_worker(self, write_enabled: bool) -> None:
        status = collect_status(self.settings)
        missing = [name for name in ("mcp", "ollama", "opencode") if not status[name]["ok"]]
        self.events.put(("status", status))
        if missing:
            self.events.put(("fatal", "启动失败：" + "、".join(missing) + " 尚未就绪。"))
            return
        runtime = self.root / "desktop"
        if not (runtime / "opencode.jsonc").is_file():
            self.events.put(("fatal", f"找不到桌面配置：{runtime / 'opencode.jsonc'}"))
            return
        prompt = rename_prompt(self.settings.batch_limit) if write_enabled else READ_ONLY_PROMPT
        binary = str(status["opencode"]["path"])
        command = [
            binary,
            "run",
            "--format",
            "json",
            "--model",
            f"ollama/{self.settings.model}",
            "--title",
            "Pokémon GO 安全改名" if write_enabled else "Pokémon GO 只读预演",
            prompt,
        ]
        environment = os.environ.copy()
        environment.update(
            {
                "PYTHONPATH": str(self.root / "src"),
                "IPHONE_MCP_URL": self.settings.mcp_url,
                "IPHONE_MCP_HEALTH_URL": self.settings.health_url,
                "IPHONE_MCP_PROTOCOL_VERSION": "2025-11-25",
                "POKEMON_GO_BUNDLE_ID": "com.nianticlabs.pokemongo",
                "POGO_WRITE_ENABLED": "true" if write_enabled else "false",
                "POGO_BATCH_LIMIT": str(self.settings.batch_limit),
                "POGO_OBSERVATION_TTL_SECONDS": "20",
                "POGO_JOURNAL_PATH": str(self.root / ".pogo-data" / "actions.jsonl"),
                "NO_COLOR": "1",
            }
        )
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.process = subprocess.Popen(
                command,
                cwd=runtime,
                env=environment,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=creation_flags,
            )
            self.events.put(("log", "本地模型已启动。" + ("改名权限仅对本次运行开启。" if write_enabled else "当前为严格只读模式。")))
            assert self.process.stdout is not None
            for line in self.process.stdout:
                message = friendly_event(line)
                if message:
                    self.events.put(("log", message))
            code = self.process.wait()
            self.events.put(("finished", code))
        except OSError as exc:
            self.events.put(("fatal", f"无法启动 OpenCode：{exc}"))
        finally:
            self.process = None

    def stop_run(self) -> None:
        process = self.process
        if not process or process.poll() is not None:
            return
        self._append_log("正在停止任务并关闭手机控制代理…")
        try:
            if os.name == "nt":
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    timeout=5,
                    check=False,
                )
            else:
                process.terminate()
        except (OSError, subprocess.TimeoutExpired):
            try:
                process.kill()
            except OSError:
                pass
        self._set_running(False, False)
        self._append_log("任务已停止，写权限已随进程关闭。")

    def _set_running(self, running: bool, write_enabled: bool) -> None:
        state = "disabled" if running else "normal"
        self.check_button.configure(state=state)
        self.read_button.configure(state=state)
        self.rename_button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")
        self.run_state_var.set(
            "● 正在安全改名" if running and write_enabled else "● 正在只读预演" if running else "待机 · 写操作关闭"
        )
        label = getattr(self.run_state_var, "_status_label", None)
        if label is not None:
            label.configure(style="Good.TLabel" if running else "Idle.TLabel")

    def _drain_events(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(str(payload))
                elif kind == "status":
                    self._apply_status(payload)
                elif kind == "fatal":
                    self._append_log(str(payload))
                    self._set_running(False, False)
                    self.messagebox.showerror("无法启动", str(payload), parent=self.window)
                elif kind == "finished":
                    self._append_log("任务正常结束。" if payload == 0 else f"任务已结束，退出码 {payload}。")
                    self._set_running(False, False)
                elif kind == "progress":
                    self._apply_progress(payload)
        except queue.Empty:
            pass
        self.window.after(100, self._drain_events)

    def _apply_progress(self, progress: dict[str, Any]) -> None:
        pass

    def _apply_status(self, status: dict[str, Any]) -> None:
        self._set_status(self.mcp_status_var, status["mcp"]["ok"], status["mcp"]["detail"])
        self._set_status(self.ollama_status_var, status["ollama"]["ok"], status["ollama"]["detail"])
        self._set_status(self.opencode_status_var, status["opencode"]["ok"], status["opencode"]["detail"])
        self.check_button.configure(state="normal" if not self.process else "disabled")
        if all(status[name]["ok"] for name in ("mcp", "ollama", "opencode")):
            self._append_log("连接检查通过：iPhone MCP、本地模型和执行引擎均已就绪。")
        else:
            self._append_log("连接检查未完全通过，请查看顶部状态。")

    def _append_log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log.configure(state="normal")
        self.log.insert("end", f"[{timestamp}] {message}\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log.configure(state="normal")
        self.log.delete("1.0", "end")
        self.log.configure(state="disabled")

    def _on_close(self) -> None:
        if self.process and self.process.poll() is None:
            if not self.messagebox.askyesno("任务仍在运行", "关闭窗口会立即停止任务。确定关闭吗？", parent=self.window):
                return
            self.stop_run()
        self.window.destroy()


def self_check(root: Path) -> int:
    settings = load_settings(root)
    result = collect_status(settings)
    result["project_root"] = str(root)
    result["runtime_config"] = str(root / "desktop" / "opencode.jsonc")
    result["runtime_config_exists"] = (root / "desktop" / "opencode.jsonc").is_file()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["runtime_config_exists"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=APP_TITLE)
    parser.add_argument("--self-check", action="store_true", help="run without opening the window")
    args = parser.parse_args(argv)
    root = project_root()
    if args.self_check:
        return self_check(root)

    import tkinter as tk

    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    window = tk.Tk()
    RenamerApp(window, root)
    window.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
