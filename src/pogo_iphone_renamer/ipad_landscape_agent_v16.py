from __future__ import annotations

import argparse
import time

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v7 as v7
from . import ipad_landscape_agent_v15 as v15
from .appraisal_agent import Snapshot
from .config import Settings
from .ipad_landscape_agent_v5 import exact_name_field
from .landscape_cv import rotate_mcp_image_upright
from .local_ocr import ocr_mcp_screenshot, rename_dialog_visible
from .local_ocr_v4 import (
    LocatedText,
    calibrated_name_location,
    locate_exact_name_from_mcp,
)
from .native_agent import emit
from .policy import PolicyViolation
from .server import SafeProxy
from .species_db import traditional_chinese_species


# This is a read-only delay after the pencil tap.  If the dialog has not
# appeared yet, the existing OCR/accessibility proof and one bounded retry
# remain unchanged.
_RENAME_DIALOG_INITIAL_READ_DELAY_SECONDS = 0.65
_POST_PENCIL_READ_ONLY_RECHECKS = 4
# The iPad's Stage Manager/MCP stack can acknowledge the pencil touch and
# publish the actual dialog only tens of seconds later.  These are reads only;
# keeping the same verified DETAIL frame avoids both a second touch and a
# false terminal failure while the transition is still in flight.
_CALIBRATED_PENCIL_DIALOG_RECHECKS = 54


class RenamePencilLocalizationUnavailable(PolicyViolation):
    """The verified detail page was readable, but its name row was not.

    This exception is deliberately limited to failures that happen before a
    pencil tap.  Batch mode can therefore preserve the current name and move
    on without guessing whether a rename dialog or keyboard is open.
    """

    def __init__(self, snapshot: Snapshot, cause: PolicyViolation) -> None:
        self.snapshot = snapshot
        self.cause = cause
        super().__init__(
            "详情页名称边界连续只读重测仍不可用；当前名称未触碰，可安全跳过"
        )


def dynamic_pencil_point(
    located: LocatedText,
    *,
    observation_width: float,
    observation_height: float,
    extra_gap: float = 33.0,
) -> tuple[float, float]:
    box = located.box
    image_width = float(located.image_width)
    image_height = float(located.image_height)
    if image_width <= 0 or image_height <= 0:
        raise PolicyViolation("详情页截图尺寸无效")
    if not (0.36 <= box.center_y / image_height <= 0.62):
        raise PolicyViolation("名称文字框不在详情页安全区域")
    upright_x = box.right + extra_gap
    upright_y = box.center_y
    x_ratio = upright_x / image_width
    y_ratio = upright_y / image_height
    if not (0.48 <= x_ratio <= 0.76 and 0.42 <= y_ratio <= 0.60):
        raise PolicyViolation("动态铅笔位置超出详情页安全区域")
    return observation_width * x_ratio, observation_height * y_ratio


def _dynamic_pencil_coordinates(
    proxy: SafeProxy,
    detail: Snapshot,
    current_name: str,
    *,
    extra_gap: float,
) -> tuple[float, float]:
    if not detail.image:
        raise PolicyViolation("详情页截图缺失，无法定位名称铅笔")
    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 未返回触控空间")
    base._remember_stage_geometry(proxy, detail)
    try:
        located = locate_exact_name_from_mcp(
            detail.image,
            base.ORIENTATION,
            current_name,
            minimum_confidence=0.70,
        )
    except PolicyViolation:
        # The current name was already exact-matched against the local species
        # database on the appraisal frame, and no navigation to another
        # Pokémon occurs before this DETAIL frame.  If even the targeted OCR
        # passes miss the small row, use the measured iPad14,6 centered-font
        # geometry rather than dropping an otherwise readable Pokémon.
        if current_name not in traditional_chinese_species():
            raise
        upright = rotate_mcp_image_upright(detail.image, base.ORIENTATION)
        located = calibrated_name_location(
            current_name,
            image_width=upright.width,
            image_height=upright.height,
        )
        emit(
            "status",
            message="多尺度 OCR 未返回名称框；已使用 iPad14,6 真机字体标定定位铅笔。",
        )
    x_ratio, y_ratio = dynamic_pencil_point(
        located,
        observation_width=1.0,
        observation_height=1.0,
        extra_gap=extra_gap,
    )
    return base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        x_ratio,
        y_ratio,
        geometry=base.current_stage_geometry(proxy),
    )


def _static_pencil_coordinates(
    proxy: SafeProxy,
    detail: Snapshot,
    *,
    detail_already_verified: bool = False,
) -> tuple[float, float]:
    """Map the device-calibrated pencil anchor into the active game window.

    The exact-name geometry is the preferred choice.  A few Stage Manager
    captures can, however, rescale the text glyph box by several pixels while
    leaving the actual pencil at the original calibrated anchor.  This is a
    last pre-input fallback after both dynamic positions were read and tapped
    without a dialog; it remains inside the verified name-row control.
    """

    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 未返回触控空间")
    if not detail_already_verified:
        _require_visual_detail(detail)
    base._remember_stage_geometry(proxy, detail)
    x_ratio, y_ratio, _label, _expected = base.ANCHORS["NAME_PENCIL"]
    return base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        x_ratio,
        y_ratio,
        geometry=base.current_stage_geometry(proxy),
    )


def _tap_dynamic_pencil_at(proxy: SafeProxy, x: float, y: float) -> None:
    observation = proxy.observation
    if observation is None:
        raise PolicyViolation("MCP 未返回安全观察")
    proxy.call_tool(
        "tap_screen",
        {
            "x": x,
            "y": y,
            "_observation_token": observation.token,
            "_intent": "navigate dynamic name pencil for verified rename dialog",
            "_expected_after": "RENAME_DIALOG",
        },
    )


def _require_visual_detail(snapshot: Snapshot) -> None:
    """Require the local game crop, never surrounding AX text, to show DETAIL.

    In the iPad Stage Manager layout, accessibility can temporarily describe
    unrelated window chrome while the screenshot crop has already proved the
    active Pokémon detail.  The generic ``_validate_expected`` path was
    designed for the inventory card transition and therefore reports its
    misleading “first card” error for that condition.  Dynamic-pencil work is
    already bound to one verified detail; use the calibrated local classifier
    that sees the same pixels used to locate the name row.
    """

    if base.local_page_state(snapshot) != "DETAIL":
        raise PolicyViolation("详情页局部像素证明暂不可用；不会定位或点击名称铅笔")


def _locate_dynamic_pencil_with_read_only_retry(
    proxy: SafeProxy,
    detail: Snapshot,
    current_name: str,
    *,
    extra_gap: float,
    attempts: int = 3,
    detail_already_verified: bool = False,
) -> tuple[Snapshot, tuple[float, float]]:
    """Locate the pencil from fresh screenshots without performing a tap."""

    last_error: PolicyViolation | None = None
    for attempt in range(1, attempts + 1):
        # A post-pencil detail was just proved by
        # _wait_for_dialog_or_detail_after_pencil.  Re-running a broader AX
        # validation on that identical image is both redundant and vulnerable
        # to Stage Manager's stale surrounding accessibility tree.
        if not (detail_already_verified and attempt == 1):
            _require_visual_detail(detail)
        try:
            point = _dynamic_pencil_coordinates(
                proxy, detail, current_name, extra_gap=extra_gap
            )
            if attempt > 1:
                emit(
                    "status",
                    message=f"名称边界在第 {attempt - 1} 次只读重测时恢复。",
                )
            return detail, point
        except PolicyViolation as exc:
            last_error = exc
            if attempt >= attempts:
                break
            emit(
                "status",
                message=(
                    f"名称边界第 {attempt} 帧暂时不可读；"
                    "仅刷新截图，不点击名称或铅笔。"
                ),
            )
            detail = base._next_snapshot(proxy, 0.6)
            detail_already_verified = False

    assert last_error is not None
    raise RenamePencilLocalizationUnavailable(detail, last_error) from last_error


def _verified_dialog_snapshot(
    proxy: SafeProxy, current_name: str
) -> Snapshot | None:
    time.sleep(_RENAME_DIALOG_INITIAL_READ_DELAY_SECONDS)
    image = v7._screenshot_only(proxy)
    lines = ocr_mcp_screenshot(image, base.ORIENTATION)
    verified_by_ocr = rename_dialog_visible(lines)
    verified_by_accessibility = False
    try:
        verified_by_accessibility = exact_name_field(
            Snapshot(proxy.observation.text if proxy.observation else "", None)
        ) == current_name
    except PolicyViolation:
        pass
    if not (verified_by_ocr or verified_by_accessibility):
        return None
    if proxy.observation is None:
        raise PolicyViolation("改名窗口缺少安全观察")
    evidence = "离线 OCR" if verified_by_ocr else "accessibility 名称字段"
    proxy.observation.text += (
        f"\n重新命名（{evidence}已验证；来源物种={current_name}）"
    )
    return Snapshot(text=proxy.observation.text, image=image)


def _wait_for_dialog_or_detail_after_pencil(
    proxy: SafeProxy,
    current_name: str,
    snapshot: Snapshot,
    *,
    detail_stability_rechecks: int = 1,
) -> Snapshot | None:
    """Resolve a post-pencil transition without issuing another tap.

    Stage Manager occasionally supplies a single stale MAP-classified frame
    immediately after a correctly targeted name-pencil tap.  The original
    code treated that one frame as a terminal failure even when the same
    Pokémon detail was back on the next capture.  Read through that transient
    state first; a delayed rename dialog wins over a second pencil tap.
    """

    candidate = snapshot
    checks = max(_POST_PENCIL_READ_ONLY_RECHECKS, detail_stability_rechecks)
    for attempt in range(1, checks + 1):
        verified = _verified_dialog_snapshot(proxy, current_name)
        if verified is not None:
            return verified
        if base.local_page_state(candidate) == "DETAIL":
            if attempt >= detail_stability_rechecks:
                return candidate
            emit(
                "status",
                message=(
                    f"铅笔点击后详情仍在；正在只读等待改名弹框完成（第 {attempt} 次复核）。"
                ),
            )
        if attempt < checks:
            emit(
                "status",
                message=(
                    f"铅笔点击后第 {attempt} 帧页面暂未稳定；"
                    "只读等待，不会在不明页面继续点击。"
                ),
            )
            candidate = base._next_snapshot(proxy, 0.8)
    return None


def open_dynamic_rename_from_detail(
    proxy: SafeProxy, detail: Snapshot, current_name: str
) -> Snapshot:
    _require_visual_detail(detail)
    for attempt, gap in enumerate((33.0, 45.0), start=1):
        detail, (x, y) = _locate_dynamic_pencil_with_read_only_retry(
            proxy,
            detail,
            current_name,
            extra_gap=gap,
            detail_already_verified=True,
        )
        _tap_dynamic_pencil_at(proxy, x, y)
        resolved = _wait_for_dialog_or_detail_after_pencil(
            proxy,
            current_name,
            base._next_snapshot(proxy, 0.4),
        )
        if resolved is not None and base.local_page_state(resolved) == "RENAME_DIALOG":
            emit(
                "status",
                message="已根据当前名称宽度定位铅笔，改名窗口验证通过。",
            )
            return resolved
        if resolved is None:
            raise PolicyViolation(
                "点击动态铅笔后连续只读等待仍未回到详情或验证改名弹窗；未输入文字"
            )
        # ``resolved`` has just been locally classified as DETAIL by the
        # read-only post-tap loop.  Carry that fresh frame forward: keeping
        # the pre-tap frame here can cause a transient stale-capture
        # classifier failure to prevent the calibrated fallback from running.
        detail = resolved
        if attempt == 1:
            emit(
                "status",
                message="详情页已在只读复核中恢复；刷新名称边界后重试一次铅笔点击。",
            )
            continue
        break

    x, y = _static_pencil_coordinates(
        proxy,
        detail,
        detail_already_verified=True,
    )
    emit(
        "status",
        message="动态名称边界两次未打开弹窗；改用已校准的详情页铅笔锚点复核一次。",
    )
    _tap_dynamic_pencil_at(proxy, x, y)
    resolved = _wait_for_dialog_or_detail_after_pencil(
        proxy,
        current_name,
        base._next_snapshot(proxy, 0.4),
        detail_stability_rechecks=_CALIBRATED_PENCIL_DIALOG_RECHECKS,
    )
    if resolved is not None and base.local_page_state(resolved) == "RENAME_DIALOG":
        emit(
            "status",
            message="已根据已校准详情页铅笔锚点，改名窗口验证通过。",
        )
        return resolved
    if resolved is None:
        raise PolicyViolation(
            "备用铅笔点击后连续只读等待仍未回到详情或验证改名弹窗；未输入文字"
        )
    raise PolicyViolation("动态与已校准备用铅笔均未打开已验证改名窗口；未输入文字")


def _open_verified_rename_dialog(
    proxy: SafeProxy, snapshot: Snapshot, current_name: str
) -> Snapshot:
    base._tap(proxy, "APPRAISAL_CLOSE")
    detail = base._next_snapshot(proxy)
    return open_dynamic_rename_from_detail(proxy, detail, current_name)


def run(mode: str, settings: Settings, ollama_url: str = "", model: str = "") -> int:
    previous = v7._open_verified_rename_dialog
    v7._open_verified_rename_dialog = _open_verified_rename_dialog
    try:
        return v15.run(mode, settings, ollama_url, model)
    finally:
        v7._open_verified_rename_dialog = previous


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pure-Python iPad landscape deterministic renamer v16"
    )
    parser.add_argument("--mode", choices=("scan", "rename"), required=True)
    args = parser.parse_args(argv)
    try:
        return run(args.mode, Settings.from_env())
    except Exception as exc:
        emit("error", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
