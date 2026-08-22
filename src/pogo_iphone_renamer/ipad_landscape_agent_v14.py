from __future__ import annotations

import argparse
import re
import time
from typing import Any

from PIL import ImageChops, ImageStat

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v13 as v13
from .appraisal_agent import Snapshot
from .config import Settings
from .landscape_cv import rotate_mcp_image_upright
from .local_ocr import ocr_mcp_screenshot
from .native_agent import emit
from .policy import PolicyViolation
from .server import SafeProxy


_ORIGINAL_NAVIGATE = base.navigate_to_appraisal
_NEXT_STATE = {
    "MAP": "MAIN_MENU",
    "MAIN_MENU": "INVENTORY",
    "INVENTORY": "DETAIL",
}


def storage_capacity_visible(text: str) -> bool:
    """Accept storage counters with or without locale thousands separators."""
    for match in re.finditer(
        r"(?<![\w.])([0-9][0-9,. ]{0,11})\s*/\s*([0-9][0-9,. ]{0,11})(?![\w.])",
        text,
    ):
        left_digits = re.sub(r"\D", "", match.group(1))
        right_digits = re.sub(r"\D", "", match.group(2))
        if not left_digits or not right_digits:
            continue
        used, capacity = int(left_digits), int(right_digits)
        if capacity >= 200 and used <= capacity + 10:
            return True
    return False


def snapshot_is_black(snapshot: Snapshot) -> bool:
    if not snapshot.image:
        return True
    image = rotate_mcp_image_upright(snapshot.image, base.ORIENTATION).resize((64, 48))
    extrema = image.getextrema()
    return max(high for _low, high in extrema) <= 8


def perceptual_change(before: Snapshot, after: Snapshot) -> float:
    if not before.image or not after.image:
        return 1.0
    first = rotate_mcp_image_upright(before.image, base.ORIENTATION).resize((64, 48))
    second = rotate_mcp_image_upright(after.image, base.ORIENTATION).resize((64, 48))
    difference = ImageChops.difference(first, second)
    return sum(ImageStat.Stat(difference).mean) / (3.0 * 255.0)


def _inventory_text_evidence(text: str) -> bool:
    folded = text.casefold()
    if storage_capacity_visible(folded):
        return True
    has_pokemon = (
        "寶可夢" in folded
        or "宝可梦" in folded
        or "pokémon" in folded
        or "pokemon" in folded
    )
    has_search = "搜尋" in folded or "搜索" in folded or "search" in folded
    has_tag = any(token in folded for token in ("標籤", "标签", "tag"))
    has_egg = any(token in folded for token in ("蛋", "egg"))
    return has_pokemon and has_search and has_tag and has_egg


def _fresh_screenshot_text(snapshot: Snapshot) -> tuple[str, bool]:
    """Return OCR tied to the current pixels, never stale accessibility text."""

    if not snapshot.image or snapshot_is_black(snapshot):
        return "", False
    try:
        return "\n".join(
            line.text
            for line in ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
            if line.confidence >= 0.45
        ), True
    except Exception:
        return "", False


def inventory_visible(snapshot: Snapshot) -> bool:
    local_text, fresh = _fresh_screenshot_text(snapshot)
    if fresh:
        return _inventory_text_evidence(local_text)
    return _inventory_text_evidence(snapshot.text)


def _pokedex_detail_text_evidence(text: str) -> bool:
    folded = re.sub(r"\s+", "", text).casefold()
    has_seen = any(token in folded for token in ("有見過", "有见过", "seen"))
    has_caught = any(token in folded for token in ("已捕捉", "caught"))
    has_entry_control = any(
        token in folded
        for token in ("關閉通知", "关闭通知", "通知", "notification")
    )
    return has_seen and has_caught and has_entry_control


def _pokedex_grid_text_evidence(text: str) -> bool:
    folded = text.casefold()
    compact = re.sub(r"\s+", "", folded)
    has_search = any(token in compact for token in ("搜尋", "搜索", "search"))
    species_numbers = re.findall(r"(?<!\d)0\d{3}(?!\d)", folded)
    has_grid_filters = any(
        token in compact
        for token in ("異色", "异色", "亮晶晶", "xxl", "xxs", "地區", "地区")
    )
    return has_search and len(set(species_numbers)) >= 3 and has_grid_filters


def _pokedex_index_text_evidence(text: str) -> bool:
    folded = text.casefold()
    compact = re.sub(r"\s+", "", folded)
    has_title = any(
        token in compact for token in ("寶可夢圖鑑", "宝可梦图鉴", "pokédex", "pokedex")
    )
    region_count = sum(
        token in compact
        for token in (
            "關都",
            "关都",
            "城都",
            "豐緣",
            "丰缘",
            "神奧",
            "神奥",
            "合眾",
            "合众",
            "卡洛斯",
            "阿羅拉",
            "阿罗拉",
            "伽勒爾",
            "伽勒尔",
            "帕底亞",
            "帕底亚",
        )
    )
    progress_count = len(
        re.findall(r"(?<!\d)\d{2,3}\s*/\s*\d{2,3}(?!\d)", folded)
    )
    has_caught_total = any(token in compact for token in ("已捕捉", "caught"))
    return has_title and region_count >= 2 and (
        progress_count >= 2 or has_caught_total
    )


def detail_record_overlay_visible(snapshot: Snapshot) -> bool:
    if not snapshot.image:
        return False
    try:
        texts = [
            re.sub(r"\s+", "", line.text).casefold()
            for line in ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
            if line.confidence >= 0.70
        ]
    except Exception:
        return False
    has_record_title = any(
        token in text
        for text in texts
        for token in ("身高新紀錄", "身高新纪录", "體重新紀錄", "体重新纪录")
    )
    has_cp = any(re.fullmatch(r"cp\d+", text) for text in texts)
    has_metric = any(re.fullmatch(r"\d+(?:[.,]\d+)?m", text) for text in texts)
    return has_record_title and has_cp and has_metric


def robust_page_state(snapshot: Snapshot) -> str:
    if snapshot_is_black(snapshot):
        return "LOADING"
    local_text, fresh = _fresh_screenshot_text(snapshot)
    visual_snapshot = (
        Snapshot(text=local_text, image=snapshot.image) if fresh else snapshot
    )
    if _inventory_text_evidence(visual_snapshot.text):
        return "INVENTORY"
    if _pokedex_detail_text_evidence(visual_snapshot.text):
        return "POKEDEX_DETAIL"
    if _pokedex_grid_text_evidence(visual_snapshot.text):
        return "POKEDEX_REGION_GRID"
    if _pokedex_index_text_evidence(visual_snapshot.text):
        return "POKEDEX_INDEX"
    return base.local_page_state(visual_snapshot)


def _close_detail_record_overlay_if_needed(
    proxy: SafeProxy, snapshot: Snapshot
) -> tuple[Snapshot, bool]:
    if not detail_record_overlay_visible(snapshot):
        return snapshot, False
    emit(
        "status",
        message="检测到详情页身高/体重新纪录覆盖层；关闭覆盖层后重新验证详情页。",
    )
    snapshot = base._ensure_stage_geometry_for_state(
        proxy,
        snapshot,
        "DETAIL",
        state_reader=robust_page_state,
    )
    base._tap(proxy, "APPRAISAL_CLOSE")
    detail = base._next_snapshot(proxy, 1.25)
    if robust_page_state(detail) != "DETAIL":
        raise PolicyViolation("关闭新纪录覆盖层后未验证到详情页；已停止")
    return detail, True


def _target_reached(expected: str, snapshot: Snapshot) -> tuple[bool, str]:
    state = robust_page_state(snapshot)
    if expected == "INVENTORY":
        reached = inventory_visible(snapshot)
        return reached, "INVENTORY" if reached else state
    if expected == "MAIN_MENU":
        # The menu button and the storage tile can occasionally consume one
        # buffered tap during a slow transition.  INVENTORY is the intended
        # immediate downstream page, so accepting it is safer than treating a
        # successfully reached storage screen as a fatal navigation failure.
        return state in {"MAIN_MENU", "INVENTORY"}, state
    if expected == "DETAIL":
        return state == "DETAIL", state
    return state == expected, state


def _wait_for_target(
    proxy: SafeProxy,
    *,
    expected: str,
    before: Snapshot,
    timeout: float = 12.0,
) -> tuple[Snapshot, str, float]:
    deadline = time.monotonic() + timeout
    last = before
    observed = robust_page_state(before)
    record_overlay_closed = False
    while time.monotonic() < deadline:
        last = base._next_snapshot(proxy, 1.25)
        if (
            expected == "DETAIL"
            and not record_overlay_closed
            and detail_record_overlay_visible(last)
        ):
            last, record_overlay_closed = _close_detail_record_overlay_if_needed(
                proxy, last
            )
        reached, observed = _target_reached(expected, last)
        if reached:
            return last, observed, perceptual_change(before, last)
        # If a delayed or user transition is already on the detail page, never
        # replay the storage tap on that new screen.
        if expected == "INVENTORY" and observed == "DETAIL":
            return last, observed, perceptual_change(before, last)
    return last, observed, perceptual_change(before, last)


def _safe_same_page_retry(source: str, observed: str, change: float) -> bool:
    if observed != source:
        return False
    # The map and storage grid both contain ambient animation.  A large pixel
    # delta therefore does not imply that the semantic page changed.  Retrying
    # their calibrated target once remains safe only when both classifiers
    # still identify the exact same source page.  Other pages retain the
    # stricter unchanged-frame rule.
    return source in {"MAP", "INVENTORY"} or change < 0.015


def _transition(proxy: SafeProxy, snapshot: Snapshot, source: str) -> tuple[Snapshot, str]:
    expected = _NEXT_STATE[source]
    for write_attempt in range(2):
        snapshot = base._ensure_stage_geometry_for_state(
            proxy,
            snapshot,
            source,
            state_reader=robust_page_state,
        )
        before = snapshot
        base._tap(proxy, source)
        snapshot, observed, change = _wait_for_target(
            proxy,
            expected=expected,
            before=before,
        )
        if observed == expected or (
            expected == "MAIN_MENU" and observed == "INVENTORY"
        ) or (expected == "INVENTORY" and observed == "DETAIL"):
            emit(
                "navigation",
                state=observed,
                orientation=base.ORIENTATION,
                step=write_attempt + 2,
            )
            return snapshot, observed

        if write_attempt == 0 and _safe_same_page_retry(source, observed, change):
            emit(
                "status",
                message=(
                    f"{source} 页面语义状态确认未变化；"
                    "刷新观察后有界重试一次导航点击。"
                ),
            )
            continue

        if change >= 0.015:
            raise PolicyViolation(
                f"点击后画面已变化，但目标 {expected} 的双通道证据不足（检测={observed}）；"
                "为避免在新页面重复点击，已停止"
            )
        raise PolicyViolation(
            f"页面在等待 12 秒后仍为 {observed}，未到达 {expected}；已停止"
        )
    raise PolicyViolation(f"无法从 {source} 安全进入 {expected}")


def _wait_until_visible(proxy: SafeProxy, snapshot: Snapshot) -> Snapshot:
    if not snapshot_is_black(snapshot):
        return snapshot
    emit("status", message="检测到游戏加载黑屏；只读取等待画面出现，不执行坐标点击。")
    deadline = time.monotonic() + 45.0
    while time.monotonic() < deadline:
        snapshot = base._next_snapshot(proxy, 2.0)
        if not snapshot_is_black(snapshot):
            emit("status", message="游戏画面已恢复，开始识别当前页面。")
            return snapshot
    raise PolicyViolation("Pokémon GO 持续黑屏 45 秒；未把黑屏误判为地图，也未执行点击")


def navigate_to_appraisal_v14(proxy: SafeProxy, snapshot: Snapshot) -> tuple[Snapshot, Any]:
    snapshot = _wait_until_visible(proxy, snapshot)
    snapshot, _closed_record_overlay = _close_detail_record_overlay_if_needed(
        proxy, snapshot
    )
    state = robust_page_state(snapshot)
    emit("navigation", state=state, orientation=base.ORIENTATION, step=1)

    for recovery_step in range(3):
        if state not in {
            "POKEDEX_DETAIL",
            "POKEDEX_REGION_GRID",
            "POKEDEX_INDEX",
        }:
            break
        previous_state = state
        label = {
            "POKEDEX_DETAIL": "图鉴条目",
            "POKEDEX_REGION_GRID": "地区物种网格",
            "POKEDEX_INDEX": "地区图鉴索引",
        }[state]
        emit(
            "status",
            message=f"检测到误留在{label}；只点击底部关闭键一次并重新识别返回页。",
        )
        close_key = (
            "POKEDEX_CLOSE"
            if state == "POKEDEX_DETAIL"
            else "POKEDEX_GRID_CLOSE"
        )
        snapshot = base._ensure_stage_geometry_for_state(
            proxy,
            snapshot,
            state,
            state_reader=robust_page_state,
        )
        base._tap(proxy, close_key)
        snapshot = base._next_snapshot(proxy, 1.5)
        state = robust_page_state(snapshot)
        if state == previous_state:
            raise PolicyViolation(f"关闭{label}后页面未变化；未重复点击")
        emit(
            "navigation",
            state=state,
            orientation=base.ORIENTATION,
            step=recovery_step + 2,
        )
    if state in {"POKEDEX_DETAIL", "POKEDEX_REGION_GRID", "POKEDEX_INDEX"}:
        raise PolicyViolation("图鉴三层关闭恢复后仍未返回可支持页面；未继续点击")

    for _ in range(4):
        if state not in _NEXT_STATE:
            break
        snapshot, state = _transition(proxy, snapshot, state)

    if state != "DETAIL" and state not in {"DETAIL_MENU", "APPRAISAL_BARS"}:
        raise PolicyViolation(f"入口导航停在不支持的页面：{state}")
    return _ORIGINAL_NAVIGATE(proxy, snapshot)


def run(mode: str, settings: Settings, ollama_url: str, model: str) -> int:
    previous = base.navigate_to_appraisal
    base.navigate_to_appraisal = navigate_to_appraisal_v14
    try:
        return v13.run(mode, settings, ollama_url, model)
    finally:
        base.navigate_to_appraisal = previous


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="iPad landscape resilient Pokémon GO renamer v14")
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
