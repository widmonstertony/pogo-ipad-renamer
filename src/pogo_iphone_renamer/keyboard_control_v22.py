from __future__ import annotations

from .appraisal_agent import Snapshot
from .ipad_landscape_agent_v5 import accessibility_elements
from .native_agent import tool_result_message
from .policy import PolicyViolation
from .server import SafeProxy


def _all_elements(proxy: SafeProxy) -> list[dict]:
    result = proxy.call_tool("get_ui_elements", {})
    message = tool_result_message("get_ui_elements", result)
    return accessibility_elements(Snapshot(str(message.get("content", "")), None))


def exact_accessibility_tap_point(
    proxy: SafeProxy, exact_text: str
) -> tuple[float, float] | None:
    matches = [
        item
        for item in _all_elements(proxy)
        if str(item.get("text", "")).strip() == exact_text
        and item.get("clickable") is True
    ]
    if not matches:
        return None
    if len(matches) != 1:
        raise PolicyViolation(f"accessibility 控件不唯一：{exact_text}")
    tap = matches[0].get("tap")
    if not isinstance(tap, dict):
        raise PolicyViolation(f"accessibility 控件没有触点：{exact_text}")
    x, y = tap.get("x"), tap.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
        raise PolicyViolation(f"accessibility 控件触点无效：{exact_text}")
    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 未返回触控空间")
    if not (0 <= float(x) <= observation.width and 0 <= float(y) <= observation.height):
        raise PolicyViolation(f"accessibility 控件触点越界：{exact_text}")
    return float(x), float(y)


def dismiss_active_keyboard(proxy: SafeProxy) -> bool:
    # In the Stage Manager layout iOS reports keyboard accessibility frames in
    # a separate 1024x347 portrait surface while tap_screen uses the 1366x1024
    # landscape display.  A raw accessibility point is therefore not a safe
    # screen coordinate.  The game dialog's OCR-verified OK button remains
    # visible above the keyboard, so submission can safely use that instead.
    from . import ipad_landscape_agent as base

    if base.ORIENTATION == "STAGE_MANAGER_MAXIMIZED":
        return False
    point = exact_accessibility_tap_point(proxy, "收起键盘")
    if point is None:
        return False
    observation = proxy.observation
    assert observation is not None
    pending = proxy.pending_name
    proxy.pending_name = None
    try:
        proxy.call_tool(
            "tap_screen",
            {
                "x": point[0],
                "y": point[1],
                "_observation_token": observation.token,
                "_intent": "navigate dismiss exact accessibility keyboard before rename control",
                "_expected_after": "rename dialog remains visible without keyboard layer",
            },
        )
    finally:
        proxy.pending_name = pending
    return True
