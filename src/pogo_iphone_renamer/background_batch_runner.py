from __future__ import annotations

"""Detached, lock-screen-safe owner for the deterministic batch worker.

The Tk window starts this module, but it does not own its lifetime.  The runner
owns both ``caffeinate`` and the real batch subprocess, so an idle lock screen
or a closed control window cannot release the power assertion or cut the
worker's stdout pipe.  Its only durable interfaces are the existing pause file,
the human-readable live log, and a small JSON state file.
"""

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, TextIO

from .gui_ipad_landscape import friendly_ipad_landscape_event
from .power_awake import AwakeGuard


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%H:%M:%S")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"[{timestamp}] {message}\n")


def _write_state(path: Path, **values: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(values, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def worker_command(mode: str) -> list[str]:
    """Use the runner interpreter so its installed dependencies match the GUI."""

    return [
        sys.executable,
        "-u",
        "-m",
        "pogo_iphone_renamer.ipad_landscape_batch_agent_v26",
        "--mode",
        mode,
    ]


def background_run_is_active(state_path: Path) -> bool:
    """Return true only for a live runner recorded as active.

    A state file can survive a crash, so it is never treated as an eternal lock.
    The batch worker retains its own device-level lock as the final authority.
    """

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        pid = int(state.get("pid", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if state.get("status") not in {"starting", "waiting_for_mcp", "running"} or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def request_background_stop(state_path: Path) -> bool:
    """Ask the detached runner to stop its child and release its power guard."""

    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        pid = int(state.get("pid", 0))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    if not background_run_is_active(state_path):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        return False
    return True


def _event_progress(line: str) -> dict[str, object] | None:
    try:
        event = json.loads(line.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(event, dict) or event.get("type") != "progress":
        return None
    return event


def _mcp_health_available(health_url: str, *, timeout: float = 3.0) -> bool:
    """Return whether the configured MCP endpoint is ready for a safe worker.

    A Pokémon GO update or an iPad-side MCP restart can temporarily close the
    service port.  Starting the worker during that window only creates a
    misleading failure; this deliberately performs no phone action.
    """

    request = urllib.request.Request(health_url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            value = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError, json.JSONDecodeError):
        return False
    return isinstance(value, dict) and value.get("status") == "ok"


def _is_recoverable_mcp_disconnect(lines: list[str]) -> bool:
    """Recognize transport-only worker exits which are safe to reconnect.

    This intentionally excludes page-classification and rename failures.  On
    those failures the task stops for safety; only a lost MCP transport is
    allowed to keep the detached owner alive and retry the exact direct route.
    """

    text = "\n".join(lines).casefold()
    return any(
        marker in text
        for marker in (
            "connection refused",
            "connection reset",
            "connection aborted",
            "network is unreachable",
            "no route to host",
            "urlopen error",
            "remote end closed connection",
            "remotedisconnected",
            "httpx.connecterror",
            "urlerror",
            "timed out",
            "timeouterror",
            "mcp transport",
        )
    )


def run_background_batch(
    mode: str,
    *,
    root: Path,
    environment: dict[str, str] | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
    awake_factory: Callable[[], AwakeGuard] = AwakeGuard,
    health_check: Callable[[str], bool] = _mcp_health_available,
    sleep: Callable[[float], None] = time.sleep,
) -> int:
    """Run the deterministic worker independently from the desktop window."""

    env = os.environ.copy()
    if environment is not None:
        env.update(environment)
    log_path = Path(
        env.get("POGO_BACKGROUND_LOG", root / ".pogo-data" / "background-worker.log")
    )
    state_path = Path(env.get("POGO_BATCH_STATE", root / ".pogo-data" / "batch-state.json"))
    runner_pid = os.getpid()
    stopped = False
    child: Any | None = None
    reconnects = 0

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True
        if child is not None and child.poll() is None:
            child.terminate()

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    awake = awake_factory()
    started_at = _now()

    def wait_for_mcp() -> bool:
        """Hold the detached job until MCP recovers, without touching iPad."""

        nonlocal reconnects
        health_url = env.get("IPHONE_MCP_HEALTH_URL", "")
        # Unit/integration callers that do not provide a configured endpoint
        # retain the historical runner behaviour.  The production headless
        # launcher always supplies this value from the saved GUI settings.
        if not health_url:
            return True
        while not stopped:
            if health_url and health_check(health_url):
                if reconnects:
                    _append_log(
                        log_path,
                        "iOS MCP 连接已恢复；正在用当前代码重新读取当前宝可梦详情页。",
                    )
                return True
            reconnects += 1
            _write_state(
                state_path,
                status="waiting_for_mcp",
                pid=runner_pid,
                mode=mode,
                started_at=started_at,
                reconnects=reconnects,
            )
            if reconnects == 1:
                _append_log(
                    log_path,
                    "iOS MCP 暂时不可连接；后台将保持防睡眠并只读等待恢复，"
                    "不会触碰 iPad、重开游戏或进入宝可梦盒。",
                )
            sleep(10.0)
        return False

    try:
        _write_state(
            state_path,
            status="starting",
            pid=runner_pid,
            mode=mode,
            started_at=started_at,
        )
        description = awake.acquire()
        if description:
            _append_log(log_path, description + "。")
        _append_log(
            log_path,
            "后台批量工作进程已启动；Mac 锁屏或关闭控制窗口后仍会继续。",
        )
        if stopped:
            _write_state(
                state_path,
                status="stopped",
                pid=runner_pid,
                mode=mode,
                started_at=started_at,
                finished_at=_now(),
            )
            return 1
        while not stopped:
            if not wait_for_mcp():
                break
            child = popen(
                worker_command(mode),
                cwd=root,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
            )
            _write_state(
                state_path,
                status="running",
                pid=runner_pid,
                worker_pid=getattr(child, "pid", None),
                mode=mode,
                started_at=started_at,
                reconnects=reconnects,
            )
            worker_lines: list[str] = []
            stream: TextIO | None = child.stdout
            if stream is not None:
                for line in stream:
                    worker_lines.append(line)
                    progress = _event_progress(line)
                    if progress is not None:
                        _write_state(
                            state_path,
                            status="running",
                            pid=runner_pid,
                            worker_pid=getattr(child, "pid", None),
                            mode=mode,
                            started_at=started_at,
                            reconnects=reconnects,
                            progress=progress,
                        )
                    message = friendly_ipad_landscape_event(line)
                    if message:
                        _append_log(log_path, message)
            code = child.wait()
            if code == 0 or stopped:
                break
            if not (
                _is_recoverable_mcp_disconnect(worker_lines)
                or not health_check(env.get("IPHONE_MCP_HEALTH_URL", ""))
            ):
                break
            _append_log(
                log_path,
                "iOS MCP 在任务中断开；仅等待连接恢复后从当前详情页重新读取，"
                "不会重开游戏或改动其他页面。",
            )
            child = None
        else:
            code = 1
        if stopped and (child is None or child.poll() is not None):
            code = 1
        status = "stopped" if stopped else "finished" if code == 0 else "failed"
        _write_state(
            state_path,
            status=status,
            pid=runner_pid,
            worker_pid=getattr(child, "pid", None),
            mode=mode,
            started_at=started_at,
            finished_at=_now(),
            exit_code=code,
        )
        _append_log(
            log_path,
            "后台任务正常结束。" if code == 0 else f"后台任务已结束，退出码 {code}。",
        )
        return code
    except OSError as exc:
        _write_state(
            state_path,
            status="failed",
            pid=runner_pid,
            mode=mode,
            started_at=started_at,
            finished_at=_now(),
            error=str(exc),
        )
        _append_log(log_path, f"无法启动后台批量流程：{exc}")
        return 1
    finally:
        awake.release()
        _append_log(log_path, "电脑防睡眠已释放；系统电源策略已恢复。")
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGINT, previous_int)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lock-screen-safe Pokémon GO batch runner")
    parser.add_argument("--mode", choices=("scan", "rename"), required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    return run_background_batch(args.mode, root=args.root.resolve())


if __name__ == "__main__":
    raise SystemExit(main())
