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
from .live_activity import live_activity_paths, update_live_activity
from .power_awake import AwakeGuard


_MCP_RETRY_BASE_SECONDS = 5.0
_MCP_RETRY_MAX_SECONDS = 60.0
_VERIFICATION_TARGET_DEFAULT = 50
_PROGRESS_COUNT_KEYS = ("renamed", "skipped", "scanned", "unreadable")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _append_log(path: Path, message: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%H:%M:%S")
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(f"[{timestamp}] {message}\n")
    mirror_dir = os.getenv("POGO_DASHBOARD_MIRROR_DIR", "").strip()
    if mirror_dir:
        mirror = Path(mirror_dir) / path.name
        if mirror != path:
            mirror.parent.mkdir(parents=True, exist_ok=True)
            with mirror.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(f"[{timestamp}] {message}\n")


def _write_state(path: Path, **values: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(values, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    mirror_dir = os.getenv("POGO_DASHBOARD_MIRROR_DIR", "").strip()
    if mirror_dir:
        mirror = Path(mirror_dir) / path.name
        if mirror != path:
            mirror.parent.mkdir(parents=True, exist_ok=True)
            mirror_temporary = mirror.with_suffix(mirror.suffix + ".tmp")
            mirror_temporary.write_text(
                json.dumps(values, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            mirror_temporary.replace(mirror)


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


def _worker_event(line: str) -> dict[str, object] | None:
    """Return a structured worker event without treating ordinary logs as one."""

    try:
        event = json.loads(line.strip())
    except (json.JSONDecodeError, TypeError):
        return None
    return event if isinstance(event, dict) and isinstance(event.get("type"), str) else None


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


def _mcp_retry_delay(reconnects: int) -> float:
    """Return a bounded backoff for a disconnected MCP transport.

    The health endpoint can briefly answer before the endpoint used for screen
    reads is actually reachable.  Retrying the complete worker immediately in
    that state creates a tight restart loop and makes a transient Wi-Fi issue
    look like a stalled renaming workflow.  A small capped backoff keeps the
    Mac awake and preserves the recovery journal without hammering the iPad.
    """

    exponent = min(max(reconnects - 1, 0), 4)
    return min(_MCP_RETRY_MAX_SECONDS, _MCP_RETRY_BASE_SECONDS * (2**exponent))


def _verification_target(environment: dict[str, str]) -> int:
    """Return the non-stopping continuous-run acceptance threshold."""

    try:
        value = int(environment.get("POGO_CONTINUOUS_VERIFICATION_TARGET", "50"))
    except ValueError:
        return _VERIFICATION_TARGET_DEFAULT
    return max(1, value)


def _count_progress(progress: dict[str, object]) -> dict[str, int]:
    return {
        key: max(0, int(progress.get(key, 0)))
        if isinstance(progress.get(key, 0), (int, float))
        else 0
        for key in _PROGRESS_COUNT_KEYS
    }


def _with_cumulative_verification(
    progress: dict[str, object], *, completed: dict[str, int], target: int
) -> dict[str, object]:
    """Count only verified renames toward acceptance; expose traversal separately."""

    local = _count_progress(progress)
    total = {key: completed[key] + local[key] for key in _PROGRESS_COUNT_KEYS}
    result = dict(progress)
    result.update(total)
    processed = sum(total.values())
    result["verification"] = {
        "target": target,
        "completed": total["renamed"],
        "processed": processed,
        "basis": "verified_renames",
        "passed": total["renamed"] >= target,
    }
    return result


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
    activity_path, _preview_path = live_activity_paths(root)
    activity_path = Path(env.get("POGO_LIVE_ACTIVITY_PATH", activity_path))
    runner_pid = os.getpid()
    stopped = False
    child: Any | None = None
    reconnects = 0
    verification_target = _verification_target(env)
    completed_counts = {key: 0 for key in _PROGRESS_COUNT_KEYS}
    latest_progress: dict[str, object] | None = None

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
            update_live_activity(
                {
                    "type": "waiting",
                    "stage": "等待 iOS MCP 重连",
                    "reason": "MCP 服务暂时不可达，尚未获得新的 iPad 观察结果。",
                    "attempt": reconnects,
                    "next_action": "按退避间隔检查 MCP 健康状态，恢复后从当前游戏画面重新读取。",
                    "user_action": "无需操作；后台正在自动重连，不会点击、重开游戏或跳过宝可梦。",
                },
                path=activity_path,
            )
            if reconnects == 1:
                _append_log(
                    log_path,
                    "iOS MCP 暂时不可连接；后台将保持防睡眠并只读等待恢复，"
                    "不会触碰 iPad、重开游戏或进入宝可梦盒。",
                )
            sleep(_mcp_retry_delay(reconnects))
        return False

    try:
        mirror_dir = env.get("POGO_DASHBOARD_MIRROR_DIR", "").strip()
        if mirror_dir:
            mirror = Path(mirror_dir)
            mirror.mkdir(parents=True, exist_ok=True)
            settings_source = root / ".pogo-data" / "gui-settings.json"
            if settings_source.is_file():
                (mirror / "gui-settings.json").write_bytes(settings_source.read_bytes())
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
            child_counts = {key: 0 for key in _PROGRESS_COUNT_KEYS}
            stream: TextIO | None = child.stdout
            if stream is not None:
                for line in stream:
                    worker_lines.append(line)
                    event = _worker_event(line)
                    if event is not None:
                        update_live_activity(event, path=activity_path)
                    progress = _event_progress(line)
                    if progress is not None:
                        child_counts = _count_progress(progress)
                        cumulative_progress = _with_cumulative_verification(
                            progress,
                            completed=completed_counts,
                            target=verification_target,
                        )
                        latest_progress = cumulative_progress
                        _write_state(
                            state_path,
                            status="running",
                            pid=runner_pid,
                            worker_pid=getattr(child, "pid", None),
                            mode=mode,
                            started_at=started_at,
                            reconnects=reconnects,
                            progress=cumulative_progress,
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
            # A health response alone is not sufficient proof that the route
            # used by describe_screen has recovered.  Always pause before a
            # replacement worker, even when /health happened to return OK.
            reconnects += 1
            completed_counts = {
                key: completed_counts[key] + child_counts[key]
                for key in _PROGRESS_COUNT_KEYS
            }
            retry_delay = _mcp_retry_delay(reconnects)
            _write_state(
                state_path,
                status="waiting_for_mcp",
                pid=runner_pid,
                mode=mode,
                started_at=started_at,
                reconnects=reconnects,
                retry_after_seconds=retry_delay,
            )
            _append_log(
                log_path,
                f"MCP 传输恢复前等待 {retry_delay:g} 秒，避免断线时重复启动流程。",
            )
            sleep(retry_delay)
            child = None
        else:
            code = 1
        if stopped and (child is None or child.poll() is not None):
            code = 1
        status = "stopped" if stopped else "finished" if code == 0 else "failed"
        terminal_state: dict[str, Any] = {
            "status": status,
            "pid": runner_pid,
            "worker_pid": getattr(child, "pid", None),
            "mode": mode,
            "started_at": started_at,
            "finished_at": _now(),
            "exit_code": code,
        }
        if latest_progress is not None:
            # The dashboard must keep the continuous-50 result visible after
            # a normal finish.  Previously this final write discarded the
            # latest card counts and made a one-card early exit look like an
            # unqualified successful batch.
            terminal_state["progress"] = latest_progress
        _write_state(state_path, **terminal_state)
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
