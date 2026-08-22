from __future__ import annotations

from . import ipad_landscape_agent as base
from .appraisal_agent import Snapshot
from .local_ocr_v4 import LocatedText, locate_exact_text_from_mcp
from .native_agent import tool_result_message
from .policy import PolicyViolation
from .server import SafeProxy


def scaled_text_center(
    located: LocatedText,
    *,
    observation_width: float,
    observation_height: float,
    x_range: tuple[float, float],
    y_range: tuple[float, float],
) -> tuple[float, float]:
    box = located.box
    x_ratio = ((box.left + box.right) / 2.0) / located.image_width
    y_ratio = ((box.top + box.bottom) / 2.0) / located.image_height
    if not (x_range[0] <= x_ratio <= x_range[1]):
        raise PolicyViolation(f"控件横向位置超出安全区域：{x_ratio:.3f}")
    if not (y_range[0] <= y_ratio <= y_range[1]):
        raise PolicyViolation(f"控件纵向位置超出安全区域：{y_ratio:.3f}")
    return observation_width * x_ratio, observation_height * y_ratio


def _screenshot_only(proxy: SafeProxy) -> str:
    result = proxy.call_tool("screenshot", {})
    message = tool_result_message("screenshot", result)
    images = message.get("images")
    if not isinstance(images, list) or not images:
        raise PolicyViolation("iOS MCP 没有返回截图")
    image = str(images[-1])
    base._remember_stage_geometry(proxy, Snapshot(text="", image=image))
    return image


def tap_ocr_control(
    proxy: SafeProxy,
    text: str,
    *,
    x_range: tuple[float, float],
    y_range: tuple[float, float],
    expected_after: str,
    intent: str,
) -> None:
    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 未返回触控空间")
    image = _screenshot_only(proxy)
    located = locate_exact_text_from_mcp(
        image, base.ORIENTATION, text, minimum_confidence=0.70
    )
    x_ratio, y_ratio = scaled_text_center(
        located,
        observation_width=1.0,
        observation_height=1.0,
        x_range=x_range,
        y_range=y_range,
    )
    x, y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        x_ratio,
        y_ratio,
        geometry=base.current_stage_geometry(proxy),
    )
    proxy.call_tool(
        "tap_screen",
        {
            "x": x,
            "y": y,
            "_observation_token": observation.token,
            "_intent": intent,
            "_expected_after": expected_after,
        },
    )


def tap_cancel(proxy: SafeProxy) -> None:
    tap_ocr_control(
        proxy,
        "取消",
        x_range=(0.36, 0.64),
        y_range=(0.60, 0.76),
        expected_after="DETAIL",
        intent="navigate OCR-verified cancel rename dialog without submitting",
    )


def tap_ok(proxy: SafeProxy) -> None:
    tap_ocr_control(
        proxy,
        "OK",
        x_range=(0.36, 0.64),
        y_range=(0.48, 0.64),
        expected_after="DETAIL",
        intent="rename submit exact verified nickname using OCR-verified OK control",
    )
