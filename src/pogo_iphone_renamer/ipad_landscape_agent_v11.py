from __future__ import annotations

import sys


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdio()

from . import ipad_landscape_agent as base  # noqa: E402
from . import ipad_landscape_agent_v5 as v5  # noqa: E402
from . import ipad_landscape_agent_v7 as v7  # noqa: E402
from .appraisal_agent import Snapshot  # noqa: E402
from .ipad_landscape_agent_v10 import (  # noqa: E402
    EMPTY_FIELD_LABELS,
    _field_value,
    _mark_rename_observation,
)
from .local_ocr_v2 import exact_species_from_name_region  # noqa: E402
from .native_agent import emit  # noqa: E402
from .policy import PolicyViolation  # noqa: E402
from .server import SafeProxy  # noqa: E402


def _clear_button_center(proxy: SafeProxy, current_name: str) -> tuple[float, float]:
    if proxy.observation is None:
        raise PolicyViolation("改名窗口缺少安全观察")
    snapshot = Snapshot(proxy.observation.text, None)
    elements = v5.accessibility_elements(snapshot)
    field_verified = False
    clear_rect = None
    for element in elements:
        text = str(element.get("text", "")).strip()
        rect = element.get("rect")
        if not isinstance(rect, dict):
            continue
        width = rect.get("width")
        if text == current_name and isinstance(width, (int, float)) and width >= 200:
            field_verified = True
        if text == "清除文本" and element.get("type") == "control":
            clear_rect = rect
    if not field_verified:
        raise PolicyViolation("打开改名窗口后未精确读取到原名称字段；未清空")
    if clear_rect is None:
        raise PolicyViolation("打开改名窗口后未找到“清除文本”控件；未清空")
    try:
        x = float(clear_rect["x"])
        y = float(clear_rect["y"])
        width = float(clear_rect["width"])
        height = float(clear_rect["height"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PolicyViolation("“清除文本”控件矩形无效") from exc
    if not (10 <= width <= 80 and 10 <= height <= 80):
        raise PolicyViolation("“清除文本”控件尺寸异常；未点击")
    return x + width / 2.0, y + height / 2.0


def _clear_current_name(proxy: SafeProxy, current_name: str) -> str:
    x, y = _clear_button_center(proxy, current_name)
    assert proxy.observation is not None
    proxy.call_tool(
        "tap_screen",
        {
            "x": x,
            "y": y,
            "_observation_token": proxy.observation.token,
            "_intent": "navigate clear current rename field before entering nickname",
            "_expected_after": "rename field is empty",
        },
    )
    cleared_value = _field_value(proxy)
    if cleared_value.casefold() not in EMPTY_FIELD_LABELS:
        raise PolicyViolation(f"清除旧名称后字段仍有内容：{cleared_value!r}；未输入新昵称")
    return cleared_value


def _commit_and_verify(
    proxy: SafeProxy,
    snapshot: Snapshot,
    *,
    current_name: str,
    species: str,
    nickname: str,
) -> None:
    cleared_value = _clear_current_name(proxy, current_name)
    _mark_rename_observation(proxy, f"accessibility 已验证字段为空占位 {cleared_value!r}")
    emit("status", message="旧名称已清空；正在输入并逐字核验目标昵称。")

    assert proxy.observation is not None
    proxy.call_tool(
        "input_text",
        {
            "text": nickname,
            "_observation_token": proxy.observation.token,
            "_intent": "rename exact default species using deterministic pixel IV nickname",
            "_expected_after": "rename field contains exact deterministic nickname",
            "_current_name": current_name,
            "_species": species,
            "_default_name_verified": True,
        },
    )
    entered_value = _field_value(proxy)
    if entered_value != nickname:
        raise PolicyViolation(
            f"输入后字段不完全一致：期望 {nickname!r}，实际 {entered_value!r}；未点击 OK"
        )
    emit("status", message="完整昵称逐字核验通过；正在提交。")

    base._tap(proxy, "RENAME_OK")
    detail = base._next_snapshot(proxy, 3.0)
    if proxy.verified_renames >= 1:
        return
    base._validate_expected("DETAIL", detail)

    base._tap(proxy, "NAME_PENCIL")
    try:
        committed_value = _field_value(proxy)
    except PolicyViolation:
        base._next_snapshot(proxy, 1.0)
        committed_value = _field_value(proxy)
    if committed_value != nickname:
        raise PolicyViolation(
            f"提交后重新打开字段核验失败：期望 {nickname!r}，实际 {committed_value!r}"
        )
    proxy.verified_renames += 1
    proxy.pending_name = None
    proxy.journal.append(
        "verified_rename_reopen",
        {"species": species, "old_name": current_name, "new_name": nickname},
    )
    _mark_rename_observation(proxy, "提交后 accessibility 已逐字核验")
    v5.cancel_name_field(proxy)


v7.exact_species_from_mcp_screenshot = exact_species_from_name_region
v7._commit_and_verify = _commit_and_verify


def main(argv: list[str] | None = None) -> int:
    return v7.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
