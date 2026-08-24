from __future__ import annotations

import argparse
import json
import re
import time
from typing import Any, Callable

from .appraisal_agent import Snapshot, StructuredVisionClient, screen_snapshot
from .config import Settings
from .deterministic_appraisal_agent import run as run_portrait
from .landscape_cv import (
    StageManagerGeometry,
    image_to_base64_jpeg,
    rotate_mcp_image_upright,
    set_preferred_stage_manager_geometry,
    stage_manager_geometry_from_base64,
    stage_manager_upright_ratio_to_touch,
)
from .landscape_cv_calibrated import measure_ipad14_6_appraisal
from .native_agent import emit
from .native_agent_v2 import ResilientStreamableHTTPClient
from .nickname import generate_iv_nickname, iv_percent
from .policy import PolicyViolation
from .server import SafeProxy


ORIENTATION = "STAGE_MANAGER_MAXIMIZED"

# Ratios are expressed in the native 1366x1024 landscape touch space.  The
# screenshot decoder independently accepts both the old rotated 1024x1366
# encoding and ios-mcp 1.2.3-blackframe1's native 1366x1024 encoding.
ANCHORS: dict[str, tuple[float, float, str, str]] = {
    "MAP": (0.5000, 0.9048, "精靈球主選單", "MAIN_MENU"),
    "MAIN_MENU": (0.2422, 0.7657, "寶可夢", "INVENTORY"),
    "INVENTORY": (0.1855, 0.4729, "第一只可见寶可夢", "DETAIL"),
    "DETAIL": (0.8955, 0.9217, "更多選單", "DETAIL_MENU"),
    "DETAIL_MENU": (0.8047, 0.7167, "寶可夢鑑定", "APPRAISAL"),
    "APPRAISAL_DIALOG": (0.5000, 0.9473, "鉴定对白", "APPRAISAL_BARS"),
    "APPRAISAL_CLOSE": (0.5000, 0.9473, "关闭鉴定", "DETAIL"),
    "DETAIL_CLOSE": (0.5000, 0.9473, "关闭详情返回宝可梦盒", "INVENTORY"),
    "POKEDEX_CLOSE": (0.5000, 0.9014, "关闭图鉴条目", "PREVIOUS_PAGE"),
    "POKEDEX_GRID_CLOSE": (0.5000, 0.7380, "关闭图鉴列表", "MAIN_MENU"),
    "NAME_PENCIL": (0.6006, 0.5081, "名称铅笔", "RENAME_DIALOG"),
    "RENAME_OK": (0.4980, 0.5622, "改名 OK", "DETAIL"),
    "RENAME_CANCEL": (0.4980, 0.6794, "取消改名", "DETAIL"),
}


IDENTITY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "species_text": {"type": "string"},
        "current_name": {"type": "string"},
        "default_name_verified": {"type": "boolean"},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": [
        "species_text",
        "current_name",
        "default_name_verified",
        "confidence",
        "reason",
    ],
    "additionalProperties": False,
}


def _device_details(result: dict[str, Any]) -> dict[str, Any]:
    structured = result.get("structuredContent")
    return structured if isinstance(structured, dict) else {}


def _normalized_text(snapshot: Snapshot) -> str:
    return snapshot.text.casefold().replace("\\/", "/")


def _bright_fraction(snapshot: Snapshot) -> float:
    if not snapshot.image:
        return 0.0
    image = rotate_mcp_image_upright(snapshot.image, ORIENTATION)
    sample = image.resize((96, 72))
    pixels = list(sample.getdata())
    return sum(1 for red, green, blue in pixels if min(red, green, blue) >= 205) / len(pixels)


def _detail_text_evidence(text: str) -> bool:
    """Recognize a detail page despite rotated OCR mangling the HP prefix.

    On the real landscape-right iPad, RapidOCR can read ``66/66 HP`` as
    ``dH66/66``.  CP + weight + height are independent, layout-specific fields
    that do not coexist on MAP or INVENTORY, so this conjunction remains a
    stronger page proof than depending on the two Latin HP letters.
    """

    folded = text.casefold().replace(",", ".")
    has_cp = bool(re.search(r"\bcp\s*\d+\b", folded))
    has_weight = bool(re.search(r"\b\d+(?:\.\d+)?\s*kg\b", folded))
    has_height = bool(re.search(r"\b\d+(?:\.\d+)?\s*m\b", folded))
    has_hp_fraction = bool(
        re.search(r"(?:[a-z]{0,2})?\d+\s*/\s*\d+(?:[a-z]{0,2})?", folded)
    )
    return has_cp and has_weight and has_height and has_hp_fraction


def local_page_state(snapshot: Snapshot) -> str:
    if snapshot.image:
        try:
            measure_ipad14_6_appraisal(snapshot.image, ORIENTATION)
            return "APPRAISAL_BARS"
        except ValueError:
            pass
    text = _normalized_text(snapshot)
    local_lines = ()
    if ORIENTATION == "STAGE_MANAGER_MAXIMIZED" and snapshot.image:
        # MCP accessibility/OCR describes the whole Stage Manager desktop and
        # often omits the rotated game window.  Reuse the canonicalized local
        # game crop so the base navigation cannot contradict the enhanced v14
        # classifier on the exact same pixels.
        try:
            from .local_ocr import ocr_mcp_screenshot

            local_lines = ocr_mcp_screenshot(snapshot.image, ORIENTATION)
            local_text = "\n".join(
                line.text for line in local_lines if line.confidence >= 0.45
            )
            if local_text:
                text = local_text.casefold()
        except Exception:
            pass
    # A rotated iPad Stage Manager capture often omits the keyboard's
    # accessibility labels (notably “清除文本”).  The complete trio is a
    # stronger, pixel-local proof of the Pokémon GO rename dialog than the
    # generic page classifier, which would otherwise fall through to MAP.
    # Keep this ahead of all detail/menu heuristics so a delayed post-pencil
    # dialog is resumed or cancelled safely instead of starting navigation.
    if local_lines:
        try:
            from .local_ocr import rename_dialog_visible

            if rename_dialog_visible(local_lines):
                return "RENAME_DIALOG"
        except Exception:
            pass
    if "清除文本" in text and ("完成" in text or "取消" in text):
        return "RENAME_DIALOG"
    if _detail_text_evidence(text):
        return "DETAIL"
    if re.search(r"\d{3,}\s*/\s*\d{3,}", text) and "hp" not in text:
        return "INVENTORY"
    if "hp" in text and ("kg" in text or re.search(r"\d+(?:\.\d+)?m\b", text)):
        return "DETAIL"
    # The current pale-green main menu occupies about 56% near-white pixels in
    # the canonical Stage Manager game crop.  The previous 58% cutoff rejected
    # a proven successful Poké Ball tap and mislabeled the open menu as MAP.
    # MAP frames on the same device measure about 3.5%, while inventory/detail
    # are classified by stronger evidence above, so 50% preserves a wide
    # separation and matches _validate_expected's existing safety threshold.
    if _bright_fraction(snapshot) >= 0.50:
        return "MAIN_MENU"
    return "MAP"


def _tap(proxy: SafeProxy, key: str) -> None:
    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 未返回屏幕边界")
    x_ratio, y_ratio, label, expected = ANCHORS[key]
    if ORIENTATION == "STAGE_MANAGER_MAXIMIZED" and observation.width > observation.height:
        x, y = upright_ratio_to_touch(
            observation.width,
            observation.height,
            x_ratio,
            y_ratio,
            geometry=current_stage_geometry(proxy),
        )
    else:
        x, y = observation.width * x_ratio, observation.height * y_ratio
    proxy.call_tool(
        "tap_screen",
        {
            "x": x,
            "y": y,
            "_observation_token": observation.token,
            "_intent": f"navigate calibrated iPad landscape {label}",
            "_expected_after": expected,
        },
    )


def upright_ratio_to_touch(
    observation_width: float,
    observation_height: float,
    x_ratio: float,
    y_ratio: float,
    *,
    geometry: StageManagerGeometry | None = None,
) -> tuple[float, float]:
    """Map canonical OCR/CV coordinates into the active touch window."""

    if ORIENTATION == "STAGE_MANAGER_MAXIMIZED" and observation_width > observation_height:
        if geometry is not None:
            return stage_manager_upright_ratio_to_touch(
                geometry,
                observation_width,
                observation_height,
                x_ratio,
                y_ratio,
            )
        left = observation_width * 0.0242
        top = observation_height * 0.2334
        width = observation_width * (0.9773 - 0.0242)
        height = observation_height * (0.7666 - 0.2334)
        # The portrait game surface is rotated clockwise in the landscape
        # Stage Manager window: portrait (x, y) -> window (1-y, x).
        return left + width * (1.0 - y_ratio), top + height * x_ratio
    return observation_width * x_ratio, observation_height * y_ratio


def _remember_stage_geometry(proxy: SafeProxy, snapshot: Snapshot) -> None:
    if ORIENTATION != "STAGE_MANAGER_MAXIMIZED" or not snapshot.image:
        return
    existing = getattr(proxy, "_stage_manager_geometry", None)
    set_preferred_stage_manager_geometry(
        existing if isinstance(existing, StageManagerGeometry) else None
    )
    try:
        geometry = stage_manager_geometry_from_base64(snapshot.image)
    except (OSError, ValueError) as exc:
        try:
            setattr(proxy, "_stage_manager_geometry", None)
            setattr(proxy, "_stage_manager_geometry_error", str(exc))
        except AttributeError:
            pass
        return
    try:
        setattr(proxy, "_stage_manager_geometry", geometry)
        setattr(proxy, "_stage_manager_geometry_error", None)
        set_preferred_stage_manager_geometry(geometry)
    except AttributeError:
        pass


def current_stage_geometry(proxy: SafeProxy) -> StageManagerGeometry:
    geometry = getattr(proxy, "_stage_manager_geometry", None)
    if not isinstance(geometry, StageManagerGeometry):
        reason = getattr(proxy, "_stage_manager_geometry_error", None)
        suffix = f"：{reason}" if reason else ""
        raise PolicyViolation(
            "当前截图未能安全定位 Stage Manager 中的 Pokémon GO 窗口；"
            f"未执行触控{suffix}"
        )
    return geometry


def _ensure_stage_geometry_for_state(
    proxy: SafeProxy,
    snapshot: Snapshot,
    expected_state: str,
    *,
    attempts: int = 3,
    state_reader: Callable[[Snapshot], str] | None = None,
) -> Snapshot:
    """Read fresh frames until geometry is stable without changing pages."""

    for attempt in range(1, attempts + 1):
        _remember_stage_geometry(proxy, snapshot)
        if isinstance(
            getattr(proxy, "_stage_manager_geometry", None), StageManagerGeometry
        ):
            if attempt > 1:
                emit(
                    "status",
                    message=(
                        f"Stage Manager 窗口边界在第 {attempt - 1} 次"
                        "只读重测时稳定。"
                    ),
                )
            return snapshot
        if attempt >= attempts:
            break
        refreshed = _next_snapshot(proxy, 0.6)
        refreshed_state = (state_reader or local_page_state)(refreshed)
        if refreshed_state != expected_state:
            raise PolicyViolation(
                "Stage Manager 窗口边界重测期间页面状态由 "
                f"{expected_state} 变为 {refreshed_state}；未执行触控"
            )
        snapshot = refreshed
    current_stage_geometry(proxy)
    raise AssertionError("unreachable")


def _next_snapshot(proxy: SafeProxy, delay: float = 2.5) -> Snapshot:
    time.sleep(delay)
    snapshot = screen_snapshot(proxy)
    _remember_stage_geometry(proxy, snapshot)
    return snapshot


def _validate_expected(state: str, snapshot: Snapshot) -> None:
    if ORIENTATION == "STAGE_MANAGER_MAXIMIZED" and state in {
        "MAIN_MENU",
        "INVENTORY",
        "DETAIL",
    }:
        # Accessibility text describes the Stage Manager desktop rather than
        # the rotated game surface.  Validate against the same canonical game
        # crop used by local_page_state so a successful close/navigation tap
        # cannot be rejected merely because MCP omitted Pokémon GO OCR text.
        actual = local_page_state(snapshot)
        if actual == state:
            return
        if state == "DETAIL" and actual == "APPRAISAL_BARS":
            raise PolicyViolation("鉴定条仍可见；不能将覆盖层当作详情页")
        messages = {
            "MAIN_MENU": "点击精灵球后没有验证到主菜单",
            "INVENTORY": "点击“寶可夢”后没有验证到宝可梦盒",
            "DETAIL": "点击第一张卡片后没有验证到详情页",
        }
        raise PolicyViolation(messages[state])

    text = _normalized_text(snapshot)
    if state == "MAIN_MENU" and _bright_fraction(snapshot) < 0.50:
        raise PolicyViolation("点击精灵球后没有验证到主菜单")
    if state == "INVENTORY" and not re.search(r"\d{3,}\s*/\s*\d{3,}", text):
        raise PolicyViolation("点击“寶可夢”后没有验证到宝可梦盒")
    if state == "DETAIL":
        if snapshot.image:
            try:
                measure_ipad14_6_appraisal(snapshot.image, ORIENTATION)
            except ValueError:
                pass
            else:
                raise PolicyViolation("鉴定条仍可见；不能将覆盖层当作详情页")
        if not ("hp" in text and "kg" in text):
            raise PolicyViolation("点击第一张卡片后没有验证到详情页")


def navigate_to_appraisal(proxy: SafeProxy, snapshot: Snapshot) -> tuple[Snapshot, Any]:
    state = local_page_state(snapshot)
    snapshot = _ensure_stage_geometry_for_state(proxy, snapshot, state)
    emit("navigation", state=state, orientation=ORIENTATION, step=1)
    order = ["MAP", "MAIN_MENU", "INVENTORY", "DETAIL", "DETAIL_MENU"]
    if state == "APPRAISAL_BARS":
        assert snapshot.image
        return snapshot, measure_ipad14_6_appraisal(snapshot.image, ORIENTATION)
    if state not in order:
        raise PolicyViolation(f"横屏状态机不支持当前起点：{state}")

    for step, current in enumerate(order[order.index(state) :], start=2):
        snapshot = _ensure_stage_geometry_for_state(proxy, snapshot, current)
        _tap(proxy, current)
        snapshot = _next_snapshot(proxy)
        expected = ANCHORS[current][3]
        emit("navigation", state=expected, orientation=ORIENTATION, step=step)
        _validate_expected(expected, snapshot)

    if not snapshot.image:
        # The MCP can occasionally return an empty capture immediately after
        # the Appraise transition.  This is a read-channel condition, not
        # evidence that navigation failed: callers with the v24 adapter catch
        # ValueError and perform bounded screenshot-only retries while leaving
        # the appraisal overlay untouched.
        raise ValueError("鉴定页截图缺失")
    # Selecting Appraise already opens the IV bars in the current game UI.  A
    # failed measurement here is normally just an animation frame.  The old
    # fallback tapped APPRAISAL_DIALOG, whose calibrated point is also the
    # close control once the appraisal overlay is open; that could close the
    # overlay and leave every subsequent read looking for bars on DETAIL.
    # Propagate the read failure so the v24 adapter can wait for fresh frames
    # without issuing another write.
    measurement = measure_ipad14_6_appraisal(snapshot.image, ORIENTATION)
    emit("navigation", state="APPRAISAL_BARS", orientation=ORIENTATION, step=step + 1)
    return snapshot, measurement


def identify_pokemon(vision: StructuredVisionClient, snapshot: Snapshot) -> dict[str, Any]:
    if not snapshot.image:
        raise PolicyViolation("无法读取名称：截图缺失")
    upright = rotate_mcp_image_upright(snapshot.image, ORIENTATION)
    width, height = upright.size
    crop = upright.crop(
        (
            round(width * 0.24),
            round(height * 0.04),
            round(width * 0.76),
            round(height * 0.62),
        )
    )
    return vision.analyze(
        prompt=(
            "这是已经旋正的 Pokémon GO 繁中详情/鉴定截图裁剪。"
            "读取名称文字，并根据宝可梦外观确认官方繁中物种名。"
            "current_name 是画面显示的完整当前名称；species_text 是官方繁中物种名。"
            "只有两者完全相同且没有自定义符号时，default_name_verified 才为 true。"
            "不要读取或猜测 IV。"
        ),
        image=image_to_base64_jpeg(crop),
        schema=IDENTITY_SCHEMA,
    )


def _mark_verified_rename_dialog(proxy: SafeProxy, current_name: str) -> None:
    observation = proxy.observation
    if observation is None:
        raise PolicyViolation("改名窗口缺少安全观察")
    text = observation.text
    if current_name not in text or not any(term in text for term in ("清除文本", "完成", "取消", '"OK"')):
        raise PolicyViolation("没有同时验证当前名称和改名控件")
    observation.text += "\n重新命名（由当前名称字段 + 清除文本/完成/取消/OK 控件联合验证）"


def rename_current(
    proxy: SafeProxy,
    snapshot: Snapshot,
    *,
    current_name: str,
    species: str,
    nickname: str,
) -> None:
    _tap(proxy, "APPRAISAL_CLOSE")
    snapshot = _next_snapshot(proxy)
    _validate_expected("DETAIL", snapshot)
    _tap(proxy, "NAME_PENCIL")
    snapshot = _next_snapshot(proxy)
    _mark_verified_rename_dialog(proxy, current_name)
    assert proxy.observation is not None
    proxy.call_tool(
        "input_text",
        {
            "text": nickname,
            "_observation_token": proxy.observation.token,
            "_intent": "rename default-name Pokemon using deterministic appraisal IV nickname",
            "_expected_after": "rename field contains exact deterministic nickname",
            "_current_name": current_name,
            "_species": species,
            "_default_name_verified": True,
        },
    )
    time.sleep(1.0)
    _tap(proxy, "RENAME_OK")
    snapshot = _next_snapshot(proxy, 3.0)
    if proxy.verified_renames >= 1:
        return

    # Reopen the field and verify the exact committed string through iOS
    # accessibility. This is stronger than unreliable rotated-screen OCR.
    _tap(proxy, "NAME_PENCIL")
    snapshot = _next_snapshot(proxy)
    if nickname not in snapshot.text:
        raise PolicyViolation("提交后重新打开名称字段，未发现完整目标昵称")
    proxy.verified_renames += 1
    proxy.pending_name = None
    proxy.journal.append(
        "verified_rename_reopen",
        {"species": species, "old_name": current_name, "new_name": nickname},
    )
    _tap(proxy, "RENAME_CANCEL")
    _next_snapshot(proxy, 1.5)


def run(mode: str, settings: Settings, ollama_url: str, model: str) -> int:
    client = ResilientStreamableHTTPClient(settings, timeout=120.0)
    device = _device_details(client.call_tool("get_device_info", {}))
    machine = str(device.get("machine", ""))
    if machine != "iPad14,6":
        emit("status", message=f"设备 {machine or '未知'} 不使用 iPad14,6 横屏配置，转入竖屏执行器。")
        return run_portrait(mode, settings, ollama_url, model)

    emit(
        "device",
        name=str(device.get("deviceName", "iPad")),
        machine=machine,
        system=str(device.get("systemName", "iPadOS")),
        version=str(device.get("systemVersion", "")),
        width=device.get("screenWidth"),
        height=device.get("screenHeight"),
    )
    emit("status", message="iPad14,6 横屏归一化触控配置已启用；导航不使用模型坐标。")
    proxy = SafeProxy(settings, client)
    vision = StructuredVisionClient(ollama_url, model)
    snapshot = screen_snapshot(proxy)
    snapshot, measurement = navigate_to_appraisal(proxy, snapshot)
    if measurement.confidence < 0.90:
        raise PolicyViolation(f"IV 像素测量置信度不足：{measurement.confidence:.1%}")

    emit(
        "iv_measurement",
        attack=measurement.attack,
        defense=measurement.defense,
        stamina=measurement.stamina,
        confidence=measurement.confidence,
        endpoints=list(measurement.endpoints),
    )
    identity = identify_pokemon(vision, snapshot)
    if float(identity["confidence"]) < 0.85:
        raise PolicyViolation(f"物种名称识别置信度不足：{identity['reason']}")
    species = str(identity["species_text"]).strip()
    current_name = str(identity["current_name"]).strip()
    nickname = generate_iv_nickname(
        species,
        measurement.attack,
        measurement.defense,
        measurement.stamina,
    )
    emit(
        "pokemon",
        species=species,
        current_name=current_name,
        attack=measurement.attack,
        defense=measurement.defense,
        stamina=measurement.stamina,
        percent=iv_percent(measurement.attack, measurement.defense, measurement.stamina),
        nickname=nickname,
        confidence=min(measurement.confidence, float(identity["confidence"])),
    )
    if mode == "scan":
        emit("finished", message="横屏鉴定扫描完成；没有修改昵称。")
        return 0
    if not identity["default_name_verified"] or current_name != species:
        emit("finished", message="当前名称未验证为完整默认物种名，已保留并跳过。")
        return 0
    rename_current(
        proxy,
        snapshot,
        current_name=current_name,
        species=species,
        nickname=nickname,
    )
    emit("renamed", nickname=nickname)
    emit("finished", message=f"横屏改名并复核成功：{nickname}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="iPad landscape Pokémon GO appraisal scanner")
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
