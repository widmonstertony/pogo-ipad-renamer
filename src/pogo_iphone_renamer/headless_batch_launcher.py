from __future__ import annotations

"""Start the current batch code without importing or reopening Tk.

This small launcher is intentionally separate from the GUI.  It reads the
saved GUI connection settings and starts the detached runner from the source
tree.  Its default is the verified automatic entry route: it can resume from
the game's map, menu, box, or an already-open detail page.  A caller may still
ask for strict direct-detail mode when that narrower behaviour is wanted.
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .appraisal_agent import screen_snapshot
from .batch_pause import BatchPauseFile
from .background_batch_runner import background_run_is_active, request_background_stop
from .config import Settings
from .gui import AppSettings, load_settings
from .live_activity import live_activity_paths, publish_preview, update_live_activity
from .resilient_mcp import ResilientStreamableHTTPClient
from .protocol import text_from_content
from .server import SafeProxy


# These are the two user-provided LAN endpoints for this workspace.  The
# device model is intentionally carried into the detached worker so a DHCP
# mix-up cannot quietly point a long-running batch at the other iPad.
_KNOWN_MCP_DEVICE_MACHINES = {
    "http://192.168.68.84:8090/mcp": "iPad7,2",
    "http://192.168.68.104:8090/mcp": "iPad14,6",
}


def background_environment(
    root: Path,
    settings: AppSettings,
    *,
    from_current_detail: bool = False,
) -> dict[str, str]:
    """Build the same safety configuration as the batch GUI, without Tk."""

    activity_path, preview_path = live_activity_paths(root)
    dashboard_mirror = Path.home() / "Library" / "Application Support" / "PokemonGOOrganizer"
    environment = {
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
        "POGO_LIVE_RAW_PREVIEW_PATH": str(root / ".pogo-data" / "live-preview-raw.jpg"),
        # The native macOS app reads its sandbox-friendly Application Support
        # mirror.  The worker remains authoritative and mirrors only its
        # local dashboard artifacts; no additional iPad reads are introduced.
        "POGO_DASHBOARD_MIRROR_DIR": str(dashboard_mirror),
        # Automatic entry is the normal launcher behaviour.  The batch agent
        # verifies every intermediate page and only opens a detail through its
        # calibrated MAP -> MAIN_MENU -> INVENTORY -> DETAIL route.  Strict
        # direct-detail mode remains available for a deliberately constrained
        # resume, but is not required for ordinary automation.
        "POGO_START_FROM_CURRENT_DETAIL": "true" if from_current_detail else "false",
        "POGO_ALLOW_GAME_RESTART": "false",
        # A dropped MCP screenshot stream is safe to wait out.  The detached
        # direct-detail worker must not convert it into a task failure.
        "POGO_PERSIST_CAPTURE_WAIT": "true",
    }
    expected_machine = _KNOWN_MCP_DEVICE_MACHINES.get(settings.mcp_url.rstrip("/"))
    if expected_machine:
        environment["POGO_EXPECTED_DEVICE_MACHINE"] = expected_machine
    if expected_machine == "iPad7,2":
        # Its current MCP server caches Stage Manager screenshots by session.
        # Force a fresh *read* session before each local pixel observation.
        environment["POGO_RESET_MCP_SCREENSHOT_SESSION"] = "true"
    return environment


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


def start_batch(
    root: Path,
    *,
    mode: str,
    from_current_detail: bool = False,
    settings: AppSettings | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
) -> int:
    """Detach a fresh worker running the source currently on disk."""

    root = root.resolve()
    state_path = root / ".pogo-data" / "batch-state.json"
    if background_run_is_active(state_path):
        raise RuntimeError("已有后台批量任务正在运行；不会启动第二个手机控制任务")
    if from_current_detail:
        # An explicit "from the current detail" start supersedes a handoff
        # recorded by an older run. Reusing that prior CP after the user has
        # navigated to a different detail can send recovery into an unrelated
        # part of the box.
        try:
            (root / ".pogo-data" / "pager-resume.json").unlink()
        except FileNotFoundError:
            pass
    # A previous headless hot-reload may have left its safe-boundary pause
    # marker behind.  A newly started task is an explicit request to continue,
    # so clear it before launching rather than processing exactly one card and
    # immediately pausing again.
    BatchPauseFile(root / ".pogo-data" / "batch.pause").resume()
    settings = settings or load_settings(root)
    settings.validate()
    environment = os.environ.copy()
    environment.update(
        background_environment(
            root,
            settings,
            from_current_detail=from_current_detail,
        )
    )
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


def start_from_current_detail(
    root: Path,
    *,
    mode: str,
    settings: AppSettings | None = None,
    popen: Callable[..., Any] = subprocess.Popen,
) -> int:
    """Backward-compatible strict entry helper used by existing callers."""

    return start_batch(
        root,
        mode=mode,
        from_current_detail=True,
        settings=settings,
        popen=popen,
    )


def _publish_focus_observation(root: Path, image: str | None, message: str) -> None:
    """Make a foreground-recovery observation visible in the desktop app."""

    activity_path, preview_path = live_activity_paths(root)
    publish_preview(image, path=preview_path)
    update_live_activity({"type": "status", "message": message}, path=activity_path)


def focus_configured_game(root: Path, *, settings: AppSettings | None = None) -> bool:
    """Bring only the configured game back to foreground without a restart.

    This is a narrow recovery operation for a system app/window interruption.
    It performs no Pokémon navigation, appraisal or rename.  The same
    SafeProxy policy used by the batch authorizes the exact configured bundle
    and refuses every other app.
    """

    root = root.resolve()
    app_settings = settings or load_settings(root)
    app_settings.validate()
    run_settings = Settings(
        mcp_url=app_settings.mcp_url,
        health_url=app_settings.health_url,
        protocol_version="2025-11-25",
        pokemon_go_bundle_id="com.nianticlabs.pokemongo",
        write_enabled=True,
        batch_limit=0,
        observation_ttl_seconds=120,
        journal_path=root / ".pogo-data" / "actions.jsonl",
    )
    proxy = SafeProxy(run_settings, ResilientStreamableHTTPClient(run_settings, timeout=45.0))
    snapshot = screen_snapshot(proxy)
    frontmost = proxy.call_tool("get_frontmost_app", {})
    bundle_id = run_settings.pokemon_go_bundle_id.casefold()
    if bundle_id in text_from_content(frontmost).casefold():
        _publish_focus_observation(root, snapshot.image, "已确认 Pokémon GO 在前台；正在读取当前画面。")
        return False
    observation = proxy.observation
    if observation is None:
        raise RuntimeError("恢复 Pokémon GO 前缺少安全观察")
    proxy.call_tool(
        "launch_app",
        {
            "bundle_id": run_settings.pokemon_go_bundle_id,
            "_observation_token": observation.token,
            "_intent": "launch configured Pokemon GO to foreground after an interrupted batch",
            "_expected_after": "configured Pokemon GO is foreground",
        },
    )
    verified = proxy.call_tool("get_frontmost_app", {})
    if bundle_id not in text_from_content(verified).casefold():
        raise RuntimeError("已请求恢复 Pokémon GO 前台，但 MCP 未确认该游戏已在前台")
    snapshot = screen_snapshot(proxy)
    _publish_focus_observation(root, snapshot.image, "Pokémon GO 已恢复到前台；正在读取当前画面。")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Start the latest Pokémon GO batch code without Tk"
    )
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--mode", choices=("rename", "scan"), default="rename")
    parser.add_argument(
        "--from-current-detail",
        action="store_true",
        help="strict mode: require an already-open Pokémon detail page",
    )
    parser.add_argument("--stop", action="store_true")
    parser.add_argument("--pause", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--focus-game", action="store_true")
    args = parser.parse_args(argv)
    root = args.root.resolve()
    state_path = root / ".pogo-data" / "batch-state.json"
    pause = BatchPauseFile(root / ".pogo-data" / "batch.pause")
    requested_actions = sum((args.stop, args.pause, args.resume, args.focus_game))
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
    if args.focus_game:
        try:
            changed = focus_configured_game(root)
        except (OSError, RuntimeError, ValueError) as exc:
            print(f"未能恢复 Pokémon GO 前台：{exc}")
            return 1
        print("Pokémon GO 已恢复到前台。" if changed else "Pokémon GO 已在前台。")
        return 0
    try:
        pid = start_batch(
            root,
            mode=args.mode,
            from_current_detail=args.from_current_detail,
        )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"后台批量未启动：{exc}")
        return 1
    entry = "仅从当前详情页继续" if args.from_current_detail else "自动识别当前游戏页面并继续"
    print(f"后台批量已用最新代码启动（PID {pid}）；{entry}。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
