from __future__ import annotations

import argparse
import time
from typing import Any

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v14 as v14
from .appraisal_agent import Snapshot, screen_snapshot
from .config import Settings
from .native_agent import emit
from .policy import PolicyViolation
from .server import SafeProxy


_MANUAL_UNLOCK_TIMEOUT: float | None = None


def device_screen_state(proxy: SafeProxy) -> tuple[bool, bool]:
    result = proxy.call_tool("get_screen_info", {})
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        return False, True
    nested = structured.get("device_state")
    state = nested if isinstance(nested, dict) else structured
    return bool(state.get("locked", False)), bool(state.get("screen_on", True))


def wait_for_manual_unlock(
    proxy: SafeProxy, *, timeout: float | None = _MANUAL_UNLOCK_TIMEOUT
) -> None:
    """Wait read-only for the user to complete device authentication."""

    emit(
        "status",
        message=(
            "iPad 已进入锁屏；任务保持运行并暂停所有触控。"
            "请在设备上手动解锁，解锁后会自动继续当前一只。"
        ),
    )
    deadline = None if timeout is None else time.monotonic() + timeout
    while deadline is None or time.monotonic() < deadline:
        locked, screen_on = device_screen_state(proxy)
        if not locked and screen_on:
            emit("status", message="检测到 iPad 已解锁；正在重新验证当前游戏画面。")
            return
        time.sleep(1.0)
    raise PolicyViolation(
        f"等待 iPad 手动解锁超过 {timeout:g} 秒；任务安全停止"
    )


def wait_for_unlocked_snapshot(proxy: SafeProxy, snapshot: Snapshot) -> Snapshot:
    """Return a fresh snapshot only after the device is unlocked and on.

    The patched MCP intentionally returns a truthful lock-screen image instead
    of an all-black frame.  Lock handling therefore cannot be conditional on
    pixel darkness anymore.  This gate is safe to use after every read: it
    performs no phone input while locked and refreshes the observation once the
    user has authenticated on the device.
    """

    locked, screen_on = device_screen_state(proxy)
    if not locked and screen_on:
        return snapshot
    wait_for_manual_unlock(proxy)
    return screen_snapshot(proxy)


def refresh_game_foreground_capture(proxy: SafeProxy) -> Snapshot:
    """Background and foreground Pokémon GO once without coordinate taps.

    This is only used after unlocked/on-screen pure-black capture frames.  It
    does not terminate the game and is refused while any rename is pending.
    """

    if proxy.pending_name:
        raise PolicyViolation("存在待确认昵称时禁止刷新游戏前台状态")
    observation = proxy.observation
    if observation is None:
        raise PolicyViolation("刷新截图通道前缺少安全观察")
    proxy.call_tool(
        "wake_and_home",
        {
            "sequence": "home_twice",
            "_observation_token": observation.token,
            "_intent": "navigate Home to recover the ios-mcp capture channel",
            "_expected_after": "iPad Home screen before relaunching Pokemon GO",
        },
    )
    home = base._next_snapshot(proxy, 1.0)
    if v14.snapshot_is_black(home):
        emit(
            "status",
            message=(
                "返回 iPad 主屏幕后截图仍为纯黑；不会模拟电源键，"
                "将直接按 bundleId 重新前台 Pokémon GO 并继续只读等待截图恢复。"
            ),
        )
    observation = proxy.observation
    if observation is None:
        raise PolicyViolation("返回主屏幕后缺少安全观察")
    proxy.call_tool(
        "launch_app",
        {
            "bundle_id": proxy.settings.pokemon_go_bundle_id,
            "_observation_token": observation.token,
            "_intent": "launch Pokemon GO to recover the ios-mcp capture channel",
            "_expected_after": "Pokemon GO foreground after capture refresh",
        },
    )
    return home


def restart_game_for_capture(proxy: SafeProxy) -> None:
    """Force-restart only the configured game after Home capture was proven."""

    if proxy.pending_name:
        raise PolicyViolation("存在待确认昵称时禁止重启游戏")
    observation = proxy.observation
    if observation is None:
        raise PolicyViolation("重启游戏前缺少安全观察")
    proxy.call_tool(
        "kill_app",
        {
            "bundle_id": proxy.settings.pokemon_go_bundle_id,
            "_observation_token": observation.token,
            "_intent": "restart and launch Pokemon GO after a verified game-only black frame",
            "_expected_after": "Pokemon GO terminated before one controlled relaunch",
        },
    )
    observation = proxy.observation
    if observation is None:
        raise PolicyViolation("结束游戏后缺少安全观察")
    proxy.call_tool(
        "launch_app",
        {
            "bundle_id": proxy.settings.pokemon_go_bundle_id,
            "_observation_token": observation.token,
            "_intent": "restart and launch Pokemon GO after a verified game-only black frame",
            "_expected_after": "fresh Pokemon GO launch for capture recovery",
        },
    )


def wait_for_capture_channel(
    proxy: SafeProxy,
    snapshot: Snapshot,
    *,
    allow_game_restart: bool = True,
) -> Snapshot:
    snapshot = wait_for_unlocked_snapshot(proxy, snapshot)
    if not v14.snapshot_is_black(snapshot):
        return snapshot

    emit(
        "status",
        message=(
            "MCP 截图通道返回纯黑帧；这不代表 iPad 或 Pokémon GO 黑屏。"
            "只读取等待截图恢复，不执行坐标点击。"
        ),
    )
    deadline = time.monotonic() + 12.0
    while time.monotonic() < deadline:
        snapshot = base._next_snapshot(proxy, 2.0)
        if not v14.snapshot_is_black(snapshot):
            emit("status", message="MCP 截图通道已恢复，开始识别当前游戏页面。")
            return snapshot
        locked, screen_on = device_screen_state(proxy)
        if locked or not screen_on:
            wait_for_manual_unlock(proxy)
            snapshot = base._next_snapshot(proxy, 1.0)
            if not v14.snapshot_is_black(snapshot):
                emit("status", message="解锁后截图已恢复，继续识别当前游戏页面。")
                return snapshot
            deadline = time.monotonic() + 12.0

    emit(
        "status",
        message=(
            "纯黑截图连续 12 秒且设备仍亮屏未锁定；"
            "将仅返回主屏幕再重新前台 Pokémon GO 一次，以刷新截图通道。"
        ),
    )
    refresh_game_foreground_capture(proxy)
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        snapshot = base._next_snapshot(proxy, 2.0)
        if not v14.snapshot_is_black(snapshot):
            emit("status", message="重新前台后截图通道已恢复，继续识别当前游戏页面。")
            return snapshot
        locked, screen_on = device_screen_state(proxy)
        if locked or not screen_on:
            wait_for_manual_unlock(proxy)
            snapshot = base._next_snapshot(proxy, 1.0)
            if not v14.snapshot_is_black(snapshot):
                emit("status", message="解锁后截图已恢复，继续识别当前游戏页面。")
                return snapshot
            deadline = time.monotonic() + 20.0
    emit(
        "status",
        message=(
            "iPad 主屏截图正常，但重新前台后游戏仍为纯黑；"
            "确认故障仅在 Pokémon GO，现只强制重启该游戏一次。"
        ),
    )
    if not allow_game_restart:
        raise PolicyViolation(
            "当前宝可梦处理中截图仍为纯黑；已完成安全的前后台恢复，"
            "但禁止在处理中强制重启游戏，任务保持原状态停止"
        )
    restart_game_for_capture(proxy)
    deadline = time.monotonic() + 75.0
    while time.monotonic() < deadline:
        snapshot = base._next_snapshot(proxy, 2.5)
        if not v14.snapshot_is_black(snapshot):
            emit("status", message="强制重启游戏后截图已恢复，继续识别当前页面。")
            return snapshot
        locked, screen_on = device_screen_state(proxy)
        if locked or not screen_on:
            wait_for_manual_unlock(proxy)
            snapshot = base._next_snapshot(proxy, 1.0)
            if not v14.snapshot_is_black(snapshot):
                emit("status", message="解锁后截图已恢复，继续识别当前游戏页面。")
                return snapshot
            deadline = time.monotonic() + 75.0
    raise PolicyViolation(
        "iPad 主屏截图正常，但 Pokémon GO 强制重启后截图仍连续 75 秒为纯黑；"
        "游戏渲染未恢复，未执行坐标点击"
    )


def run(mode: str, settings: Settings, ollama_url: str, model: str) -> int:
    previous = v14._wait_until_visible
    v14._wait_until_visible = wait_for_capture_channel
    try:
        return v14.run(mode, settings, ollama_url, model)
    finally:
        v14._wait_until_visible = previous


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="iPad landscape truthful capture renamer v15")
    parser.add_argument("--mode", choices=("scan", "rename"), required=True)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.8:27b")
    args = parser.parse_args(argv)
    try:
        return run(args.mode, Settings.from_env(), args.ollama_url, args.model)
    except Exception as exc:
        emit("error", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
