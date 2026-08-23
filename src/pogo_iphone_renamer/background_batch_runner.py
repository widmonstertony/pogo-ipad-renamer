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
    if state.get("status") not in {"starting", "running"} or pid <= 0:
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


def run_background_batch(
    mode: str,
    *,
    root: Path,
    environment: dict[str, str] | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
    awake_factory: Callable[[], AwakeGuard] = AwakeGuard,
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

    def request_stop(_signum: int, _frame: object) -> None:
        nonlocal stopped
        stopped = True
        if child is not None and child.poll() is None:
            child.terminate()

    previous_term = signal.signal(signal.SIGTERM, request_stop)
    previous_int = signal.signal(signal.SIGINT, request_stop)
    awake = awake_factory()
    started_at = _now()
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
        )
        stream: TextIO | None = child.stdout
        if stream is not None:
            for line in stream:
                progress = _event_progress(line)
                if progress is not None:
                    _write_state(
                        state_path,
                        status="running",
                        pid=runner_pid,
                        worker_pid=getattr(child, "pid", None),
                        mode=mode,
                        started_at=started_at,
                        progress=progress,
                    )
                message = friendly_ipad_landscape_event(line)
                if message:
                    _append_log(log_path, message)
        code = child.wait()
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
