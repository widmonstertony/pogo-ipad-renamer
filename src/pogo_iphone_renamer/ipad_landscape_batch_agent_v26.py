from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import time
from pathlib import Path

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v14 as v14
from .appraisal_agent import Snapshot, screen_snapshot
from .batch_navigation_v26 import (
    DetailExitedToOverview,
    DetailFingerprint,
    NEXT_PAGER_DIRECTION,
    NoNextPokemon,
    VerifiedNextDetail,
    VerifiedEndOfStorage,
    detail_fingerprint,
    fingerprints_differ,
    swipe_to_verified_next,
    wait_for_stable_detail_fingerprint,
)
from .batch_pause import BatchPauseFile
from .config import BATCH_LIMIT_UNLIMITED, Settings
from .device_run_lock import DeviceRunLock
from .ipad_landscape_agent_v16 import (
    RenamePencilLocalizationUnavailable,
    _static_pencil_coordinates,
    _tap_dynamic_pencil_at,
    _wait_for_dialog_or_detail_after_pencil,
    open_dynamic_rename_from_detail,
)
from .ipad_landscape_agent_v5 import exact_name_field
from .ipad_landscape_agent_v15 import (
    DeviceLockRecoveryRequired,
    wait_for_capture_channel,
    wait_for_unlocked_snapshot,
)
from .ipad_landscape_agent_v22 import (
    RenameFieldVerificationUnavailable,
    _cancel_unverified_input,
    _commit_after_dismissing_keyboard,
    _dialog_evidence_after_keyboard_dismiss,
    _submit_with_one_verified_retry,
)
from .ipad_landscape_agent_v20 import _verified_entered_value
from .ipad_landscape_agent_v24 import (
    AppraisalMeasurementUnavailable,
    _navigate_with_read_only_measurement_retry,
)
from .ipad_landscape_agent_v25 import _navigate_with_complete_stale_recovery
from .landscape_cv import is_fullscreen_portrait_game_frame, rotate_mcp_image_upright
from .landscape_cv_v6 import measure_ipad14_6_appraisal_v6
from .live_activity import publish_preview
from .local_ocr import exact_species_from_lines, ocr_mcp_screenshot, rename_dialog_visible
from .local_ocr_v4 import locate_exact_text_from_mcp
from .local_ocr_v3 import (
    HP_LINE,
    NUMBER_TOKEN,
    NameRegionResult,
    analyze_name_region,
    is_detail_status_label,
)
from .native_agent import emit
from .native_agent_v2 import ResilientStreamableHTTPClient
from .nickname import generate_iv_nickname, iv_percent
from .policy import ANNOTATION_CHARS, PolicyViolation
from .protocol import text_from_content
from .server import SafeProxy
from .species_db import traditional_chinese_species


base.measure_ipad14_6_appraisal = measure_ipad14_6_appraisal_v6


_CONSENSUS_MEASUREMENT_CONFIDENCE = 0.80
_MEASUREMENT_READ_ONLY_RETRIES = 12
_DETAIL_IDENTITY_READ_ONLY_RETRIES = 12
_FRESH_FRAME_HISTORY_LIMIT = 512
_MAX_TRANSIENT_NAVIGATION_RECOVERIES = 3
_MAX_READ_SESSION_NAVIGATION_RECOVERIES = 3
_UNSAFE_STAGE_MANAGER_GEOMETRY = "detected Stage Manager game-window geometry is unsafe"
# Faster initial read-only sampling on the calibrated iPad.  These never
# reduce the number of required distinct proof frames: an unsettled capture
# simply falls through to the existing bounded retry loops.
_DETAIL_IDENTITY_FAST_READ_DELAY_SECONDS = 0.8
_MEASUREMENT_FAST_READ_DELAY_SECONDS = 0.9
_CLOSE_APPRAISAL_FAST_READ_DELAY_SECONDS = 1.0
_CALIBRATED_LANDSCAPE_DEVICES = frozenset({"iPad14,6", "iPad7,2"})
_CALIBRATED_LANDSCAPE_POINTS = (1366, 1024)
_INVENTORY_CP_TOKEN = re.compile(r"^cp\s*(\d+)$", re.IGNORECASE)
# CP labels identify the card; a measured offset reaches the card's stable
# middle hit area. ``upright_ratio_to_touch`` applies iPad7,2's HID rotation
# at the write edge, so this stays on the same logical card.
_INVENTORY_CARD_TAP_OFFSET = 0.16


class FreshDetailIdentityUnavailable(PolicyViolation):
    """The current card was never proven from fresh detail reads.

    This is deliberately different from an unreadable appraisal: without a
    fresh detail identity, there is no evidence that a card was processed at
    all.  The batch loop must reset only its read session and retry the same
    index instead of incrementing the progress counter or paging again.
    """

    def __init__(self, snapshot: Snapshot) -> None:
        super().__init__("当前详情未取得三张新鲜身份帧")
        self.snapshot = snapshot


def _pager_resume_path(settings: Settings) -> Path:
    """Local-only handoff recorded immediately before a verified pager attempt."""

    return settings.journal_path.parent / "pager-resume.json"


def _save_pager_direction(settings: Settings, direction: str | None) -> None:
    if direction not in {"left", "right"}:
        return
    destination = settings.journal_path.parent / "pager-direction.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps({"mcp_url": settings.mcp_url, "direction": direction}), encoding="utf-8")
    temporary.replace(destination)


def _load_pager_direction(settings: Settings) -> str | None:
    try:
        payload = json.loads((settings.journal_path.parent / "pager-direction.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("mcp_url") != settings.mcp_url:
        return None
    direction = payload.get("direction")
    return direction if direction in ("left", "right") else None


def _save_pager_resume(settings: Settings, fingerprint: DetailFingerprint) -> None:
    """Persist only immutable navigation evidence, never a rename decision."""

    destination = _pager_resume_path(settings)
    payload = {
        "version": 1,
        "fingerprint": {
            "name_tokens": list(fingerprint.name_tokens),
            "cp": fingerprint.cp,
            "hp": fingerprint.hp,
            "weight": fingerprint.weight,
            "height": fingerprint.height,
        },
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary.replace(destination)


def _load_pager_resume(settings: Settings) -> DetailFingerprint | None:
    """Return a prior proven detail only when its CP is structurally safe."""

    try:
        payload = json.loads(_pager_resume_path(settings).read_text(encoding="utf-8"))
        values = payload["fingerprint"]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(values, dict):
        return None
    cp = str(values.get("cp", "")).casefold().replace(" ", "")
    if not _INVENTORY_CP_TOKEN.fullmatch(cp):
        return None
    raw_tokens = values.get("name_tokens", [])
    tokens = (
        tuple(str(value) for value in raw_tokens if str(value).strip())
        if isinstance(raw_tokens, list)
        else ()
    )
    return DetailFingerprint(
        tokens,
        cp,
        str(values.get("hp", "")),
        str(values.get("weight", "")),
        str(values.get("height", "")),
    )


def _clear_pager_resume(settings: Settings) -> None:
    """Forget the handoff only after a distinct next detail was verified."""

    try:
        _pager_resume_path(settings).unlink()
    except FileNotFoundError:
        pass


def _is_calibrated_landscape_device(device: dict[str, object]) -> bool:
    """Accept only explicitly calibrated models at the exact touch geometry.

    iPad7,2 reports the same 1366×1024 landscape point space as the existing
    iPad14,6 capture profile.  The visual locator is point-space based, so
    both models are safe to use only when MCP confirms that exact geometry.
    Unknown models or any other size remain blocked before a touch.
    """

    if str(device.get("machine", "")) not in _CALIBRATED_LANDSCAPE_DEVICES:
        return False
    try:
        width = int(device.get("screenWidth", 0))
        height = int(device.get("screenHeight", 0))
    except (TypeError, ValueError):
        return False
    return (width, height) == _CALIBRATED_LANDSCAPE_POINTS


def _matches_configured_device(device: dict[str, object]) -> bool:
    """Reject an endpoint that resolves to a different user-paired iPad."""

    expected = os.getenv("POGO_EXPECTED_DEVICE_MACHINE", "").strip()
    return not expected or str(device.get("machine", "")) == expected


def _orientation_for_calibrated_device(device: dict[str, object]) -> str:
    """Choose the verified screenshot encoding for each calibrated device."""

    # iPad7,2's iPadOS 17 MCP returns a full Stage Manager desktop encoded as
    # 1024×1366 pixels, with an upright portrait game window inside it.
    # iPad14,6 instead returns the old sideways-game card encoding.
    return (
        "STAGE_MANAGER_PORTRAIT_WINDOW"
        if str(device.get("machine", "")) == "iPad7,2"
        else "STAGE_MANAGER_MAXIMIZED"
    )


def _publish_live_snapshot(snapshot: Snapshot) -> Snapshot:
    """Refresh the desktop preview from an iPad frame we already captured."""

    publish_preview(snapshot.image, orientation=base.ORIENTATION)
    return snapshot


def _adopt_current_capture_profile(snapshot: Snapshot) -> bool:
    """Use the live MCP encoding, not a stale device-model assumption.

    iPad7,2 can change between an inset Stage Manager desktop and a direct
    full-screen portrait game capture without changing its device-info point
    bounds. The latter must be recognised before any name OCR or pager
    gesture. This is a local pixel classification only and never touches the
    iPad.
    """

    if base.ORIENTATION != "STAGE_MANAGER_PORTRAIT_WINDOW" or not snapshot.image:
        return False
    if not is_fullscreen_portrait_game_frame(snapshot.image):
        return False
    base.ORIENTATION = "PORTRAIT_FULLSCREEN"
    emit(
        "status",
        message=(
            "已检测到 iPad MCP 的全屏竖屏游戏帧；已切换到直连识别与横向翻页坐标。"
        ),
    )
    return True


def _snapshot_digest(snapshot: Snapshot) -> str:
    if not snapshot.image:
        raise PolicyViolation("截图缺失，无法验证帧新鲜度")
    return hashlib.sha256(base64.b64decode(snapshot.image)).hexdigest()


def _independent_read_key(snapshot: Snapshot, digest: str) -> str:
    """Use a fresh MCP capture id when iPadOS emits identical pixels."""

    return f"capture:{snapshot.capture_id}" if snapshot.capture_id else f"pixel:{digest}"


def _frame_history(proxy: SafeProxy) -> list[str]:
    history = getattr(proxy, "_pogo_verified_frame_history", None)
    if isinstance(history, list):
        return history
    history = []
    try:
        setattr(proxy, "_pogo_verified_frame_history", history)
    except AttributeError:
        pass
    return history


def _remember_fresh_frames(proxy: SafeProxy, digests: list[str]) -> None:
    history = _frame_history(proxy)
    for digest in digests:
        if digest not in history:
            history.append(digest)
    if len(history) > _FRESH_FRAME_HISTORY_LIMIT:
        del history[:-_FRESH_FRAME_HISTORY_LIMIT]


def _name_agnostic_navigation_fingerprint(
    snapshot: Snapshot,
) -> DetailFingerprint | None:
    """Keep only immutable detail fields for an already-confirmed detail.

    The helper is deliberately name-free: configured nicknames can truncate a
    species title, and custom names must never be guessed.  Its caller may use
    the result only after either a committed-and-verified rename or a
    three-frame custom-name skip.  The returned values prove that a later
    swipe changed detail pages; they can never authorize a rename.
    """

    try:
        fingerprint = detail_fingerprint(snapshot, require_name=False)
    except PolicyViolation:
        return None
    fallback = DetailFingerprint(
        (),
        fingerprint.cp,
        fingerprint.hp,
        fingerprint.weight,
        fingerprint.height,
    )
    return fallback if any(fallback.stable_fields()[1:]) else None


def _visual_navigation_fingerprint(snapshot: Snapshot) -> DetailFingerprint:
    """Build a navigation fallback when numeric identity OCR is completely
    unavailable.

    Some iPadOS frames remain visually stable but have no reliable CP/HP/size
    text (for example fast animations or an aggressive OCR retry loop). Those
    frames must still permit one safe swipe when the user asked for continuous
    unattended operation. Use the capture id or image digest so navigation can
    still require a changed screen before reusing the same frame as proof.
    """

    marker = snapshot.capture_id
    if not marker and snapshot.image:
        marker = _snapshot_digest(snapshot)[:16]
    if marker is None:
        marker = ""
    marker = str(marker)
    if not marker:
        marker = "unreadable"
    # Two non-empty fields keep this usable as a navigation key while
    # making it clear this is a read-only proof, not a rename-safe identity.
    return DetailFingerprint((), marker, marker, "", "")


def _swipe_to_verified_next_with_read_recovery(
    proxy: SafeProxy,
    detail: Snapshot,
    fingerprint: DetailFingerprint,
    *,
    allow_opposite_direction: bool = False,
) -> VerifiedNextDetail:
    """Retry a bounded pager proof after resetting only the MCP read session.

    An iPad-side screenshot stream can retain an old or blank image even
    though Pokémon GO remains visibly responsive. A failed proof must never
    become an unbounded wait or an unverified extra gesture. This helper first
    completes the normal bounded pager checks, then resets only the local MCP
    read session, freshly re-proves the same current detail, and tries again.
    It never relaunches the game or changes Pokémon data.
    """

    last_error: NoNextPokemon | None = None
    current_detail = detail
    current_fingerprint = fingerprint
    for recovery in range(_MAX_READ_SESSION_NAVIGATION_RECOVERIES + 1):
        try:
            return swipe_to_verified_next(
                proxy,
                current_detail,
                before=current_fingerprint,
                allow_opposite_direction=allow_opposite_direction,
            )
        except (VerifiedEndOfStorage, DetailExitedToOverview):
            raise
        except NoNextPokemon as exc:
            last_error = exc
            if recovery >= _MAX_READ_SESSION_NAVIGATION_RECOVERIES:
                break
            emit(
                "waiting",
                stage="翻页读取会话恢复",
                reason=(
                    "截图流未能在限定采样内证明下一只；正在重置只读截图会话，"
                    "不会重开游戏或重复使用旧画面。"
                ),
                attempt=recovery + 1,
                total=_MAX_READ_SESSION_NAVIGATION_RECOVERIES,
                elapsed_seconds=0,
                next_action="重新读取并确认当前详情身份后，再执行一次受验证的横向翻页。",
                user_action="无需操作；这是自动的只读 MCP 恢复。",
            )
            reset_session = getattr(getattr(proxy, "client", None), "reset_read_session", None)
            if callable(reset_session):
                reset_session()
            current_detail = base._next_snapshot(proxy, 0.8)
            current_detail, current_fingerprint = wait_for_stable_detail_fingerprint(
                proxy,
                current_detail,
                verified_navigation_fallback=current_fingerprint,
            )
            emit("status", message="当前详情已重新确认；正在再次验证横向翻页。")
    assert last_error is not None
    raise NoNextPokemon(
        f"{last_error}；已完成 {_MAX_READ_SESSION_NAVIGATION_RECOVERIES} 次"
        "只读截图会话恢复，仍不能安全证明下一只"
    ) from last_error


def _detail_name_key(result: NameRegionResult) -> tuple[str, str] | None:
    if result.is_default and result.species:
        return "default", result.species
    # A non-default decision is safe only when the title row still contains a
    # complete, known species name.  Stage Manager can otherwise feed move
    # labels (for example ``道館對戰&團體戰``) into the crop; three equal labels
    # are not proof of a custom nickname and must never authorize a skip.
    # Returning ``None`` keeps those frames in the read-only/unreadable path.
    if result.species and result.evidence:
        return "custom", result.species
    # The app's own IV nickname uses a deliberately shortened species stem,
    # so a narrow title crop can read ``種子鐵1415 / 96 / 14`` rather than the
    # complete database name ``種子鐵球``.  That is nevertheless unambiguous
    # evidence of an existing generated nickname: two standalone annotation
    # values plus a title token that contains both Han text and an annotation
    # digit cannot be a default Pokémon GO title, a status badge, or HP.  It
    # is safe to preserve this card, but it is still never enough to authorize
    # a rename.
    title_tokens = tuple(
        token
        for token in result.evidence
        if not HP_LINE.fullmatch(token)
        and not is_detail_status_label(token)
    )
    annotation_values = sum(
        1 for token in title_tokens if NUMBER_TOKEN.fullmatch(token)
    )
    has_annotated_han_title = any(
        any("\u3400" <= character <= "\u9fff" for character in token)
        and any(character.isdigit() or character in ANNOTATION_CHARS for character in token)
        for token in title_tokens
    )
    if annotation_values >= 2 and has_annotated_han_title:
        return "custom", "|".join(title_tokens)
    # OCR may split *all* circled values away from a shortened title, e.g.
    # 泥偶小 / 56 / 9 / 11.  Requiring a digit inside the Han token then
    # retries forever even though the annotations prove a non-default name.
    # Accept only a known proper three-character species prefix, with HP
    # context and three separate numeric annotations. This is preservation
    # evidence only: never expand the prefix or reconstruct the nickname.
    han_tokens = [token for token in title_tokens if re.fullmatch(r"[\u3400-\u9fff]{3}", token)]
    numeric_tokens = [token for token in title_tokens if re.fullmatch(r"[0-9]{1,3}", token)]
    if (
        len(han_tokens) == 1
        and len(numeric_tokens) >= 3
        and len(han_tokens) + len(numeric_tokens) == len(title_tokens)
        and any(HP_LINE.fullmatch(token) for token in result.evidence)
        and all(0 <= int(token) <= 100 for token in numeric_tokens)
        and sum(int(token) <= 15 for token in numeric_tokens) >= 2
        and han_tokens[0] not in traditional_chinese_species()
        and any(name.startswith(han_tokens[0]) for name in traditional_chinese_species())
    ):
        return "custom", "|".join(title_tokens)
    return None


def _confirm_fresh_detail_identity(
    proxy: SafeProxy,
    snapshot: Snapshot,
    *,
    seed_samples: tuple[Snapshot, ...] = (),
) -> tuple[Snapshot, NameRegionResult] | None:
    """Require three independent, never-before-used detail reads.

    ios-mcp can replay an old but visually valid screenshot after the real
    device has already navigated.  Pixel hashes make prior verified frames
    ineligible.  A stable iPadOS detail can nevertheless produce identical
    pixels on several newly requested screenshots, so the capture ids retain
    those independent reads while the local name reader still ties later
    appraisal to the same exact default species.
    """

    blocked = set(_frame_history(proxy))

    def confirm_candidates(
        candidates: tuple[Snapshot, ...],
    ) -> tuple[Snapshot, NameRegionResult, list[str]] | None:
        samples: dict[
            tuple[str, str], list[tuple[Snapshot, NameRegionResult, str, str]]
        ] = {}
        for candidate in candidates:
            try:
                base._validate_expected("DETAIL", candidate)
                digest = _snapshot_digest(candidate)
            except PolicyViolation:
                continue
            if digest in blocked:
                continue
            try:
                result = analyze_name_region(candidate.image, base.ORIENTATION)
            except PolicyViolation:
                continue
            if not result.is_default and not result.evidence:
                continue
            key = _detail_name_key(result)
            if key is None:
                return None
            if not result.is_default:
                title_tokens = tuple(token for token in result.evidence
                                     if not HP_LINE.fullmatch(token) and not is_detail_status_label(token))
                explicit_custom = any(
                    any(char.isdigit() or char in ANNOTATION_CHARS for char in token)
                    or sum(char.isascii() and char.isalpha() for char in token) >= 3
                    for token in title_tokens
                )
                if not explicit_custom:
                    return None
                # A species-only key is too broad for a custom-name fast
                # path. All three source frames must agree on the actual
                # observed nickname evidence, including every numeric token.
                key = ("custom", repr((key, title_tokens)))
            bucket = samples.setdefault(key, [])
            read_key = _independent_read_key(candidate, digest)
            if read_key in {item[3] for item in bucket}:
                continue
            bucket.append((candidate, result, digest, read_key))
            if len(bucket) >= 3:
                result_snapshot, result, _digest, _read_key = bucket[-1]
                return result_snapshot, result, [item[2] for item in bucket]
        return None

    # A successful swipe already required three independent frames for
    # the changed detail. Re-read their name OCR before using them;
    # this preserves the existing three-frame and exact-name gate while
    # avoiding two immediately redundant capture round trips.  Tie the seed
    # to the actual next snapshot so stale evidence cannot leak across cards.
    if (
        len(seed_samples) == 3
        and seed_samples[-1].image == snapshot.image
    ):
        seeded = confirm_candidates(seed_samples)
        if seeded is not None:
            result_snapshot, result, digests = seeded
            _remember_fresh_frames(proxy, digests)
            emit(
                "status",
                message=(
                    "翻页后的三张新鲜身份帧已确认同一名称（合并读取）："
                    f"{_display_name(result)}。"
                ),
            )
            return result_snapshot, result

    samples: dict[
        tuple[str, str], list[tuple[Snapshot, NameRegionResult, str, str]]
    ] = {}
    wait_started = time.monotonic()
    candidates = [snapshot]
    for attempt in range(_DETAIL_IDENTITY_READ_ONLY_RETRIES):
        emit(
            "waiting",
            stage="详情身份核验",
            reason="需要三张独立截图确认同一完整名称；当前只读，不会点击或改名。",
            attempt=attempt + 1,
            total=_DETAIL_IDENTITY_READ_ONLY_RETRIES,
            elapsed_seconds=max(0, int(time.monotonic() - wait_started)),
            next_action="读取下一张详情截图并比对名称",
            user_action="无需操作；若超过约 2 分钟仍无变化，界面会明确提示安全保留。",
        )
        if attempt:
            candidates.append(
                base._next_snapshot(
                    proxy,
                    (
                        _DETAIL_IDENTITY_FAST_READ_DELAY_SECONDS
                        if attempt <= 2
                        else 1.0
                    ),
                )
            )
        candidate = candidates[-1]
        try:
            base._validate_expected("DETAIL", candidate)
            digest = _snapshot_digest(candidate)
        except PolicyViolation:
            continue
        if digest in blocked:
            continue
        try:
            result = analyze_name_region(candidate.image, base.ORIENTATION)
        except PolicyViolation:
            continue
        key = _detail_name_key(result)
        if key is None:
            continue
        bucket = samples.setdefault(key, [])
        read_key = _independent_read_key(candidate, digest)
        if read_key in {item[3] for item in bucket}:
            continue
        bucket.append((candidate, result, digest, read_key))
        if len(bucket) >= 3:
            digests = [item[2] for item in bucket]
            _remember_fresh_frames(proxy, digests)
            emit(
                "status",
                message=(
                    "详情身份已由三次独立读取确认："
                    f"{_display_name(result)}。"
                ),
            )
            return candidate, result
    return None


def _probe_unreadable_name_via_untouched_dialog(
    proxy: SafeProxy, detail: Snapshot
) -> tuple[Snapshot, NameRegionResult] | None:
    """Read an obscured title from its untouched edit field, then cancel.

    Large models and nearby status ribbons can make the rendered title row
    unreadable even though the editable field still exposes the complete
    current name through accessibility. This recovery never clears, types,
    or submits anything. Three identical complete field reads are required,
    and the verified Cancel transition must return to DETAIL before the value
    may be used. An official species name resumes the normal appraisal path;
    every other value is preservation-only custom-name evidence.
    """

    try:
        base._validate_expected("DETAIL", detail)
        detail_state = base.local_page_state(detail)
    except (PolicyViolation, ValueError):
        return None
    if detail_state != "DETAIL" or getattr(proxy, "observation", None) is None:
        return None

    dialog: Snapshot | None = None
    current = detail
    for attempt in range(1, 4):
        x, y = _static_pencil_coordinates(
            proxy, current, detail_already_verified=True
        )
        emit(
            "waiting",
            screen="DETAIL",
            stage="名称框只读兜底",
            reason="详情标题连续无法辨认；将打开未修改的名称框读取完整原名。",
            attempt=attempt,
            total=3,
            elapsed_seconds=0,
            next_action="读取原名三次后立即取消，不会清除、输入或点击 OK。",
            user_action="无需操作。",
        )
        _tap_dynamic_pencil_at(proxy, x, y)
        resolved = _wait_for_dialog_or_detail_after_pencil(
            proxy,
            "",
            base._next_snapshot(proxy, 0.5),
            detail_stability_rechecks=3,
        )
        if resolved is None:
            raise PolicyViolation("名称框只读兜底后页面未稳定；未输入文字")
        state = base.local_page_state(resolved)
        if state == "RENAME_DIALOG":
            dialog = resolved
            break
        if state != "DETAIL":
            raise PolicyViolation("名称框只读兜底进入未知页面；未输入文字")
        current = resolved

    if dialog is None:
        return None

    reads: list[str] = []
    candidate = dialog
    for attempt in range(1, 4):
        if base.local_page_state(candidate) != "RENAME_DIALOG":
            raise PolicyViolation("名称框三次读取期间弹窗消失；未输入文字")
        try:
            value = exact_name_field(candidate).strip()
        except PolicyViolation:
            value = ""
        if not value:
            raise PolicyViolation("名称框未返回完整原名；未输入文字，也未点击 OK")
        reads.append(value)
        if attempt < 3:
            candidate = base._next_snapshot(proxy, 0.7)

    if len(set(reads)) != 1:
        raise PolicyViolation("名称框三次原名读取不一致；未输入文字，也未点击 OK")
    current_name = reads[0]

    try:
        _cancel_unverified_input(proxy, current_name)
    except RenameFieldVerificationUnavailable as cancelled:
        restored = cancelled.snapshot
    else:  # pragma: no cover - cancellation always returns by exception
        raise PolicyViolation("名称框只读取消未返回已验证详情页")
    if base.local_page_state(restored) != "DETAIL":
        raise PolicyViolation("名称框只读取消后未验证到详情页")

    if current_name in traditional_chinese_species():
        result = NameRegionResult(
            current_name, True, 1.0, (current_name, "名称框三次一致")
        )
        emit(
            "status",
            message=f"页面标题被遮挡；名称框三次一致确认默认名：{current_name}。",
        )
    else:
        result = NameRegionResult(
            None, False, 1.0, (current_name, "名称框三次一致")
        )
        emit(
            "status",
            message=f"名称框三次一致确认已有昵称，已取消并保留：{current_name}。",
        )
    return restored, result


def _ensure_game_foreground(proxy: SafeProxy, snapshot: Snapshot) -> Snapshot:
    """Launch only the configured game when unlock returned to SpringBoard."""

    frontmost = proxy.call_tool("get_frontmost_app", {})
    if proxy.settings.pokemon_go_bundle_id.casefold() in text_from_content(
        frontmost
    ).casefold():
        return snapshot
    observation = proxy.observation
    if observation is None:
        raise PolicyViolation("解锁后启动 Pokémon GO 前缺少安全观察")
    emit("status", message="解锁后当前不在 Pokémon GO；正在安全启动游戏并重新识别页面。")
    proxy.call_tool(
        "launch_app",
        {
            "bundle_id": proxy.settings.pokemon_go_bundle_id,
            "_observation_token": observation.token,
            "_intent": "launch configured Pokemon GO after manual unlock",
            "_expected_after": "configured Pokemon GO is foreground",
        },
    )
    return wait_for_capture_channel(proxy, base._next_snapshot(proxy, 3.0))


def _current_detail_only(snapshot: Snapshot) -> bool:
    """Choose the strict route only when requested or inferred in legacy auto mode.

    The headless and redesigned GUI launchers explicitly select ``false`` for
    ordinary automation.  That prevents an already-open detail from silently
    becoming direct-detail-only and preserves automatic entry recovery.
    """

    value = os.getenv("POGO_START_FROM_CURRENT_DETAIL", "auto").strip().casefold()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    try:
        _require_current_detail(snapshot)
    except PolicyViolation:
        # A visible dock is also normal in a live Stage Manager split layout.
        # Only call it an overview after the detail proof itself has failed.
        overview_markers = ("程序坞", "dock", "Shijima", "设置")
        if any(
            marker.casefold() in snapshot.text.casefold()
            for marker in overview_markers
        ):
            raise PolicyViolation(
                "当前是 Stage Manager 多窗口总览；请先回到 Pokémon GO 并打开目标详情页"
            )
        return False
    return True


def _wait_for_direct_stage_geometry(
    proxy: SafeProxy, snapshot: Snapshot, expected_state: str
) -> Snapshot:
    """Read only until the current direct-detail surface is touch-calibrated.

    A reconnect can capture the same appraisal overlay while Stage Manager is
    still redrawing the surrounding card edges.  That is not a reason to leave
    the user's Pokémon detail page.  Keep observing the exact same state and
    permit a close/tap only after the calibrated geometry is available again.
    """

    waiting_reported = False
    while True:
        try:
            return base._ensure_stage_geometry_for_state(
                proxy, snapshot, expected_state
            )
        except PolicyViolation as exc:
            if "当前截图未能安全定位 Stage Manager" not in str(exc):
                raise
            if not waiting_reported:
                emit(
                    "status",
                    message=(
                        "Stage Manager 正在重绘 Pokémon GO 窗口边界；"
                        "后台只读等待重新标定，不会点击、结束任务或重开游戏。"
                    ),
                )
                waiting_reported = True
            candidate = base._next_snapshot(proxy, 3.0)
            if base.local_page_state(candidate) != expected_state:
                # The caller will re-evaluate the newly observed page before
                # it considers any write.  Returning it is safer than closing
                # an overlay whose visual state has changed during redraw.
                return candidate
            snapshot = candidate


def _restore_direct_detail_after_interrupted_appraisal(
    proxy: SafeProxy, snapshot: Snapshot
) -> Snapshot:
    """Close only a proven appraisal overlay before a direct-detail resume."""

    state = base.local_page_state(snapshot)
    if state not in {"APPRAISAL_DIALOG", "APPRAISAL_BARS"}:
        return snapshot
    snapshot = _wait_for_direct_stage_geometry(proxy, snapshot, state)
    state = base.local_page_state(snapshot)
    if state not in {"APPRAISAL_DIALOG", "APPRAISAL_BARS"}:
        return snapshot
    if state == "APPRAISAL_DIALOG":
        emit(
            "status",
            message=(
                "检测到上次中断时遗留的鉴定对白；只推进该对白一次，"
                "再关闭鉴定层回到同一只详情页。"
            ),
        )
        appraisal, _measurement = _navigate_with_read_only_measurement_retry(
            proxy, snapshot
        )
        return _close_appraisal(proxy)
    emit(
        "status",
        message=(
            "检测到上次截图中断时遗留的鉴定层；只关闭鉴定层并回到同一只详情页，"
            "不会进入精灵球、宝可梦盒或重启游戏。"
        ),
    )
    return _close_appraisal(proxy)


def _lock_recovery_default_species(proxy: SafeProxy) -> str | None:
    """Return this worker's proven pre-input dialog species, if any.

    This marker exists only during the small interval after this worker opened
    a verified rename dialog and before SafeProxy accepted an input request.
    It permits cancelling that known unsubmitted edit after a device lock;
    it must never be inferred from arbitrary OCR on a recovered dialog.
    """

    value = getattr(proxy, "_pogo_lock_recovery_default_species", None)
    return value if isinstance(value, str) and value else None


def _set_lock_recovery_default_species(proxy: SafeProxy, species: str) -> None:
    """Record the in-process pre-input proof when the proxy supports it."""

    try:
        setattr(proxy, "_pogo_lock_recovery_default_species", species)
    except AttributeError:
        # Lightweight read-only test doubles need not carry process state.
        # The production SafeProxy has a normal instance dictionary.
        pass
    journal = getattr(proxy, "journal", None)
    append = getattr(journal, "append", None)
    if callable(append):
        append(
            "rename_pre_input_dialog_verified",
            {
                "species": species,
                "evidence": "verified default detail + verified rename dialog; no input accepted",
            },
        )


def _clear_lock_recovery_default_species(proxy: SafeProxy) -> None:
    try:
        delattr(proxy, "_pogo_lock_recovery_default_species")
    except AttributeError:
        pass


_VISIBLE_GAME_SURFACES = frozenset(
    {
        "DETAIL",
        "DETAIL_MENU",
        "APPRAISAL_DIALOG",
        "APPRAISAL_BARS",
        "RENAME_DIALOG",
    }
)


def _wait_for_current_game_surface_after_unlock(
    proxy: SafeProxy, snapshot: Snapshot
) -> Snapshot:
    """Keep a direct-detail worker alive through post-unlock redraw frames.

    iPad can return a black or transitional screenshot for several seconds
    after unlock even though Pokémon GO remains frontmost and its interrupted
    appraisal/dialog is still underneath.  That frame cannot justify a map or
    box navigation, nor should it discard the in-progress Pokémon.  With the
    persistent direct worker enabled, observe only until a known live game
    surface reappears; the caller then performs the already-scoped recovery.
    """

    if base.local_page_state(snapshot) in _VISIBLE_GAME_SURFACES:
        return snapshot
    if not _persistent_capture_wait_enabled():
        return snapshot
    started = time.monotonic()
    last_reported = started
    emit(
        "status",
        message=(
            "解锁后 Pokémon GO 正在恢复当前页面；后台只读等待详情、鉴定或改名层重新可见，"
            "不会进入盒子、翻页或重开游戏。"
        ),
    )
    while True:
        snapshot = base._next_snapshot(proxy, 3.0)
        if base.local_page_state(snapshot) in _VISIBLE_GAME_SURFACES:
            return snapshot
        now = time.monotonic()
        if now - last_reported >= 15.0:
            elapsed = max(0, int(now - started))
            emit(
                "status",
                message=(
                    f"解锁后仍在只读等待当前游戏页面恢复（已 {elapsed} 秒）；"
                    "不会点击精灵球、盒子卡片、OK 或重开游戏。"
                ),
            )
            last_reported = now


def _recover_after_device_unlock(
    proxy: SafeProxy,
    snapshot: Snapshot,
    settings: Settings,
) -> tuple[Snapshot, str]:
    """Return to a proven detail after a lock invalidated an active step.

    No stale coordinate or input operation is resumed.  A durable text input
    is recovered only through the existing journal-plus-live-field proof.  If
    this worker had merely cleared the known default name before the lock, it
    cancels that unsubmitted dialog and retries the *same* Pokémon.
    """

    if _has_ipad_task_switcher_overlay(snapshot):
        emit(
            "status",
            message=(
                "解锁后 iPad 多任务切换层仍覆盖 Pokémon GO；后台继续只读等待"
                "原详情页恢复，不会选择卡片、进入盒子或重开游戏。"
            ),
        )
        snapshot = _wait_for_visible_game_surface_after_task_switcher(proxy, snapshot)

    snapshot = _wait_for_current_game_surface_after_unlock(proxy, snapshot)
    state = base.local_page_state(snapshot)
    if state in {"APPRAISAL_DIALOG", "APPRAISAL_BARS"}:
        emit(
            "status",
            message=(
                "解锁后检测到锁屏前的鉴定层；只关闭该层回到同一只详情页，"
                "然后重新读取，不会翻页或重开游戏。"
            ),
        )
        return _restore_direct_detail_after_interrupted_appraisal(proxy, snapshot), "retry"

    if state == "RENAME_DIALOG":
        before = proxy.verified_renames
        if proxy.pending_name or _last_unsubmitted_journal_nickname(settings):
            emit(
                "status",
                message=(
                    "解锁后检测到已留档的改名字段；仅当留档目标与当前字段逐字一致时"
                    "才会提交，否则取消未提交编辑并重试同一只。"
                ),
            )
            detail = _resume_verified_unsubmitted_rename(proxy, snapshot, settings)
            _clear_lock_recovery_default_species(proxy)
            base._validate_expected("DETAIL", detail)
            return detail, "renamed" if proxy.verified_renames == before + 1 else "retry"

        default_species = _lock_recovery_default_species(proxy)
        if default_species is None:
            raise PolicyViolation(
                "解锁后出现未留档的改名弹窗；无法证明字段归属，"
                "不会点击 OK 或取消"
            )
        emit(
            "status",
            message=(
                f"锁屏发生在 {default_species} 发送新昵称之前；"
                "现在仅取消本工作器刚打开的未提交编辑，并重新处理同一只。"
            ),
        )
        try:
            _cancel_unverified_input(proxy, default_species)
        except RenameFieldVerificationUnavailable as cancelled:
            _clear_lock_recovery_default_species(proxy)
            base._validate_expected("DETAIL", cancelled.snapshot)
            return cancelled.snapshot, "retry"
        raise PolicyViolation("取消锁屏前未提交编辑后未返回已验证详情页")

    _clear_lock_recovery_default_species(proxy)
    return _require_current_detail(snapshot), "retry"


def _wait_for_visible_game_surface_after_task_switcher(
    proxy: SafeProxy, snapshot: Snapshot
) -> Snapshot:
    """Read until a known Pokémon GO surface reappears after iPad overview.

    Unlike startup recovery, an overview can interrupt an appraisal or rename
    dialog rather than a plain detail page.  This helper performs reads only
    and therefore returns the first *known* game surface; its caller chooses
    the matching safe recovery action afterwards.
    """

    if not _persistent_capture_wait_enabled():
        raise PolicyViolation("多任务切换层出现时未启用持续只读等待")
    wait_started = time.monotonic()
    last_reported = wait_started
    emit(
        "status",
        message=(
            "iPad 多任务切换层中断当前步骤；后台只读等待同一 Pokémon GO 页面恢复，"
            "不会点击其他 App、OK、取消或重开游戏。"
        ),
    )
    while True:
        # AX can retain Stage Manager's Dock/card labels even while the fresh
        # screenshot already proves a live Pokémon GO detail, appraisal, or
        # rename dialog.  Pixel-local state is stronger evidence than those
        # stale surrounding accessibility nodes, so always test it first.
        state = base.local_page_state(snapshot)
        if state in _VISIBLE_GAME_SURFACES:
            return snapshot
        snapshot = base._next_snapshot(proxy, 3.0)
        now = time.monotonic()
        if now - last_reported >= 15.0:
            elapsed = max(0, int(now - wait_started))
            emit(
                "status",
                message=(
                    f"iPad 系统层仍未恢复可验证游戏页面（已安全等待 {elapsed} 秒）；"
                    "后台继续只读检查，不会执行触控。"
                ),
            )
            last_reported = now


def _recover_after_task_switcher_interrupt(
    proxy: SafeProxy, snapshot: Snapshot, settings: Settings
) -> tuple[Snapshot, str]:
    """Recover the same Pokémon after a system overview blocked a write.

    A fresh known game surface is required before any recovery touch.  The
    appraisal and rename paths reuse the existing bounded verification flows;
    unknown pages remain read-only waits.
    """

    snapshot = _wait_for_visible_game_surface_after_task_switcher(proxy, snapshot)
    state = base.local_page_state(snapshot)
    if state in {"APPRAISAL_DIALOG", "APPRAISAL_BARS"}:
        emit(
            "status",
            message=(
                "多任务切换层消失后仍是同一只的鉴定层；只关闭鉴定层回到详情页，"
                "随后重试本只，不会翻页或重开游戏。"
            ),
        )
        return _restore_direct_detail_after_interrupted_appraisal(proxy, snapshot), "retry"
    if state == "RENAME_DIALOG":
        before = proxy.verified_renames
        emit(
            "status",
            message=(
                "多任务切换层消失后发现改名弹窗；仅按留档与实时字段逐字一致性恢复，"
                "否则保留原名并重试同一只。"
            ),
        )
        detail = _resume_verified_unsubmitted_rename(proxy, snapshot, settings)
        _clear_lock_recovery_default_species(proxy)
        base._validate_expected("DETAIL", detail)
        return detail, "renamed" if proxy.verified_renames == before + 1 else "retry"
    _clear_lock_recovery_default_species(proxy)
    return _require_current_detail(snapshot), "retry"


def _foreground_proof_was_interrupted(exc: BaseException) -> bool:
    """Recognize the safe-proxy refusal caused by a transient system layer."""

    return "pokemon go is not proven to be the foreground app" in str(exc).casefold()


def _game_restart_allowed() -> bool:
    """Keep historical recovery opt-in; a normal navigation miss never relaunches."""

    value = os.getenv("POGO_ALLOW_GAME_RESTART", "false").strip().casefold()
    return value in {"1", "true", "yes", "on"}


def _wait_without_game_restart(proxy: SafeProxy, snapshot: Snapshot) -> Snapshot:
    """Wait for a capture frame without moving away from the current detail."""

    return wait_for_capture_channel(proxy, snapshot, allow_game_restart=False)


def _navigate_from_current_detail_only(
    proxy: SafeProxy, snapshot: Snapshot
):
    """Open appraisal from a proven detail without permitting entry navigation.

    The historical v14 appraise adapter can start from MAP, MAIN_MENU, or
    INVENTORY and deliberately walks forward to a detail page.  That is useful
    for the legacy batch entry, but it is never allowed once this batch has
    committed to the user's manually opened detail page.  In particular, an
    unexpected stale/changed frame must stop before the legacy adapter can tap
    the first visible storage card.

    The adapter normally needs no v14 transition when it begins at DETAIL: it
    only opens the detail menu, chooses Appraise, and (where required) advances
    the appraisal dialogue once.  Do not call the v25/v14 wrapper here: that
    wrapper re-classifies a fresh detail using broad accessibility text, and a
    Stage Manager frame can then be mistaken for INVENTORY.  Its historical
    recovery would consequently tap a storage card despite a direct-detail
    batch already having established the next detail by pixel evidence.

    The v24 reader wraps its import-time reference to the base navigator.  It
    provides the required bounded, read-only appraisal measurement retries
    while retaining the direct DETAIL -> DETAIL_MENU -> APPRAISAL route.  Keep
    the tap allowlist as a final guard: even a future navigator regression
    must not be able to enter the map or Pokémon box during a direct batch.
    """

    snapshot = _require_current_detail(snapshot)
    original_tap = base._tap
    allowed_taps = {
        "DETAIL",
        "DETAIL_MENU",
        "APPRAISAL_DIALOG",
        "APPRAISAL_CLOSE",
    }

    def direct_detail_tap(active_proxy: SafeProxy, key: str) -> None:
        if key not in allowed_taps:
            raise PolicyViolation(
                "从当前详情页连续模式拒绝非详情鉴定操作 "
                f"({key})；已安全停止，不会点击精灵球、宝可梦盒、"
                "第一只可见宝可梦、图鉴，也不会重启游戏"
            )
        original_tap(active_proxy, key)

    base._tap = direct_detail_tap
    try:
        return _navigate_with_read_only_measurement_retry(proxy, snapshot)
    finally:
        base._tap = original_tap


def _require_current_detail(snapshot: Snapshot) -> Snapshot:
    """Prove the first detail page without tapping anywhere else."""

    # A pixel-proven expanded detail menu is still the current Pokémon detail:
    # its only allowed continuation is the exact Appraise control.  Accepting
    # this descendant avoids reopening/toggling the three-dot menu after a
    # transient OCR miss while preserving the direct-route tap allowlist.
    try:
        if base.local_page_state(snapshot) == "DETAIL_MENU":
            return snapshot
    except Exception:
        # DETAIL_MENU is an optional pixel-local fast path.  Preserve the
        # historical validator below when a malformed/transitional capture
        # cannot be classified; it remains the authoritative safety gate.
        pass
    if _is_scrolled_pokemon_detail(snapshot):
        return snapshot
    try:
        base._validate_expected("DETAIL", snapshot)
    except (PolicyViolation, ValueError) as exc:
        raise PolicyViolation(
            "请先手动打开要处理的第一只宝可梦详情页；当前页面未执行精灵球、"
            "宝可梦盒或重启游戏操作"
        ) from exc
    return snapshot


def _is_scrolled_pokemon_detail(snapshot: Snapshot) -> bool:
    """Recognize the lower portion of a Pokémon detail without guessing pages."""

    if not snapshot.image:
        return False
    try:
        from .local_ocr import ocr_mcp_screenshot

        values = {
            line.text.strip().casefold()
            for line in ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
            if line.confidence >= 0.80
        }
    except Exception:
        return False
    # A main menu, map, or box cannot expose this combination.  Require the
    # normal detail controls plus either its move area or its buddy/storage
    # labels; a lone "進化" button is deliberately insufficient.
    core = {"強化", "强化", "進化", "进化"}
    corroborating = {
        "新攻擊招式",
        "新攻击招式",
        "替換夥伴",
        "替换伙伴",
        "道館對戰&團體戰",
        "道馆对战&团体战",
        "訓練家對戰",
        "训练家对战",
    }
    return bool(values.intersection({"強化", "强化"})) and bool(
        values.intersection({"進化", "进化"})
    ) and bool(values.intersection(corroborating))


def _scroll_verified_detail_to_title(proxy: SafeProxy, snapshot: Snapshot) -> Snapshot:
    """Perform one safe, in-card scroll only for a proven lower detail page."""

    if not _is_scrolled_pokemon_detail(snapshot):
        return snapshot
    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 未返回触控空间")
    from_x, from_y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        0.50,
        0.36,
        geometry=base.current_stage_geometry(proxy),
    )
    to_x, to_y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        0.50,
        0.74,
        geometry=base.current_stage_geometry(proxy),
    )
    proxy.call_tool(
        "swipe_screen",
        {
            "fromX": from_x,
            "fromY": from_y,
            "toX": to_x,
            "toY": to_y,
            "_observation_token": observation.token,
            "_intent": "navigate proven Pokemon detail to title row",
            "_expected_after": "same Pokemon detail with title row visible",
        },
    )
    return base._next_snapshot(proxy, 1.2)


def _persistent_capture_wait_enabled() -> bool:
    return os.getenv("POGO_PERSIST_CAPTURE_WAIT", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _has_ipad_task_switcher_overlay(snapshot: Snapshot) -> bool:
    """Recognize the iPad multiwindow layer without treating it as a game page."""

    text = snapshot.text.casefold()
    return "程序坞" in text or "dock" in text


def _last_unsubmitted_journal_nickname(settings: Settings) -> str | None:
    """Return only the most recent input that lacks a later verified commit."""

    candidate: str | None = None
    try:
        lines = settings.journal_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if (
            record.get("event") == "write_attempt"
            and record.get("tool") == "input_text"
            and record.get("success") is True
        ):
            arguments = record.get("arguments")
            value = arguments.get("text") if isinstance(arguments, dict) else None
            if isinstance(value, str) and value.strip():
                candidate = value.strip()
            continue
        if (
            candidate
            and str(record.get("event", "")).startswith("verified_rename")
            and str(record.get("new_name", "")).strip() == candidate
        ):
            candidate = None
    return candidate


def _species_from_deterministic_nickname(value: object) -> str | None:
    """Recover a default species from this worker's deterministic nickname."""

    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    matches = [species for species in traditional_chinese_species() if value.startswith(species)]
    if not matches:
        return None
    species = max(matches, key=len)
    # A bare species does not prove that this was an attempted generated
    # nickname.  The generated form always includes at least one IV glyph.
    return species if len(value) > len(species) else None


def _last_interrupted_preinput_journal_species(settings: Settings) -> str | None:
    """Find a durable, uncommitted pre-input dialog proof after a restart.

    The normal marker records a verified default dialog before any text write.
    For older workers, a failed deterministic ``input_text`` attempt is also
    enough: SafeProxy journals it only from this renamer's write path and it
    did not reach a successful input/commit.  This proof authorizes only
    *cancelling* the unsubmitted dialog, never pressing OK.
    """

    try:
        lines = settings.journal_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        event = str(record.get("event", ""))
        if event.startswith("verified_rename"):
            return None
        if event == "rename_pre_input_dialog_verified":
            species = record.get("species")
            return species if isinstance(species, str) and species else None
        if event != "write_attempt" or record.get("tool") not in {
            "input_text",
            "type_text",
        }:
            continue
        if record.get("success") is True:
            return None
        arguments = record.get("arguments")
        value = arguments.get("text") if isinstance(arguments, dict) else None
        return _species_from_deterministic_nickname(value)
    return None


def _proven_default_name_in_rename_dialog(snapshot: Snapshot) -> str | None:
    """Return a default species only when it is visibly in the dialog field.

    A worker can be interrupted after it has opened the iOS dialog but before
    it has typed anything.  That blank/default dialog is safe to cancel and
    resume from; a custom or partially edited field is deliberately left
    untouched.  The field-region test prevents a Pokémon name elsewhere in a
    Stage Manager screenshot from authorizing a cancel.
    """

    if not snapshot.image:
        return None
    lines = ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
    if not rename_dialog_visible(lines):
        return None
    try:
        species, _confidence = exact_species_from_lines(lines)
        located = locate_exact_text_from_mcp(
            snapshot.image,
            base.ORIENTATION,
            species,
            minimum_confidence=0.70,
        )
    except PolicyViolation:
        return None
    x_ratio = (located.box.left + located.box.right) / (2.0 * located.image_width)
    y_ratio = located.box.center_y / located.image_height
    if not (0.10 <= x_ratio <= 0.70 and 0.30 <= y_ratio <= 0.55):
        return None
    return species


def _journalled_nondefault_rename_can_resume_without_ax(
    snapshot: Snapshot, candidate: str
) -> bool:
    """Recognize the narrow iPad keyboard case where AX drops the text field.

    ``input_text`` is journalled only after the local worker sent one complete,
    deterministic nickname.  On this iPad, opening the Chinese keyboard can
    subsequently remove the field from *all* accessibility responses even
    though the same populated rename dialog is still plainly visible.  The
    recovery is deliberately narrower than normal submission: it requires a
    fresh pixel-proven dialog, a syntactically valid deterministic nickname,
    and proof that the field is no longer the untouched default species.
    Anything default, custom, partially proven, or visually unreadable stays
    unsubmitted.
    """

    # Long CJK names may be deliberately truncated by ``generate_iv_nickname``
    # to Pokémon GO's UTF-8 byte limit (for example 呱呱泡蛙 → 呱呱泡…).
    # The durable journal already limits this helper to our own successful
    # ``input_text`` operation, so require a Poke Genie annotation character
    # instead of incorrectly requiring the *full* species prefix.
    if not snapshot.image or not any(char in ANNOTATION_CHARS for char in candidate):
        return False
    try:
        if not rename_dialog_visible(
            ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
        ):
            return False
        return _proven_default_name_in_rename_dialog(snapshot) is None
    except (PolicyViolation, ValueError, OSError):
        return False


def _resume_verified_unsubmitted_rename(
    proxy: SafeProxy, snapshot: Snapshot, settings: Settings
) -> Snapshot:
    """Commit a restart-interrupted edit only when both durable proofs agree.

    A newly spawned worker has no in-memory pending-name state.  It may recover
    exactly one edit only if the journal's last uncommitted input and the live
    accessibility text field agree character-for-character.  Otherwise it
    leaves the dialog untouched: unknown manual text is never submitted.
    """

    # The overview can retain stale rename accessibility beneath its cards.
    # It is not evidence about the live field, so never parse, cancel, or
    # submit it.  First wait for the existing Pokémon GO detail to be visible.
    if _has_ipad_task_switcher_overlay(snapshot):
        emit(
            "status",
            message=(
                "多任务切换层覆盖遗留改名界面；先只读等待原详情页恢复，"
                "不会解析字段、点击 OK 或取消。"
            ),
        )
        snapshot = _wait_for_visible_game_surface_after_task_switcher(proxy, snapshot)

    if base.local_page_state(snapshot) != "RENAME_DIALOG":
        return snapshot
    candidate = _last_unsubmitted_journal_nickname(settings)
    if not candidate:
        default_name = _proven_default_name_in_rename_dialog(snapshot)
        if default_name is None:
            default_name = _last_interrupted_preinput_journal_species(settings)
        if default_name is None:
            raise PolicyViolation("检测到未留档且无法证明默认名称的改名弹窗；不会猜测提交或取消")
        emit(
            "status",
            message=(
                f"检测到先前仅打开、尚未提交的默认名称弹窗：{default_name}；"
                "自动取消后从同一详情页重试。"
            ),
        )
        try:
            _cancel_unverified_input(proxy, default_name)
        except RenameFieldVerificationUnavailable as cancelled:
            return cancelled.snapshot
        raise PolicyViolation("默认名称弹窗取消流程未返回已验证详情页")
    try:
        actual = _verified_entered_value(proxy)
    except PolicyViolation:
        # Keyboard-open Stage Manager frames can make every AX field query
        # raise rather than merely return an empty string.  Treat that as the
        # same unavailable-field state so the guarded journal/pixel recovery
        # below can decide; never let it bypass the default-name check.
        actual = ""
    if actual != candidate:
        # A prior MCP input request can be durably journalled even though the
        # Stage Manager text field never received it.  If the live pixels now
        # prove that the field is still the untouched default species, this is
        # not an ambiguous partial edit: cancel it automatically and restart
        # the same detail.  Any other mismatch remains a hard stop.
        default_name = _proven_default_name_in_rename_dialog(snapshot)
        if default_name is not None:
            emit(
                "status",
                message=(
                    f"留档目标 {candidate} 未出现在字段中，当前仍是默认名称"
                    f"{default_name}；自动取消空白弹窗后重试。"
                ),
            )
            try:
                _cancel_unverified_input(proxy, default_name)
            except RenameFieldVerificationUnavailable as cancelled:
                return cancelled.snapshot
            raise PolicyViolation("默认名称弹窗取消流程未返回已验证详情页")
        if _journalled_nondefault_rename_can_resume_without_ax(snapshot, candidate):
            # The exact full value was supplied by our durable successful
            # input request; the live dialog is pixel-proven and is visibly no
            # longer default.  AX is the only unavailable channel here.  This
            # keeps an existing keyboard-open edit from becoming a permanent
            # batch stop, without ever using the fallback for a default or
            # unknown field.
            actual = candidate
            emit(
                "status",
                message=(
                    "当前改名弹窗与最后一次完整输入日志一致，且像素确认字段已非默认名；"
                    "辅助功能字段暂缺，按受限恢复流程继续提交。"
                ),
            )
        else:
            raise PolicyViolation(
                "遗留改名字段与最后一次留档目标不一致；不会点击 OK 或取消"
            )
    if not snapshot.image:
        raise PolicyViolation("遗留改名弹窗缺少截图；不会点击 OK")

    # A fresh dialog proof is required in addition to the exact AX field.
    # The helper performs reads only and provides the same evidence gate used
    # by ordinary per-Pokémon submission.
    dialog, _ = _dialog_evidence_after_keyboard_dismiss(proxy)
    if proxy.observation is None:
        raise PolicyViolation("恢复留档改名前缺少安全观察")
    proxy.observation.text += "\n重新命名（留档目标与当前字段逐字一致；恢复提交）"
    verified_before = proxy.verified_renames
    proxy.pending_name = candidate
    emit(
        "status",
        message=(
            f"已恢复上次中断的改名字段，并与留档目标逐字一致：{candidate}；"
            "现在才会安全提交。"
        ),
    )
    detail = _submit_with_one_verified_retry(
        proxy, nickname=candidate, initial_dialog=dialog
    )
    if proxy.pending_name is not None:
        if proxy.pending_name != candidate or proxy.verified_renames != verified_before:
            raise PolicyViolation("恢复提交后的待确认状态不一致")
        proxy.verified_renames = verified_before + 1
        proxy.pending_name = None
    elif proxy.verified_renames != verified_before + 1:
        raise PolicyViolation("恢复提交后成功计数未验证")
    proxy.journal.append(
        "verified_rename_recovered_after_task_switcher",
        {
            "new_name": candidate,
            "evidence": "journal target + exact live field + dialog proof + DETAIL",
        },
    )
    emit("status", message=f"✓ 已恢复并核验提交：{candidate}")
    return detail


def _wait_for_direct_detail_after_task_switcher(
    proxy: SafeProxy, snapshot: Snapshot
) -> Snapshot:
    """Keep a direct-detail task alive while iPad's task switcher covers it.

    The overview can expose other app cards even though Pokémon GO remains
    running with the target detail underneath.  There is no safe generic card
    coordinate, so this path only reads until the existing Pokémon GO detail
    becomes visible again; it never selects another app or starts the game.
    """

    try:
        return _scroll_verified_detail_to_title(
            proxy, _require_current_detail(snapshot)
        )
    except PolicyViolation:
        # A reconnect can return an unclassifiable first frame without ever
        # showing the task-switcher labels.  In current-detail mode that is
        # not authority to leave the user's page; it is only authority to
        # read again.  The safe proxy independently aborts on a dangerous
        # Transfer UI, and no touch is possible in this loop.
        if not _persistent_capture_wait_enabled() or getattr(proxy, "aborted", False):
            raise
    emit(
        "status",
        message=(
            "检测到 iPad 多任务切换层覆盖 Pokémon GO 详情；后台保持运行并只读等待详情恢复，"
            "不会选择其他 App、进入宝可梦盒或重新打开游戏。"
        ),
    )
    wait_started = time.monotonic()
    last_reported = wait_started
    while True:
        candidate = base._next_snapshot(proxy, 3.0)
        try:
            return _scroll_verified_detail_to_title(
                proxy, _require_current_detail(candidate)
            )
        except PolicyViolation:
            if _has_ipad_task_switcher_overlay(candidate):
                now = time.monotonic()
                if now - last_reported >= 15.0:
                    elapsed = max(0, int(now - wait_started))
                    emit(
                        "status",
                        message=(
                            f"iPad 多任务切换层仍在覆盖详情（已安全等待 {elapsed} 秒）；"
                            "后台仍在只读检查，不会点击其他 App、OK 或重开游戏。"
                        ),
                    )
                    last_reported = now
                continue
            if getattr(proxy, "aborted", False):
                raise
            now = time.monotonic()
            if now - last_reported >= 15.0:
                elapsed = max(0, int(now - wait_started))
                emit(
                    "status",
                    message=(
                        f"当前详情页重连首帧暂时不可分类（已安全等待 {elapsed} 秒）；"
                        "后台仍只读等待同一详情页恢复，不会点击、翻页或重开游戏。"
                    ),
                )
                last_reported = now
            continue


def _wait_for_verified_next_detail(
    proxy: SafeProxy,
    snapshot: Snapshot,
    *,
    seed_samples: tuple[Snapshot, ...],
) -> Snapshot:
    """Reconfirm a post-swipe detail without falling back to entry navigation.

    ``swipe_to_verified_next`` has already established the new card from
    three independent screenshots.  The broader DETAIL classifier can still
    briefly miss that very same page while its labels settle.  In the
    persistent direct-detail worker, keep reading that page until the coarse
    classifier agrees; never treat this as permission to enter the box or to
    restart the game.
    """

    def reject_known_non_detail(candidate: Snapshot) -> None:
        """Never convert a real box/menu transition into an endless read wait."""

        try:
            state = base.local_page_state(candidate)
        except Exception:
            return
        if state in {"MAP", "MAIN_MENU", "INVENTORY"}:
            raise PolicyViolation(
                f"翻页后已明确离开宝可梦详情页（{state}）；"
                "已安全停止，不会在地图或宝可梦盒继续操作"
            )

    try:
        return _require_current_detail(snapshot)
    except PolicyViolation:
        reject_known_non_detail(snapshot)
        if len(seed_samples) != 3 or not _persistent_capture_wait_enabled():
            raise
    emit(
        "status",
        message=(
            "已验证翻页后的下一只详情暂未被页面分类器识别；后台保持运行并只读复核，"
            "不会滑动、结束任务或重新打开游戏。"
        ),
    )
    while True:
        candidate = base._next_snapshot(proxy, 3.0)
        try:
            return _require_current_detail(candidate)
        except PolicyViolation:
            reject_known_non_detail(candidate)
            continue


def _restore_direct_detail_from_existing_menu(
    proxy: SafeProxy, snapshot: Snapshot
) -> Snapshot:
    """Return to the same plain detail when recovery starts on its menu.

    The expanded detail menu hides the title/HP region needed for the
    three-frame identity proof.  Its exact Appraise control is already
    pixel-proven, so we may enter that one allowed game surface and close only
    the resulting appraisal overlay.  This recovery-only round trip never
    opens the box, swipes, renames, or restarts the game.
    """

    emit(
        "status",
        message=(
            "检测到上次中断时遗留的详情菜单；仅选择已验证的“调查宝可梦”并关闭鉴定层，"
            "回到同一只详情页后重新确认身份。"
        ),
    )
    appraisal, _measurement = _navigate_from_current_detail_only(proxy, snapshot)
    return _close_appraisal(proxy)


def _is_recoverable_navigation_failure(exc: Exception) -> bool:
    """Identify read-only/transition misses that are safe to recover from.

    None of these failures has an open rename field or a pending name.  A
    controlled Pokémon GO restart is therefore safer than ending a long UI
    batch simply because Stage Manager supplied a stale or covered frame.
    """

    message = str(exc)
    return any(
        marker in message
        for marker in (
            "详情页稳定身份字段不足",
            "横向翻页后连续只读采样仍无法确认安全详情页",
            "翻页前无法取得两帧一致的详情身份",
            # These are emitted by the calibrated MAP → MAIN_MENU → INVENTORY
            # entry route before a name field can ever be opened.  A delayed
            # Stage Manager/game animation must recover the game rather than
            # end an otherwise unlimited batch.
            "页面在等待 12 秒后仍为",
            "点击精灵球后没有验证到主菜单",
            "点击“寶可夢”后没有验证到宝可梦盒",
            "点击第一张卡片后没有验证到详情页",
        )
    )


def _is_unsafe_stage_manager_geometry(exc: Exception) -> bool:
    """Recognize a transient layout capture before any game touch is allowed."""

    return _UNSAFE_STAGE_MANAGER_GEOMETRY in str(exc)


def _recover_from_transient_navigation_failure(proxy: SafeProxy) -> Snapshot:
    """Restart only the configured game, then return a fresh game frame."""

    _publish_live_snapshot(screen_snapshot(proxy))
    observation = proxy.observation
    if observation is None:
        raise PolicyViolation("恢复导航前缺少安全观察")
    proxy.call_tool(
        "kill_app",
        {
            "bundle_id": proxy.settings.pokemon_go_bundle_id,
            "_observation_token": observation.token,
            "_intent": "恢复导航：关闭已配置的 Pokémon GO 以清除过期详情帧",
            "_expected_after": "configured Pokémon GO is stopped",
        },
    )
    time.sleep(1.5)
    _publish_live_snapshot(screen_snapshot(proxy))
    observation = proxy.observation
    if observation is None:
        raise PolicyViolation("恢复启动前缺少安全观察")
    proxy.call_tool(
        "launch_app",
        {
            "bundle_id": proxy.settings.pokemon_go_bundle_id,
            "_observation_token": observation.token,
            "_intent": "恢复导航：启动已配置的 Pokémon GO 并重建详情入口",
            "_expected_after": "configured Pokémon GO is foreground",
        },
    )
    # App-launch animation and Stage Manager composition are read-only waits.
    return wait_for_capture_channel(
        proxy, base._next_snapshot(proxy, 4.0), allow_game_restart=True
    )


def _pause_file(settings: Settings) -> BatchPauseFile:
    configured = os.getenv("POGO_PAUSE_FILE", "").strip()
    path = Path(configured) if configured else settings.journal_path.parent / "batch.pause"
    return BatchPauseFile(path)


def _save_unreadable_appraisal(snapshot: Snapshot, index: int) -> None:
    """Persist the final read-only frame so a device-specific miss is diagnosable."""

    if not snapshot.image:
        return
    journal = os.getenv("POGO_JOURNAL_PATH", "").strip()
    directory = Path(journal).parent if journal else Path.cwd() / ".pogo-data"
    directory.mkdir(parents=True, exist_ok=True)
    try:
        (directory / f"appraisal-unreadable-{index}-raw.png").write_bytes(
            base64.b64decode(snapshot.image)
        )
        base.rotate_mcp_image_upright(snapshot.image, base.ORIENTATION).save(
            directory / f"appraisal-unreadable-{index}-canonical.png",
            format="PNG",
        )
    except Exception as exc:
        emit("status", message=f"未能保存第 {index} 只诊断帧：{exc}")


def _emit_progress(
    *,
    current: int,
    limit: int,
    phase: str,
    counts: dict[str, int],
) -> None:
    emit(
        "progress",
        current=current,
        limit=None if limit == BATCH_LIMIT_UNLIMITED else limit,
        phase=phase,
        renamed=counts["renamed"],
        skipped=counts["skipped"],
        scanned=counts["scanned"],
        unreadable=counts["unreadable"],
    )


def _wait_at_safe_pause_boundary(
    proxy: SafeProxy,
    detail: Snapshot,
    *,
    fingerprint: DetailFingerprint,
    index: int,
    limit: int,
    counts: dict[str, int],
    pause: BatchPauseFile,
) -> Snapshot:
    if not pause.requested:
        return detail
    _emit_progress(
        current=index,
        limit=limit,
        phase="paused",
        counts=counts,
    )
    emit(
        "status",
        message=f"已在第 {index} 只完成后安全暂停；暂停期间不读取、不触控 iPad。",
    )
    while pause.requested:
        time.sleep(0.25)

    refreshed = _publish_live_snapshot(screen_snapshot(proxy))
    base._validate_expected("DETAIL", refreshed)
    resumed_fingerprint = detail_fingerprint(refreshed)
    if fingerprints_differ(fingerprint, resumed_fingerprint):
        raise PolicyViolation("暂停期间当前宝可梦身份已变化；为避免误操作已停止")
    _emit_progress(
        current=index,
        limit=limit,
        phase="resumed",
        counts=counts,
    )
    emit("status", message=f"已继续；第 {index} 只身份复核通过。")
    return refreshed


def _close_appraisal(proxy: SafeProxy) -> Snapshot:
    for attempt in range(2):
        base._tap(proxy, "APPRAISAL_CLOSE")
        observed_states: list[str] = []
        last_validation_error: PolicyViolation | None = None
        for observation in range(5):
            # Closing the appraisal overlay has a variable animation time on
            # the real iPad.  A single in-between frame can contain neither a
            # classifiable DETAIL page nor complete appraisal tracks.  Keep
            # observing without touching; only another proven page can
            # authorize the next action.
            candidate = base._next_snapshot(
                proxy,
                (
                    _CLOSE_APPRAISAL_FAST_READ_DELAY_SECONDS
                    if observation == 0
                    else 0.8
                ),
            )
            if v14.snapshot_is_black(candidate):
                candidate = wait_for_capture_channel(
                    proxy, candidate, allow_game_restart=False
                )
            try:
                base._validate_expected("DETAIL", candidate)
                return candidate
            except PolicyViolation as validation_error:
                last_validation_error = validation_error

            state = "unknown"
            if candidate.image:
                try:
                    base.measure_ipad14_6_appraisal(
                        candidate.image, base.ORIENTATION
                    )
                    state = "appraisal"
                except ValueError:
                    pass
            observed_states.append(state)

        # A repeated close tap is allowed only when the last two independent
        # screenshots both prove that the appraisal tracks are still present.
        # Transitional/unknown frames never authorize a second tap.
        if attempt == 0 and observed_states[-2:] == ["appraisal", "appraisal"]:
            emit(
                "status",
                message="连续两帧确认鉴定页仍未关闭；安全重试一次关闭。",
            )
            continue
        if last_validation_error is not None:
            raise PolicyViolation(
                "关闭鉴定页后经五帧只读等待仍未验证到详情页；"
                "页面状态不明确，未重复点击"
            ) from last_validation_error
    raise PolicyViolation("无法安全关闭鉴定页")


def _display_name(result: NameRegionResult) -> str:
    if result.species:
        return result.species
    return next((token for token in result.evidence if token.strip()), "自定义昵称")


def _appraisal_identity_matches_current_detail(
    appraisal_name: NameRegionResult, detail_name: NameRegionResult
) -> bool:
    """Match an appraisal title to a pre-proven default detail without false skips.

    Detail identity has already been proven by three independent screenshots.
    The appraisal overlay can make OCR emit a second, clipped copy of the last
    glyph (for example ``蟲寶包 / 包``).  That is not a different name and
    must not be treated as a stale MCP frame.  This exception is deliberately
    narrow: the full known species must match, no numeric annotation is
    accepted, and every remaining token must be a strict final fragment of the
    proven species.  All other discrepancies stay safely unreadable.
    """

    expected = detail_name.species
    if not expected or appraisal_name.species != expected:
        return False
    if appraisal_name.is_default:
        return True
    extras: list[str] = []
    status_badge_seen = False
    for token in appraisal_name.evidence:
        candidate = token.strip()
        if not candidate or candidate == expected or HP_LINE.fullmatch(candidate):
            continue
        if is_detail_status_label(candidate):
            status_badge_seen = True
            continue
        if NUMBER_TOKEN.fullmatch(candidate):
            return False
        extras.append(candidate)
    return status_badge_seen or (
        bool(extras)
        and all(len(token) < len(expected) and expected.endswith(token) for token in extras)
    )


def _empty_appraisal_title_is_safe_on_direct_route(
    appraisal_name: NameRegionResult, *, current_detail_only: bool
) -> bool:
    """Allow an OCR-empty appraisal title only on the proven direct-detail route.

    On that route, the detail name has already been confirmed from three fresh
    frames and the only intervening action opens that same Pokémon's appraisal
    overlay.  Some local OCR frames contain the IV bars but no title glyphs at
    all.  An empty title is not identity *disagreement*, so skipping it loses a
    valid default-name Pokémon.  This exception never accepts a partial or
    conflicting title: any recognized evidence still has to pass the ordinary
    exact identity matcher above.
    """

    return current_detail_only and not appraisal_name.species and not any(
        token.strip() for token in appraisal_name.evidence
    )


def _ensure_plain_detail(proxy: SafeProxy, snapshot: Snapshot) -> Snapshot:
    """Navigate only as far as a plain detail page, never into appraisal."""

    state = v14.robust_page_state(snapshot)
    if state == "APPRAISAL_BARS":
        return _close_appraisal(proxy)
    if state == "APPRAISAL_DIALOG":
        appraisal, _measurement = _navigate_with_read_only_measurement_retry(
            proxy, snapshot
        )
        return _close_appraisal(proxy)
    if state == "DETAIL":
        base._validate_expected("DETAIL", snapshot)
        return snapshot
    supported_entry_states = {"MAP", "MAIN_MENU", "INVENTORY"}
    while state != "DETAIL":
        if state not in supported_entry_states:
            raise PolicyViolation(f"批量详情入口不支持当前页面：{state}")
        if state == "INVENTORY" and _egg_overview_visible(snapshot):
            snapshot = _select_pokemon_tab_from_egg_overview(proxy, snapshot)
            state = v14.robust_page_state(snapshot)
            if state != "INVENTORY":
                raise PolicyViolation("切回“寶可夢”标签后未确认宝可梦盒；不会继续点击")
            continue
        # Use the resilient transition rather than validating the first
        # post-tap frame.  On a real iPad the menu animation can take longer
        # than that frame, even though the original page remains proven.  The
        # transition waits up to 12 seconds and permits one retry only when
        # the source page is still independently verified.
        snapshot, state = v14._transition(proxy, snapshot, state)
    base._validate_expected("DETAIL", snapshot)
    return snapshot


def _visible_inventory_cards(snapshot: Snapshot) -> list[tuple[str, float, float, int, int]]:
    """Return readable, fully tappable inventory cards in visual order.

    The screen reader has no useful card nodes on this iPad, so the *local*
    CP labels are the only safe way to identify a concrete neighboring card.
    Bounds come from the offline OCR engine and are checked against the
    canonical Pokémon GO frame; no arbitrary grid position is ever tapped.
    """

    if not snapshot.image:
        return []
    try:
        image = rotate_mcp_image_upright(snapshot.image, base.ORIENTATION)
        width, height = image.size
        lines = ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
    except Exception:
        return []
    cards: list[tuple[str, float, float, int, int]] = []
    for line in lines:
        match = _INVENTORY_CP_TOKEN.fullmatch(line.text.replace(" ", ""))
        if not match or line.confidence < 0.80 or line.bounds is None:
            continue
        left, top, right, bottom = line.bounds
        center_x = (left + right) / 2
        center_y = (top + bottom) / 2
        # The card tap lands below the CP label, in the middle of the card.
        tap_y = center_y + height * _INVENTORY_CARD_TAP_OFFSET
        if not (
            width * 0.08 <= center_x <= width * 0.92
            and height * 0.22 <= center_y <= height * 0.72
            and height * 0.30 <= tap_y <= height * 0.84
        ):
            continue
        cards.append((f"cp{match.group(1)}", center_x, tap_y, width, height))
    return sorted(cards, key=lambda card: (card[2], card[1]))


def _egg_overview_visible(snapshot: Snapshot) -> bool:
    """Recognize the Eggs tab, which has storage capacity but no Pokémon cards."""

    if not snapshot.image:
        return False
    try:
        values = {
            line.text.strip().casefold()
            for line in ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
            if line.confidence >= 0.70
        }
    except Exception:
        return False
    # The tab label "蛋" is present on both tabs, so only its own content is
    # decisive. These two strings belong to the Egg-management panel and do
    # not occur on the Pokémon-card grid.
    return any("公里" in value for value in values) and any(
        token in values for token in ("開啟", "开启", "冒險同步", "冒险同步")
    )


def _select_pokemon_tab_from_egg_overview(proxy: SafeProxy, snapshot: Snapshot) -> Snapshot:
    """Return from Eggs only after a tab change is visually confirmed.

    iPad7,2 can expose either its visible Stage Manager coordinates or a
    rotated HID map to the touch service.  The Eggs -> Pokémon tab is the
    only harmless calibration target: its text is OCR-located, neither
    candidate changes Pokémon data, and every tap is followed by a fresh
    visual proof that the Egg panel actually disappeared.
    """

    if not snapshot.image:
        raise PolicyViolation("蛋页面截图缺失；不会猜测宝可梦标签位置")
    located = locate_exact_text_from_mcp(
        snapshot.image,
        base.ORIENTATION,
        "寶可夢",
        minimum_confidence=0.70,
    )
    x_ratio = (located.box.left + located.box.right) / (2.0 * located.image_width)
    y_ratio = (located.box.top + located.box.bottom) / (2.0 * located.image_height)
    if not (0.32 <= x_ratio <= 0.68 and 0.03 <= y_ratio <= 0.20):
        raise PolicyViolation("蛋页面的“寶可夢”标签位置不在安全顶部区域；不会点击")
    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 未返回屏幕边界")
    x, y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        x_ratio,
        y_ratio,
        geometry=base.current_stage_geometry(proxy),
    )
    candidates = [(base._PORTRAIT_WINDOW_INPUT_MAPPING, x, y)]
    if (
        base.ORIENTATION == "STAGE_MANAGER_PORTRAIT_WINDOW"
        and base._PORTRAIT_WINDOW_INPUT_MAPPING != "digitizer_normalized"
    ):
        # Older profiles use the inverse portrait capture
        # transform first. Keep the other maps only as harmless, visually
        # proven fallbacks. This target is only the top-level Pokémon tab: a
        # failed calibration cannot rename, transfer, or alter a Pokémon.
        ax_x, ax_y = base.portrait_window_ax_rotated_touch(
            observation.width, observation.height, x_ratio, y_ratio
        )
        direct_x, direct_y = base.portrait_window_visible_touch(
            observation.width, observation.height, x_ratio, y_ratio
        )
        legacy_x, legacy_y = base.portrait_window_legacy_rotated_touch(
            observation.width, observation.height, x_ratio, y_ratio
        )
        for mapping, touch_x, touch_y in (
            ("ax_rotated", ax_x, ax_y),
            ("direct", direct_x, direct_y),
            ("legacy_rotated", legacy_x, legacy_y),
        ):
            if (mapping, touch_x, touch_y) not in candidates and all(
                (touch_x, touch_y) != (known_x, known_y)
                for _known_mapping, known_x, known_y in candidates
            ):
                candidates.append((mapping, touch_x, touch_y))

    for attempt, (mapping, touch_x, touch_y) in enumerate(candidates, start=1):
        emit(
            "status",
            message=(
                "详情翻页误入“蛋”标签；正在点击 OCR 精确确认的“寶可夢”标签"
                f"回到卡片列表（触控校验 {attempt}/{len(candidates)}）。"
            ),
        )
        # The prior candidate deliberately performed fresh read-session
        # samples.  SafeProxy invalidates the original observation token on
        # each of those reads, so obtain the current token immediately before
        # this write rather than replaying the first tap's token.
        current_observation = proxy.observation
        if current_observation is None:
            raise PolicyViolation("切换“寶可夢”标签前缺少最新安全观察")
        proxy.call_tool(
            "tap_screen",
            {
                "x": touch_x,
                "y": touch_y,
                "_observation_token": current_observation.token,
                "_intent": "navigate exact Pokemon tab from verified Eggs overview",
                "_expected_after": "INVENTORY with Pokemon cards visible",
            },
        )
        reset_session = getattr(
            getattr(proxy, "client", None), "reset_read_session", None
        )
        if callable(reset_session):
            reset_session()
        for read_attempt in range(3):
            candidate = base._next_snapshot(proxy, 0.8 if read_attempt else 1.0)
            if (
                v14.robust_page_state(candidate) == "INVENTORY"
                and not _egg_overview_visible(candidate)
            ):
                if base.ORIENTATION == "STAGE_MANAGER_PORTRAIT_WINDOW":
                    base.set_portrait_window_input_mapping(mapping)
                emit(
                    "status",
                    message="已视觉确认回到“寶可夢”卡片列表；继续定位相邻下一只。",
                )
                return candidate
        if attempt < len(candidates):
            emit(
                "status",
                message=(
                    "OCR 定位的标签点击后仍显示蛋页面；"
                    "正在使用备用触控校准复核，不会点击任何宝可梦卡。"
                ),
            )
    raise PolicyViolation("点击“寶可夢”标签后仍未离开蛋页面；不会把蛋页当作宝可梦盒")


def _scroll_verified_inventory(proxy: SafeProxy, snapshot: Snapshot) -> Snapshot:
    """Scroll the observed Pokémon box once when its next card is offscreen."""

    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 未返回触控空间")
    from_x, from_y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        0.50,
        0.72,
        geometry=base.current_stage_geometry(proxy),
    )
    to_x, to_y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        0.50,
        0.36,
        geometry=base.current_stage_geometry(proxy),
    )
    proxy.call_tool(
        "swipe_screen",
        {
            "fromX": from_x,
            "fromY": from_y,
            "toX": to_x,
            "toY": to_y,
            "duration": 320,
            "steps": 20,
            "_observation_token": observation.token,
            "_intent": "navigate verified Pokemon inventory to later visible cards",
            "_expected_after": "INVENTORY with later Pokemon cards visible",
        },
    )
    return base._next_snapshot(proxy, 1.0)


def _open_next_inventory_card_after_pager_exit(
    proxy: SafeProxy,
    snapshot: Snapshot,
    previous: DetailFingerprint,
) -> Snapshot:
    """Open the visual successor if the iPad turns a detail pager into box exit.

    This is a narrowly scoped fallback for a *proven* INVENTORY return.  It
    does not pretend the failed pager reached another Pokémon: it locates the
    current card's CP in the visible box, taps only the immediately following
    readable card, and leaves the normal three-frame detail identity gate in
    place before any appraisal or rename is possible.
    """

    current_cp = previous.cp.casefold().replace(" ", "")
    if not _INVENTORY_CP_TOKEN.fullmatch(current_cp):
        raise PolicyViolation("当前详情缺少可在宝可梦盒中定位的 CP；不会猜测下一张卡")
    # The first overview frame after a pager can belong to an old streamable
    # HTTP session.  Refresh reads before deriving any card coordinate: an
    # apparent INVENTORY frame is never enough authorization to tap a card.
    reset_session = getattr(
        getattr(proxy, "client", None), "reset_read_session", None
    )
    if callable(reset_session):
        reset_session()
    inventory = base._next_snapshot(proxy, 0.8)
    if v14.robust_page_state(inventory) != "INVENTORY":
        raise PolicyViolation(
            "分页后的新 MCP 读取未确认宝可梦盒；不会根据旧盒子截图点击任何卡片"
        )
    returned_from_eggs = False
    for scroll_attempt in range(2):
        cards = _visible_inventory_cards(inventory)
        if not cards and not returned_from_eggs and _egg_overview_visible(inventory):
            returned_from_eggs = True
            inventory = _select_pokemon_tab_from_egg_overview(proxy, inventory)
            if v14.robust_page_state(inventory) != "INVENTORY":
                raise PolicyViolation("切回“寶可夢”标签后未确认宝可梦盒；不会点击卡片")
            continue
        current_index = next(
            (index for index, card in enumerate(cards) if card[0] == current_cp),
            None,
        )
        if current_index is not None and current_index + 1 < len(cards):
            next_cp, center_x, tap_y, width, height = cards[current_index + 1]
            if next_cp == current_cp:
                raise PolicyViolation("相邻宝可梦盒卡片 CP 相同；不会以 CP 猜测下一只")
            observation = proxy.observation
            if observation is None or observation.width is None or observation.height is None:
                raise PolicyViolation("MCP 未返回触控空间")
            touch_x, touch_y = base.upright_ratio_to_touch(
                observation.width,
                observation.height,
                center_x / width,
                tap_y / height,
                geometry=base.current_stage_geometry(proxy),
            )
            emit(
                "status",
                message=(
                    f"详情分页被 iPad 退出到宝可梦盒；已在同一可见顺序中定位 {current_cp.upper()}，"
                    f"正在打开紧邻的 {next_cp.upper()}。新详情仍须三帧身份核验，"
                    "通过前不计数、不鉴定、不改名。"
                ),
            )
            proxy.call_tool(
                "tap_screen",
                {
                    "x": touch_x,
                    "y": touch_y,
                    "_observation_token": observation.token,
                    "_intent": "navigate verified adjacent Pokemon inventory card",
                    "_expected_after": "DETAIL for neighboring visible Pokemon",
                },
            )
            # The new iPad can accept the touch but replay the prior detail
            # through its screenshot session.  A generic DETAIL state is not
            # enough: require the *target CP* on two fresh read-session
            # frames before handing the card to the usual three-frame name
            # gate.  This prevents a failed/stale inventory tap from silently
            # counting or skipping the same Pokémon again.
            reset_session = getattr(
                getattr(proxy, "client", None), "reset_read_session", None
            )
            if callable(reset_session):
                reset_session()
            target_reads = 0
            observed_cps: set[str] = set()
            candidate = base._next_snapshot(proxy, 1.0)
            for read_attempt in range(6):
                if v14.robust_page_state(candidate) == "DETAIL":
                    try:
                        candidate_fingerprint = detail_fingerprint(
                            candidate, require_name=False
                        )
                    except PolicyViolation:
                        candidate_fingerprint = None
                    if candidate_fingerprint is not None:
                        candidate_cp = candidate_fingerprint.cp.casefold().replace(" ", "")
                        if candidate_cp == next_cp:
                            target_reads += 1
                            if target_reads >= 2:
                                return candidate
                        elif candidate_cp:
                            observed_cps.add(candidate_cp.upper())
                if read_attempt == 2 and callable(reset_session):
                    # Resetting a read session never replays the card tap.
                    # It only prevents cached pixels from masking the result.
                    reset_session()
                candidate = base._next_snapshot(proxy, 0.8)
            observed = "、".join(sorted(observed_cps)) or "未读取到 CP"
            raise PolicyViolation(
                f"相邻卡点击后未实际切到 {next_cp.upper()}（读取到 {observed}）；"
                "已停止，未重复点击或增加进度"
            )
        if scroll_attempt == 0:
            emit(
                "status",
                message=(
                    "当前可见宝可梦盒已到最后一张完整卡；"
                    "正在仅滚动盒子列表一次以继续定位紧邻下一只。"
                ),
            )
            inventory = _scroll_verified_inventory(proxy, inventory)
            continue
        raise PolicyViolation("宝可梦盒中未能定位当前 CP 的紧邻下一张卡；不会猜测或重复点击")
    raise PolicyViolation("宝可梦盒相邻卡定位未完成")


def _return_detail_to_pager_resume_inventory(
    proxy: SafeProxy, snapshot: Snapshot
) -> Snapshot:
    """Return one mistakenly opened detail to its own box before exact resume.

    This is only used after the local reader proves that the current detail's
    CP differs from the persisted pre-pager CP. It never selects a card by
    position: the caller immediately reuses the normal CP-bounded successor
    locator on the resulting inventory frame.
    """

    if v14.robust_page_state(snapshot) != "DETAIL":
        raise PolicyViolation("恢复上一轮翻页前当前页不是详情；不会尝试返回宝可梦盒")
    base._validate_expected("DETAIL", snapshot)
    base._tap(proxy, "DETAIL_CLOSE")
    inventory = base._next_snapshot(proxy, 1.0)
    if v14.robust_page_state(inventory) != "INVENTORY":
        raise PolicyViolation("关闭误开的详情后未确认宝可梦盒；不会点击任何卡片")
    return inventory


def _wait_for_resume_detail_identity(
    proxy: SafeProxy, snapshot: Snapshot, anchor: DetailFingerprint
) -> tuple[Snapshot, DetailFingerprint]:
    """Read through a temporary system banner before deciding resume identity.

    A location-error banner can cover only CP while leaving HP, weight and
    height fully visible. Any mismatch among those immutable fields is already
    positive proof that this is not the anchor detail, so it is safer and
    faster than waiting for the banner to disappear.
    """

    candidate = snapshot
    for attempt in range(1, 13):
        if v14.robust_page_state(candidate) != "DETAIL":
            raise PolicyViolation("恢复翻页时当前页不再是详情；不会猜测下一张卡")
        fingerprint = detail_fingerprint(candidate, require_name=False)
        if fingerprint.cp or fingerprints_differ(anchor, fingerprint):
            return candidate, fingerprint
        emit(
            "waiting",
            stage="恢复翻页身份核验",
            reason="当前详情的 CP 被系统横幅遮挡，且其余不可变字段仍不足以确认身份。",
            attempt=attempt,
            total=12,
            elapsed_seconds=attempt,
            next_action="读取下一张同一详情截图；CP 或不可变字段可区分时才决定是否回到已验证的相邻卡。",
            user_action="无需操作；后台只读等待系统横幅消失。",
        )
        candidate = base._next_snapshot(proxy, 1.0)
    raise PolicyViolation("恢复翻页时详情身份连续 12 次不可区分；不会猜测或点击")


def _measurement_key(measurement) -> tuple[int, int, int]:
    return (
        int(measurement.attack),
        int(measurement.defense),
        int(measurement.stamina),
    )


def _confirm_low_confidence_measurement(proxy: SafeProxy, snapshot: Snapshot, measurement):
    """Require three agreeing dual-decoder frames before any rename.

    The v6 reader already requires divider geometry, physical track start,
    endpoint decoding and 15-cell occupancy decoding to agree on each frame.
    This final gate rejects a Pokémon if any independently valid frame reports
    a different integer triple.  It performs screenshots only and never taps.
    """

    samples: list[tuple[Snapshot, object, str]] = []
    blocked = set(_frame_history(proxy))
    try:
        initial_digest = _snapshot_digest(snapshot)
    except PolicyViolation:
        initial_digest = ""
    if (
        float(measurement.confidence) >= _CONSENSUS_MEASUREMENT_CONFIDENCE
        and initial_digest
        and initial_digest not in blocked
    ):
        samples.append((snapshot, measurement, initial_digest))
    emit(
        "status",
        message=(
            f"鉴定条初帧双解码置信度 {measurement.confidence:.1%}；"
            "正在追加只读截图，要求三帧 IV 完全一致。"
        ),
    )
    for attempt in range(1, _MEASUREMENT_READ_ONLY_RETRIES + 1):
        retry = base._next_snapshot(
            proxy,
            (
                _MEASUREMENT_FAST_READ_DELAY_SECONDS
                if attempt <= 2
                else 1.25
            ),
        )
        if v14.snapshot_is_black(retry):
            retry = wait_for_capture_channel(
                proxy, retry, allow_game_restart=False
            )
        if not retry.image:
            continue
        try:
            fresh = base.measure_ipad14_6_appraisal(
                retry.image, base.ORIENTATION
            )
        except ValueError:
            continue
        if float(fresh.confidence) < _CONSENSUS_MEASUREMENT_CONFIDENCE:
            continue
        digest = _snapshot_digest(retry)
        if digest in blocked or digest in {item[2] for item in samples}:
            continue
        fresh_key = _measurement_key(fresh)
        existing_keys = {_measurement_key(item[1]) for item in samples}
        if existing_keys and fresh_key not in existing_keys:
            emit(
                "status",
                message=(
                    f"多帧 IV 出现冲突：{sorted(existing_keys)} 与 {fresh_key}；"
                    "本只绝不改名。"
                ),
            )
            return None
        samples.append((retry, fresh, digest))
        if len(samples) >= 3:
            key = _measurement_key(fresh)
            confidences = ", ".join(
                f"{float(item[1].confidence):.1%}" for item in samples
            )
            _remember_fresh_frames(proxy, [item[2] for item in samples])
            emit(
                "status",
                message=(
                    f"三张未复用像素帧双解码 IV 一致确认 "
                    f"A/D/S={key[0]}/{key[1]}/{key[2]} "
                    f"（{confidences}）；继续本只。"
                ),
            )
            return retry, fresh
    return None


def _process_one(
    proxy: SafeProxy,
    snapshot: Snapshot,
    *,
    mode: str,
    index: int,
    current_detail_only: bool = False,
    identity_seed_samples: tuple[Snapshot, ...] = (),
) -> tuple[Snapshot, str]:
    # A prior run, reconnect, or slow post-Appraise capture can leave the
    # current card underneath a proven appraisal dialogue/bars overlay.  The
    # overlay still exposes HP/kg text, so the older DETAIL validator accepted
    # it and the identity reader then spent its entire retry budget taking
    # screenshots of a title obscured by the team leader.  Recover this known
    # descendant first, regardless of entry mode: advance a proven dialogue
    # once when necessary, close the appraisal layer, then read the same card.
    # No box navigation or game relaunch is involved.
    try:
        entry_state = base.local_page_state(snapshot)
    except Exception:
        entry_state = "UNKNOWN"
    if entry_state in {"APPRAISAL_DIALOG", "APPRAISAL_BARS"}:
        emit(
            "status",
            message=(
                "当前仍在上一轮鉴定层；正在立即完成/关闭该层后核验同一只，"
                "不会继续空截图等待。"
            ),
        )
        snapshot = _restore_direct_detail_after_interrupted_appraisal(proxy, snapshot)

    if current_detail_only:
        try:
            current_state = base.local_page_state(snapshot)
        except Exception:
            current_state = "UNKNOWN"
        if current_state == "DETAIL_MENU":
            snapshot = _restore_direct_detail_from_existing_menu(proxy, snapshot)
    snapshot = (
        _wait_for_verified_next_detail(
            proxy, snapshot, seed_samples=identity_seed_samples
        )
        if current_detail_only
        else _ensure_plain_detail(proxy, snapshot)
    )
    # Automatic entry can legitimately land on the lower half of an existing
    # detail after a previous gesture.  Bring only that already-proven card
    # back to its title row before identity OCR; without this, the move and
    # source labels are mistaken for an unreadable nickname and the batch
    # burns its whole read-only retry budget on a perfectly healthy detail.
    snapshot = _scroll_verified_detail_to_title(proxy, snapshot)
    detail_identity = _confirm_fresh_detail_identity(
        proxy, snapshot, seed_samples=identity_seed_samples
    )
    if detail_identity is None:
        detail_identity = _probe_unreadable_name_via_untouched_dialog(
            proxy, snapshot
        )
    if detail_identity is None:
        emit(
            "waiting",
            stage="详情身份核验",
            reason="名称 OCR 未能形成三次一致的默认名或已有昵称证据；不会把未验证卡片计为已处理。",
            attempt=1,
            total=1,
            elapsed_seconds=0,
            next_action="重置只读截图会话后重新核验同一只。",
            user_action="无需操作；后台不会翻页、不会改名。",
            message=(
                f"第 {index} 只未取得三张新鲜详情身份帧；"
                "可能是名称 OCR 不完整或截图重复，正在重新核验同一只。"
            ),
        )
        raise FreshDetailIdentityUnavailable(snapshot)
    snapshot, detail_name = detail_identity
    emit(
        "detail",
        index=index,
        species=detail_name.species,
        current_name=_display_name(detail_name),
        is_default=detail_name.is_default,
    )
    if not detail_name.is_default or not detail_name.species:
        evidence = " / ".join(detail_name.evidence) or "非完整默认物种名"
        emit(
            "status",
            message=f"第 {index} 只已有昵称，未打开鉴定或改名并继续：{evidence}",
        )
        return snapshot, "skipped"

    try:
        navigate_to_appraisal = (
            _navigate_from_current_detail_only
            if current_detail_only
            else _navigate_with_complete_stale_recovery
        )
        appraisal, measurement = navigate_to_appraisal(proxy, snapshot)
    except AppraisalMeasurementUnavailable as exc:
        # Navigation into Appraise completed and all later operations were
        # screenshots only, so the last known page is still the appraisal
        # overlay.  Close it through the calibrated safe control, preserve the
        # current Pokémon unchanged, and let the batch continue.
        if not exc.snapshot.image:
            raise PolicyViolation("鉴定重测截图缺失；无法安全恢复详情页") from exc
        _save_unreadable_appraisal(exc.snapshot, index)
        detail = _close_appraisal(proxy)
        emit(
            "status",
            message=(
                f"第 {index} 只鉴定条暂时不可读，已保留原名并继续下一只。"
            ),
        )
        return detail, "unreadable"
    confirmed = _confirm_low_confidence_measurement(proxy, appraisal, measurement)
    if confirmed is not None:
        appraisal, measurement = confirmed
    else:
        # No rename control has been opened and the appraisal overlay is
        # still the last verified page, so this remains a recoverable
        # per-Pokemon miss rather than a fatal batch error.
        _save_unreadable_appraisal(appraisal, index)
        detail = _close_appraisal(proxy)
        emit(
            "status",
            message=(
                f"第 {index} 只鉴定条未通过三帧双解码一致性验证，"
                "已保留原名并继续下一只。"
            ),
        )
        return detail, "unreadable"
    if not appraisal.image:
        raise PolicyViolation("鉴定截图缺失")
    emit(
        "iv_measurement",
        attack=measurement.attack,
        defense=measurement.defense,
        stamina=measurement.stamina,
        confidence=measurement.confidence,
        endpoints=list(measurement.endpoints),
    )
    name = analyze_name_region(appraisal.image, base.ORIENTATION)
    identity_matches = _appraisal_identity_matches_current_detail(name, detail_name)
    title_was_empty = _empty_appraisal_title_is_safe_on_direct_route(
        name, current_detail_only=current_detail_only
    )
    if not identity_matches and not title_was_empty:
        detail = _close_appraisal(proxy)
        evidence = " / ".join(name.evidence) or "鉴定帧未确认默认物种名"
        emit(
            "status",
            message=(
                f"第 {index} 只详情身份 {detail_name.species} 与鉴定帧不一致；"
                f"疑似 MCP 旧缓存，绝不改名并继续：{evidence}"
            ),
        )
        return detail, "unreadable"
    if title_was_empty:
        emit(
            "status",
            message=(
                "鉴定页标题本帧未返回文字；当前详情已由三张新鲜截图确认默认名，"
                "且期间只进入鉴定，继续当前一只。"
            ),
        )
    elif not name.is_default:
        emit(
            "status",
            message=(
                "鉴定页名称 OCR 出现同物种末尾残片；详情页已由三帧确认默认名，"
                "继续当前一只。"
            ),
        )

    nickname = generate_iv_nickname(
        detail_name.species,
        measurement.attack,
        measurement.defense,
        measurement.stamina,
    )
    emit(
        "pokemon",
        species=detail_name.species,
        current_name=detail_name.species,
        attack=measurement.attack,
        defense=measurement.defense,
        stamina=measurement.stamina,
        percent=iv_percent(measurement.attack, measurement.defense, measurement.stamina),
        nickname=nickname,
        confidence=measurement.confidence,
        name_confidence=name.confidence,
    )
    if mode == "scan":
        return _close_appraisal(proxy), "scanned"

    try:
        # Reuse the batch close helper instead of v16's historical one-frame
        # close.  Appraisal animations and a slow screenshot can otherwise
        # reject a successful close and terminate the whole batch before the
        # pencil is ever touched.
        detail_before_rename = _close_appraisal(proxy)
        open_dynamic_rename_from_detail(
            proxy, detail_before_rename, detail_name.species
        )
    except RenamePencilLocalizationUnavailable as exc:
        # The typed exception is raised only after a verified DETAIL snapshot
        # and only before a pencil tap.  No dialog or keyboard can be pending,
        # so preserving this one and continuing is unambiguous.
        base._validate_expected("DETAIL", exc.snapshot)
        emit(
            "status",
            message=(
                f"第 {index} 只名称边界暂时不可读，已保留原名并继续下一只；"
                "本只稍后可重新处理。"
            ),
        )
        return exc.snapshot, "unreadable"
    # Until SafeProxy accepts an input request, this is the exact verified
    # default species whose field this worker alone is editing.  Preserve the
    # marker only if a device lock interrupts this commit path.
    _set_lock_recovery_default_species(proxy, detail_name.species)
    try:
        detail = _commit_after_dismissing_keyboard(
            proxy,
            current_name=detail_name.species,
            species=detail_name.species,
            nickname=nickname,
        )
    except DeviceLockRecoveryRequired:
        raise
    except RenameFieldVerificationUnavailable as exc:
        _clear_lock_recovery_default_species(proxy)
        # A verified Cancel can occasionally be acknowledged by MCP while
        # Pokémon GO leaves the same populated dialog visible.  Do not enter
        # its old unbounded DETAIL wait.  The restart-recovery helper below
        # accepts this only when the durable input journal and fresh pixels
        # prove a non-default deterministic nickname; otherwise it raises
        # without clicking either button.
        if base.local_page_state(exc.snapshot) == "RENAME_DIALOG":
            before = proxy.verified_renames
            # _process_one deliberately receives only the supervised proxy;
            # its Settings are the active run Settings.  Referencing the
            # outer run-local ``settings`` here raised NameError precisely in
            # the recovery path used after an unreadable keyboard field.
            detail = _resume_verified_unsubmitted_rename(
                proxy, exc.snapshot, proxy.settings
            )
            if proxy.verified_renames != before + 1:
                raise PolicyViolation("遗留改名弹窗恢复未验证为一次成功提交")
            return detail, "renamed"
        base._validate_expected("DETAIL", exc.snapshot)
        emit(
            "status",
            message=(
                f"第 {index} 只输入字段暂时不可核验；"
                "已取消未提交编辑、保留原名并继续下一只。"
            ),
        )
        return exc.snapshot, "unreadable"
    except BaseException:
        _clear_lock_recovery_default_species(proxy)
        raise
    _clear_lock_recovery_default_species(proxy)
    # The returned snapshot was just validated by the submit routine as a
    # dialog-free DETAIL frame.  Do not spend another MCP screenshot round
    # trip proving the same condition again.
    if not isinstance(detail, Snapshot):
        raise PolicyViolation("提交后没有返回已验证的详情页截图")
    base._validate_expected("DETAIL", detail)
    emit("renamed", nickname=nickname)
    return detail, "renamed"


def run(mode: str, settings: Settings) -> int:
    with DeviceRunLock(settings.journal_path.parent / "iphone-mcp.lock"):
        previous_original = v14._ORIGINAL_NAVIGATE
        previous_wait_until_visible = v14._wait_until_visible
        previous_next_snapshot = base._next_snapshot
        previous_orientation = base.ORIENTATION
        v14._ORIGINAL_NAVIGATE = _navigate_with_read_only_measurement_retry
        # Appraisal navigation uses this hook.  It must never turn a transient
        # black MCP frame into a Home/launch/restart sequence.
        v14._wait_until_visible = _wait_without_game_restart
        try:
            # The iPadOS 17 MCP service can take longer than 20 seconds to
            # deliver a fresh game screenshot after reconnect/unlock.  Treat
            # that as a slow read rather than a false disconnect.  Writes are
            # still never retried by ResilientStreamableHTTPClient.
            client = ResilientStreamableHTTPClient(settings, timeout=45.0)
            device = base._device_details(client.call_tool("get_device_info", {}))
            if not _matches_configured_device(device):
                expected = os.getenv("POGO_EXPECTED_DEVICE_MACHINE", "").strip()
                actual = str(device.get("machine", ""))
                raise PolicyViolation(
                    f"MCP 地址绑定的是 {expected}，但实际返回 {actual}；"
                    "已在任何触控前停止，请检查 iPad 地址。"
                )
            if not _is_calibrated_landscape_device(device):
                raise PolicyViolation(
                    "批量横屏流程仅支持已校准型号且触控空间为 1366×1024 的设备"
                )
            base.ORIENTATION = _orientation_for_calibrated_device(device)
            emit(
                "device",
                name=str(device.get("deviceName", "iPad")),
                machine=str(device.get("machine", "")),
                system=str(device.get("systemName", "iPadOS")),
                version=str(device.get("systemVersion", "")),
                width=device.get("screenWidth"),
                height=device.get("screenHeight"),
            )
            limit_text = (
                "不限数量，直到盒子末尾或用户停止"
                if settings.batch_limit == BATCH_LIMIT_UNLIMITED
                else f"最多 {settings.batch_limit} 只"
            )
            emit("status", message=f"批量模式：{limit_text}；已命名会保留并自动继续。")
            emit("status", message=f"本地繁中物种表已加载：{len(traditional_chinese_species())} 个名称。")
            proxy = SafeProxy(settings, client)
            lock_reentry_requires_recovery = False

            def device_aware_next_snapshot(
                active_proxy: SafeProxy, delay: float = 2.5
            ) -> Snapshot:
                fresh = previous_next_snapshot(active_proxy, delay)
                fresh = wait_for_unlocked_snapshot(
                    active_proxy,
                    fresh,
                    require_safe_reentry=lock_reentry_requires_recovery,
                )
                # The calibrated capture/touch profile is selected at entry.
                # Never replace it midway through a pager or dialog because a
                # single animation frame resembles a full-screen white card:
                # doing so changes both OCR coordinates and future touch points.
                # Unexpected geometry must go through the normal read-only
                # validation/recovery gates, not silently change this profile.
                return _publish_live_snapshot(fresh)

            # Every downstream module resolves base._next_snapshot at call
            # time.  Installing one device-state gate here makes lock/off
            # recovery consistent during appraisal, rename verification and
            # next-Pokémon swipes, including non-black lock-screen captures.
            base._next_snapshot = device_aware_next_snapshot
            snapshot = wait_for_capture_channel(
                proxy, screen_snapshot(proxy), allow_game_restart=False
            )
            _adopt_current_capture_profile(snapshot)
            snapshot = _publish_live_snapshot(snapshot)
            if os.getenv("POGO_START_FROM_CURRENT_DETAIL", "").strip().casefold() in {
                "1",
                "true",
                "yes",
                "on",
            }:
                # The screenshot can retain iPad's surrounding task cards
                # while the calibrated Pokémon GO crop itself is already a
                # stable DETAIL.  Direct mode must trust that stronger local
                # proof and continue its normal detail-only sequence; it must
                # not block merely because unrelated SpringBoard AX text is
                # still visible around the game surface.
                snapshot = _restore_direct_detail_after_interrupted_appraisal(
                    proxy, snapshot
                )
                snapshot = _resume_verified_unsubmitted_rename(
                    proxy, snapshot, settings
                )
                # A system overview may have appeared while an appraisal was
                # open.  Once it clears, close only that known appraisal
                # layer before requiring the original direct-detail route.
                snapshot = _restore_direct_detail_after_interrupted_appraisal(
                    proxy, snapshot
                )
            if _has_ipad_task_switcher_overlay(snapshot):
                emit(
                    "status",
                    message=(
                        "检测到 iPad 多任务切换层覆盖 Pokémon GO；后台只读等待"
                        "原详情页恢复，不会点击其他 App 或重新打开游戏。"
                    ),
                )
                snapshot = _wait_for_direct_detail_after_task_switcher(proxy, snapshot)
                current_detail_only = True
            else:
                current_detail_only = _current_detail_only(snapshot)
            resume_fingerprint = _load_pager_resume(settings)
            resume_state = v14.robust_page_state(snapshot)
            if resume_state == "INVENTORY" and resume_fingerprint is not None:
                emit(
                    "status",
                    message=(
                        "检测到上次已确认的详情翻页把页面带回宝可梦盒；"
                        f"只会从 {resume_fingerprint.cp.upper()} 定位紧邻卡片并核验，"
                        "不会选择任意第一只、不会重开游戏。"
                    ),
                )
                snapshot = _open_next_inventory_card_after_pager_exit(
                    proxy, snapshot, resume_fingerprint
                )
            elif current_detail_only:
                if resume_state == "DETAIL" and resume_fingerprint is not None:
                    emit(
                        "status",
                        message="正在核验中断前的当前详情身份。",
                    )
                    snapshot, current_fingerprint = _wait_for_resume_detail_identity(
                        proxy, snapshot, resume_fingerprint
                    )
                    if (
                        current_fingerprint.cp != resume_fingerprint.cp
                        or fingerprints_differ(resume_fingerprint, current_fingerprint)
                    ):
                        emit(
                            "status",
                            message=(
                                f"恢复时发现当前详情为 {current_fingerprint.cp.upper()}，"
                                f"而上次翻页锚点是 {resume_fingerprint.cp.upper()}；"
                                "将只关闭这张误开详情，回到盒子后定位其紧邻卡片。"
                            ),
                        )
                        inventory = _return_detail_to_pager_resume_inventory(
                            proxy, snapshot
                        )
                        snapshot = _open_next_inventory_card_after_pager_exit(
                            proxy, inventory, resume_fingerprint
                        )
                    else:
                        snapshot = _wait_for_direct_detail_after_task_switcher(
                            proxy, snapshot
                        )
                else:
                    emit(
                        "status",
                        message=(
                            "从当前手动打开的详情页开始；不会点击精灵球、宝可梦盒、"
                            "图鉴，也不会重启游戏。"
                        ),
                    )
                    snapshot = _wait_for_direct_detail_after_task_switcher(proxy, snapshot)
            else:
                try:
                    snapshot = _ensure_game_foreground(proxy, snapshot)
                except ValueError as exc:
                    if (
                        not _is_unsafe_stage_manager_geometry(exc)
                        or not _game_restart_allowed()
                    ):
                        raise
                    snapshot = _recover_from_transient_navigation_failure(proxy)
            # Before a current detail has been established, a device lock can
            # only interrupt page discovery.  Thereafter it may interrupt a
            # dialog action, so it must force an explicit safe re-entry.
            lock_reentry_requires_recovery = True
            counts = {"renamed": 0, "skipped": 0, "scanned": 0, "unreadable": 0}
            if current_detail_only:
                saved_direction = _load_pager_direction(settings)
                # Older runs inferred direction from any identity change. That
                # cannot distinguish next from previous and persisted `right`
                # after walking backward. Migrate that stale value to the
                # user-verified next-card gesture and never probe its opposite.
                next_direction = NEXT_PAGER_DIRECTION
                proxy._batch_swipe_direction = next_direction
                if saved_direction != next_direction:
                    _save_pager_direction(settings, next_direction)
                    emit(
                        "status",
                        message=(
                            "已修正下一只的翻页方向：从右往左（←）；"
                            "旧的反向记录已迁移，不会再用身份变化猜方向。"
                        ),
                    )
                else:
                    emit(
                        "status",
                        message="已恢复下一只的固定翻页方向：从右往左（←）；不会探测反向。",
                    )
            pause = _pause_file(settings)
            index = 1
            transient_recoveries = 0
            identity_read_recoveries = 0
            identity_seed_samples: tuple[Snapshot, ...] = ()
            last_known_fingerprint: DetailFingerprint | None = None
            completion_reason = "已达到配置的处理数量。"

            while (
                settings.batch_limit == BATCH_LIMIT_UNLIMITED
                or index <= settings.batch_limit
            ):
                _emit_progress(
                    current=index,
                    limit=settings.batch_limit,
                    phase="processing",
                    counts=counts,
                )
                progress_text = (
                    f"第 {index} 只"
                    if settings.batch_limit == BATCH_LIMIT_UNLIMITED
                    else f"第 {index}/{settings.batch_limit} 只"
                )
                emit("status", message=f"正在处理{progress_text}…")
                try:
                    # A post-swipe evidence bundle is bound to precisely one
                    # next detail.  Consume it before any recovery path so it
                    # can never be reused after a pause, retry, or restart.
                    seed_samples = identity_seed_samples
                    identity_seed_samples = ()
                    # This pre-write snapshot is already the current verified
                    # detail.  It can be used only after a later rename was
                    # fully committed and character-for-character checked;
                    # a confirmed custom-name skip builds its fallback from
                    # its own three-frame-confirmed detail instead.
                    pre_navigation_fallback = _name_agnostic_navigation_fingerprint(
                        snapshot
                    )
                    fallback_navigation_fingerprint = pre_navigation_fallback
                    detail, outcome = _process_one(
                        proxy,
                        snapshot,
                        mode=mode,
                        index=index,
                        current_detail_only=current_detail_only,
                        identity_seed_samples=seed_samples,
                    )
                    detail, fingerprint = wait_for_stable_detail_fingerprint(
                        proxy,
                        detail,
                        verified_navigation_fallback=(
                            pre_navigation_fallback
                            if outcome == "renamed"
                            else (
                                pre_navigation_fallback
                                if outcome == "skipped"
                                else pre_navigation_fallback
                            )
                        ),
                    )
                    last_known_fingerprint = fingerprint
                    _remember_fresh_frames(proxy, [_snapshot_digest(detail)])
                    identity_read_recoveries = 0
                except FreshDetailIdentityUnavailable as exc:
                    # A stale/MCP-replayed detail frame cannot represent a
                    # completed Pokémon.  Keep the same index, issue no
                    # swipe, and recover only the screenshot stream.  This
                    # is intentionally bounded: a permanently stale stream
                    # is safer to report than to turn into invented progress.
                    identity_read_recoveries += 1
                    if identity_read_recoveries > _MAX_READ_SESSION_NAVIGATION_RECOVERIES:
                        fallback = (
                            _name_agnostic_navigation_fingerprint(exc.snapshot)
                            or fallback_navigation_fingerprint
                            or last_known_fingerprint
                            or _visual_navigation_fingerprint(exc.snapshot)
                        )
                        # 该只详情无法稳定核验名称，但已由三帧同位置信息确认画面有效；
                        # 不做改名，只保留该卡并继续，避免整轮卡死。
                        emit(
                            "status",
                            message=(
                                f"第 {index} 只详情身份长期未稳定读取；"
                                "已记录为暂不可读并继续下一只，不会重开游戏。"
                            ),
                        )
                        detail = exc.snapshot
                        outcome = "unreadable"
                        fingerprint = fallback
                        identity_read_recoveries = 0
                        snapshot = detail
                        identity_seed_samples = ()
                        _remember_fresh_frames(proxy, [_snapshot_digest(detail)])
                        # Continue through normal completion bookkeeping and
                        # swipe-boundary checks using the best-known safe
                        # same-card fingerprint.
                    else:
                        emit(
                            "waiting",
                            stage="详情截图会话恢复",
                            reason="当前卡片未取得新鲜身份帧，不能安全计入处理数。",
                            attempt=identity_read_recoveries,
                            total=_MAX_READ_SESSION_NAVIGATION_RECOVERIES,
                            elapsed_seconds=0,
                            next_action="重置只读截图会话并重试同一只。",
                            user_action="无需操作；不会翻页、不会改名、不会重开游戏。",
                        )
                        reset_session = getattr(
                            getattr(proxy, "client", None), "reset_read_session", None
                        )
                        if callable(reset_session):
                            reset_session()
                        snapshot = base._next_snapshot(proxy, 0.8)
                        identity_seed_samples = ()
                        continue
                except DeviceLockRecoveryRequired as exc:
                    snapshot, recovered_outcome = _recover_after_device_unlock(
                        proxy, exc.snapshot, settings
                    )
                    # A post-swipe proof belongs only to the pre-lock page;
                    # it is deliberately discarded before re-reading.
                    identity_seed_samples = ()
                    if recovered_outcome == "retry":
                        emit(
                            "status",
                            message=(
                                f"第 {index} 只已从解锁后的安全详情页恢复；"
                                "正在重新处理同一只，不会跳过。"
                            ),
                        )
                        continue
                    # The recovery routine returns "renamed" only after the
                    # journal, exact live field and post-OK DETAIL proof all
                    # established one completed rename.
                    detail = snapshot
                    outcome = recovered_outcome
                    pre_navigation_fallback = _name_agnostic_navigation_fingerprint(
                        detail
                    )
                    detail, fingerprint = wait_for_stable_detail_fingerprint(
                        proxy,
                        detail,
                        verified_navigation_fallback=pre_navigation_fallback,
                    )
                    _remember_fresh_frames(proxy, [_snapshot_digest(detail)])
                except (PolicyViolation, ValueError) as exc:
                    if current_detail_only and _foreground_proof_was_interrupted(exc):
                        # Do not reuse the stale appraisal/dialog snapshot
                        # that triggered the rejected write.  First obtain a
                        # new frame, then recover only from a verified game
                        # surface after the system overview is gone.
                        snapshot, recovered_outcome = _recover_after_task_switcher_interrupt(
                            proxy,
                            base._next_snapshot(proxy, 0.0),
                            settings,
                        )
                        identity_seed_samples = ()
                        if recovered_outcome == "retry":
                            emit(
                                "status",
                                message=(
                                    f"第 {index} 只已从系统覆盖层安全恢复；"
                                    "正在重新处理同一只，不会跳过。"
                                ),
                            )
                            continue
                        detail = snapshot
                        outcome = recovered_outcome
                        pre_navigation_fallback = _name_agnostic_navigation_fingerprint(
                            detail
                        )
                        detail, fingerprint = wait_for_stable_detail_fingerprint(
                            proxy,
                            detail,
                            verified_navigation_fallback=pre_navigation_fallback,
                        )
                        _remember_fresh_frames(proxy, [_snapshot_digest(detail)])
                    elif (
                        not current_detail_only
                        and _game_restart_allowed()
                        and
                        (
                            _is_recoverable_navigation_failure(exc)
                            or _is_unsafe_stage_manager_geometry(exc)
                        )
                        and transient_recoveries < _MAX_TRANSIENT_NAVIGATION_RECOVERIES
                    ):
                        transient_recoveries += 1
                        emit(
                            "status",
                            message=(
                                f"第 {index} 只的详情身份帧暂不可用；"
                                f"正在执行第 {transient_recoveries}/"
                                f"{_MAX_TRANSIENT_NAVIGATION_RECOVERIES} 次游戏恢复后重试。"
                            ),
                        )
                        snapshot = _recover_from_transient_navigation_failure(proxy)
                        continue
                    else:
                        raise
                counts[outcome] += 1
                _emit_progress(
                    current=index,
                    limit=settings.batch_limit,
                    phase="completed",
                    counts=counts,
                )
                if (
                    settings.batch_limit != BATCH_LIMIT_UNLIMITED
                    and index >= settings.batch_limit
                ):
                    snapshot = detail
                    completion_reason = "已达到配置的处理数量。"
                    break
                detail = _wait_at_safe_pause_boundary(
                    proxy,
                    detail,
                    fingerprint=fingerprint,
                    index=index,
                    limit=settings.batch_limit,
                    counts=counts,
                    pause=pause,
                )
                emit("status", message="正在翻到下一只并验证身份变化…")
                # If this iPad turns the pager gesture into a return to the
                # already-visible box, the following run can resume from the
                # exact proven CP instead of guessing an arbitrary first card.
                _save_pager_resume(settings, fingerprint)
                try:
                    next_detail = _swipe_to_verified_next_with_read_recovery(
                        proxy,
                        detail,
                        fingerprint,
                        allow_opposite_direction=current_detail_only,
                    )
                    snapshot = next_detail.snapshot
                    identity_seed_samples = next_detail.samples
                    _save_pager_direction(settings, getattr(proxy, "_batch_swipe_direction", None))
                    _clear_pager_resume(settings)
                except VerifiedEndOfStorage:
                    completion_reason = "已验证当前盒子末尾：连续四次翻页仍为同一稳定身份。"
                    emit(
                        "status",
                        message=(
                            "纯详情页连续四次翻页后仍为同一稳定身份；"
                            "已验证当前盒子末尾。"
                        ),
                    )
                    break
                except NoNextPokemon as exc:
                    if isinstance(exc, DetailExitedToOverview):
                        # This iPad's detail pager may visibly return to the
                        # same storage grid. Treat it as a constrained
                        # continuation even in direct-detail mode: locate the
                        # last proven CP and open only its adjacent visible
                        # card. The usual three-frame gate still runs before
                        # appraisal or rename, and no arbitrary first card is
                        # ever selected.
                        snapshot = _open_next_inventory_card_after_pager_exit(
                            proxy, exc.snapshot, fingerprint
                        )
                        identity_seed_samples = ()
                        transient_recoveries = 0
                        index += 1
                        continue
                    if (
                        not current_detail_only
                        and _game_restart_allowed()
                        and transient_recoveries < _MAX_TRANSIENT_NAVIGATION_RECOVERIES
                    ):
                        transient_recoveries += 1
                        emit(
                            "status",
                            message=(
                                f"第 {index} 只翻页暂未得到可验证详情；"
                                f"正在执行第 {transient_recoveries}/"
                                f"{_MAX_TRANSIENT_NAVIGATION_RECOVERIES} 次游戏恢复后重试。"
                            ),
                        )
                        snapshot = _recover_from_transient_navigation_failure(proxy)
                        continue
                    raise PolicyViolation(
                        f"{exc}；连续恢复后仍无法安全翻页，本轮安全停止"
                    ) from exc
                transient_recoveries = 0
                index += 1

            emit(
                "finished",
                message=(
                    f"批量结束：{completion_reason} 处理 {sum(counts.values())} 只，"
                    f"改名 {counts['renamed']}，跳过已有昵称 {counts['skipped']}，"
                    f"暂不可读安全保留 {counts['unreadable']}，"
                    f"只读扫描 {counts['scanned']}。"
                ),
            )
            return 0
        finally:
            base._next_snapshot = previous_next_snapshot
            v14._ORIGINAL_NAVIGATE = previous_original
            v14._wait_until_visible = previous_wait_until_visible
            base.ORIENTATION = previous_orientation


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe batch iPad Pokémon renamer v26")
    parser.add_argument("--mode", choices=("scan", "rename"), required=True)
    args = parser.parse_args(argv)
    try:
        return run(args.mode, Settings.from_env())
    except Exception as exc:
        emit("error", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
