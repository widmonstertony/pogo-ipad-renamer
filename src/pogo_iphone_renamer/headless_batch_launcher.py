from __future__ import annotations

"""Start the current batch code without importing or reopening Tk.

This small launcher is intentionally separate from the GUI.  It reads the
saved GUI connection settings, starts the detached runner from the source tree,
and forces the direct-detail route.  It is therefore suitable for resuming a
user-confirmed batch after the desktop has been locked.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .batch_pause import BatchPauseFile
from .background_batch_runner import background_run_is_active, request_background_stop
from .gui import AppSettings, load_settings
from .live_activity import live_activity_paths


def background_environment(root: Path, settings: AppSettings) -> dict[str, str]:
    """Build the same safety configuration as the batch GUI, without Tk."""

    activity_path, preview_path = live_activity_paths(root)
    return {
        "PYTHONPATH": str(root / "src"),
        "PYTHONUTF8": "1",
        "PYTHONIOENCODING": "utf-8",
        "OC_DISABLE_DOT_ACCESS_WARNING": "1",
        "IPHONE_MCP_URL": settings.mcp_url,
        "IPHONE_MCP_HEALTH_URL": settings.health_url,
        "IPHONE_MCP_PROTOCOL_VERSION": "2025-11-25",
        "POKEMON_GO_BUNDLE_ID": "com.nianticlabs.pokemongo",
        # Scan never calls a rename tool; navigation still needs safe touches.
        "POGO_WRITE_ENABLED": "true",
        "POGO_BATCH_LIMIT": "0" if settings.unlimited else str(settings.batch_limit),
        "POGO_OBSERVATION_TTL_SECONDS": "120",
        "POGO_JOURNAL_PATH": str(root / ".pogo-data" / "actions.jsonl"),
        "POGO_PAUSE_FILE": str(root / ".pogo-data" / "batch.pause"),
        "POGO_BACKGROUND_LOG": str(root / ".pogo-data" / "background-worker.log"),
        "POGO_BATCH_STATE": str(root / ".pogo-data" / "batch-state.json"),
        "POGO_LIVE_ACTIVITY_PATH": str(activity_path),
        "POGO_LIVE_PREVIEW_PATH": str(preview_path),
        # A headless continuation must never fall back to the map/inventory
        # entry path or relaunch the game.
        "POGO_START_FROM_CURRENT_DETAIL": "true",
        "POGO_ALLOW_GAME_RESTART": "false",
        # A dropped MCP screenshot stream is safe to wait out.  The detached
        # direct-detail worker must not convert it into a task failure.
        "POGO_PERSIST_CAPTURE_WAIT": "true",
    }


def runner_command(root: Path, mode: str) -> list[str]:
    return [
        sys.executable,
        "-u",
        "-m",
        "pogo_iphone_renamer.background_batch_runner",
        "--mode",
        mode,
        "--root",
        str(root),
    ]


def start_from_current_detail(
    root: Path,
    *,
    mode: str,
    settings: AppSettings | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
) -> int:
    """Detach a fresh worker running the source currently on disk."""

    root = root.resolve()
    state_path = root / ".pogo-data" / "batch-state.json"
    if background_run_is_active(state_path):
        raise RuntimeError("已有后台批量任务正在运行；不会启动第二个手机控制任务")
    # A previous headless hot-reload may have left its safe-boundary pause
    # marker behind.  A newly started task is an explicit request to continue,
    # so clear it before launching rather than processing exactly one card and
    # immediately pausing again.
    BatchPauseFile(root / ".pogo-data" / "batch.pause").resume()
    settings = settings or load_settings(root)
    settings.validate()
    environment = os.environ.copy()
    environment.update(background_environment(root, settings))
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if os.name == "nt":
        creation_flags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    process = popen(
        runner_command(root, mode),
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creation_flags,
        start_new_session=os.name != "nt",
    )
    return int(getattr(process, "pid", 0))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start the latest Pokémon GO batch code without Tk"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("rename", "scan"), default="rename")
    parser.add_argument(
        "--from-current-detail",
        action="store_true",
        help="required safety acknowledgement: do not navigate from map or inventory",
    )
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--pause", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    state_path = root / ".pogo-data" / "batch-state.json"
    pause = BatchPauseFile(root / ".pogo-data" / "batch.pause")
    requested_actions = sum((args.stop, args.pause, args.resume))
    if requested_actions > 1:
        parser.error("--stop、--pause 与 --resume 只能选择一个")
    if args.pause:
        if not background_run_is_active(state_path):
            print("没有正在运行的后台任务。")
            return 1
        pause.request()
        print("已请求安全暂停；会在当前宝可梦完成后停在详情页。")
        return 0
    if args.resume:
        pause.resume()
        print("已请求继续运行。")
        return 0
    if args.stop:
        if request_background_stop(state_path):
            print("已请求后台任务安全停止。")
            return 0
        print("没有正在运行的后台任务。")
        return 1
    if not args.from_current_detail:
        parser.error("必须明确传入 --from-current-detail；不会从地图或宝可梦盒开始")
    try:
        pid = start_from_current_detail(root, mode=args.mode)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"后台批量未启动：{exc}")
        return 1
    print(f"后台批量已用最新代码启动（PID {pid}）；仅从当前详情页继续。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
