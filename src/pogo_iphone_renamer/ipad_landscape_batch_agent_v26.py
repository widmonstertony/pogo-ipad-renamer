from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import time
from pathlib import Path

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v14 as v14
from .appraisal_agent import Snapshot, screen_snapshot
from .batch_navigation_v26 import (
    DetailFingerprint,
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
    open_dynamic_rename_from_detail,
)
from .ipad_landscape_agent_v15 import (
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
from .landscape_cv_v6 import measure_ipad14_6_appraisal_v6
from .local_ocr import exact_species_from_lines, ocr_mcp_screenshot, rename_dialog_visible
from .local_ocr_v4 import locate_exact_text_from_mcp
from .local_ocr_v3 import HP_LINE, NUMBER_TOKEN, NameRegionResult, analyze_name_region
from .native_agent import emit
from .native_agent_v2 import ResilientStreamableHTTPClient
from .nickname import generate_iv_nickname, iv_percent
from .policy import PolicyViolation
from .protocol import text_from_content
from .server import SafeProxy
from .species_db import traditional_chinese_species


base.measure_ipad14_6_appraisal = measure_ipad14_6_appraisal_v6


_CONSENSUS_MEASUREMENT_CONFIDENCE = 0.80
_MEASUREMENT_READ_ONLY_RETRIES = 12
_DETAIL_IDENTITY_READ_ONLY_RETRIES = 12
_FRESH_FRAME_HISTORY_LIMIT = 512
_MAX_TRANSIENT_NAVIGATION_RECOVERIES = 3
_UNSAFE_STAGE_MANAGER_GEOMETRY = "detected Stage Manager game-window geometry is unsafe"
# Faster initial read-only sampling on the calibrated iPad.  These never
# reduce the number of required distinct proof frames: an unsettled capture
# simply falls through to the existing bounded retry loops.
_DETAIL_IDENTITY_FAST_READ_DELAY_SECONDS = 0.8
_MEASUREMENT_FAST_READ_DELAY_SECONDS = 0.9
_CLOSE_APPRAISAL_FAST_READ_DELAY_SECONDS = 1.0


def _snapshot_digest(snapshot: Snapshot) -> str:
    if not snapshot.image:
        raise PolicyViolation("截图缺失，无法验证帧新鲜度")
    return hashlib.sha256(base64.b64decode(snapshot.image)).hexdigest()


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


def _detail_name_key(result: NameRegionResult) -> tuple[str, str]:
    if result.is_default and result.species:
        return "default", result.species
    return "custom", ""


def _confirm_fresh_detail_identity(
    proxy: SafeProxy,
    snapshot: Snapshot,
    *,
    seed_samples: tuple[Snapshot, ...] = (),
) -> tuple[Snapshot, NameRegionResult] | None:
    """Require three distinct, never-before-used detail frames.

    ios-mcp can replay an old but visually valid screenshot after the real
    device has already navigated.  Pixel hashes make those cached frames
    ineligible, while the local name reader ties the later appraisal back to
    the same default species seen before any appraisal controls are opened.
    """

    blocked = set(_frame_history(proxy))

    def confirm_candidates(
        candidates: tuple[Snapshot, ...],
        *,
        default_only: bool,
    ) -> tuple[Snapshot, NameRegionResult, list[str]] | None:
        samples: dict[
            tuple[str, str], list[tuple[Snapshot, NameRegionResult, str]]
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
            if default_only and (not result.is_default or not result.species):
                # Reusing post-swipe evidence is an optimization only for a
                # full, exact default name.  Custom/ambiguous OCR always
                # performs the historical fresh detail reads before skipping.
                return None
            key = _detail_name_key(result)
            bucket = samples.setdefault(key, [])
            if digest in {item[2] for item in bucket}:
                continue
            bucket.append((candidate, result, digest))
            if len(bucket) >= 3:
                result_snapshot, result, _digest = bucket[-1]
                return result_snapshot, result, [item[2] for item in bucket]
        return None

    # A successful swipe already required three different pixel frames for
    # the changed detail.  Re-read their default-name OCR before using them;
    # this preserves the existing three-frame and exact-name gate while
    # avoiding two immediately redundant capture round trips.  Tie the seed
    # to the actual next snapshot so stale evidence cannot leak across cards.
    if (
        len(seed_samples) == 3
        and seed_samples[-1].image == snapshot.image
    ):
        seeded = confirm_candidates(seed_samples, default_only=True)
        if seeded is not None:
            result_snapshot, result, digests = seeded
            _remember_fresh_frames(proxy, digests)
            emit(
                "status",
                message=(
                    "翻页后的三张新鲜身份帧已再次确认同一默认名称："
                    f"{_display_name(result)}。"
                ),
            )
            return result_snapshot, result

    samples: dict[tuple[str, str], list[tuple[Snapshot, NameRegionResult, str]]] = {}
    candidates = [snapshot]
    for attempt in range(_DETAIL_IDENTITY_READ_ONLY_RETRIES):
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
        bucket = samples.setdefault(key, [])
        if digest in {item[2] for item in bucket}:
            continue
        bucket.append((candidate, result, digest))
        if len(bucket) >= 3:
            digests = [item[2] for item in bucket]
            _remember_fresh_frames(proxy, digests)
            emit(
                "status",
                message=(
                    "详情身份已由三张不同且未复用的截图确认："
                    f"{_display_name(result)}。"
                ),
            )
            return candidate, result
    return None


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
    """Choose direct-detail or legacy entry automatically and without taps."""

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


def _restore_direct_detail_after_interrupted_appraisal(
    proxy: SafeProxy, snapshot: Snapshot
) -> Snapshot:
    """Close only a proven appraisal overlay before a direct-detail resume."""

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

    try:
        base._validate_expected("DETAIL", snapshot)
    except (PolicyViolation, ValueError) as exc:
        raise PolicyViolation(
            "请先手动打开要处理的第一只宝可梦详情页；当前页面未执行精灵球、"
            "宝可梦盒或重启游戏操作"
        ) from exc
    return snapshot


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


def _resume_verified_unsubmitted_rename(
    proxy: SafeProxy, snapshot: Snapshot, settings: Settings
) -> Snapshot:
    """Commit a restart-interrupted edit only when both durable proofs agree.

    A newly spawned worker has no in-memory pending-name state.  It may recover
    exactly one edit only if the journal's last uncommitted input and the live
    accessibility text field agree character-for-character.  Otherwise it
    leaves the dialog untouched: unknown manual text is never submitted.
    """

    if base.local_page_state(snapshot) != "RENAME_DIALOG":
        return snapshot
    candidate = _last_unsubmitted_journal_nickname(settings)
    if not candidate:
        default_name = _proven_default_name_in_rename_dialog(snapshot)
        if default_name is None:
            raise PolicyViolation("检测到未留档且无法证明默认名称的改名弹窗；不会猜测提交或取消")
        emit(
            "status",
            message=(
                f"检测到先前仅打开、尚未输入的默认名称弹窗：{default_name}；"
                "自动取消后从同一详情页重试。"
            ),
        )
        try:
            _cancel_unverified_input(proxy, default_name)
        except RenameFieldVerificationUnavailable as cancelled:
            return cancelled.snapshot
        raise PolicyViolation("默认名称弹窗取消流程未返回已验证详情页")
    actual = _verified_entered_value(proxy)
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
        raise PolicyViolation(
            "遗留改名字段与最后一次留档目标不一致；不会点击 OK 或取消"
        )
    if not snapshot.image:
        raise PolicyViolation("遗留改名弹窗缺少截图；不会点击 OK")

    # A fresh dialog proof is required in addition to the exact AX field.
    # The helper performs reads only and provides the same evidence gate used
    # by ordinary per-Pokémon submission.
    _dialog_evidence_after_keyboard_dismiss(proxy)
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
    detail = _submit_with_one_verified_retry(proxy, nickname=candidate)
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
        return _require_current_detail(snapshot)
    except PolicyViolation:
        if (
            not _persistent_capture_wait_enabled()
            or not _has_ipad_task_switcher_overlay(snapshot)
        ):
            raise
    emit(
        "status",
        message=(
            "检测到 iPad 多任务切换层覆盖 Pokémon GO 详情；后台保持运行并只读等待详情恢复，"
            "不会选择其他 App、进入宝可梦盒或重新打开游戏。"
        ),
    )
    while True:
        candidate = base._next_snapshot(proxy, 3.0)
        try:
            return _require_current_detail(candidate)
        except PolicyViolation:
            if _has_ipad_task_switcher_overlay(candidate):
                continue
            raise


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

    try:
        return _require_current_detail(snapshot)
    except PolicyViolation:
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
            continue


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

    screen_snapshot(proxy)
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
    screen_snapshot(proxy)
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

    refreshed = screen_snapshot(proxy)
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
    for token in appraisal_name.evidence:
        candidate = token.strip()
        if not candidate or candidate == expected or HP_LINE.fullmatch(candidate):
            continue
        if NUMBER_TOKEN.fullmatch(candidate):
            return False
        extras.append(candidate)
    return bool(extras) and all(
        len(token) < len(expected) and expected.endswith(token) for token in extras
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
        # Use the resilient transition rather than validating the first
        # post-tap frame.  On a real iPad the menu animation can take longer
        # than that frame, even though the original page remains proven.  The
        # transition waits up to 12 seconds and permits one retry only when
        # the source page is still independently verified.
        snapshot, state = v14._transition(proxy, snapshot, state)
    base._validate_expected("DETAIL", snapshot)
    return snapshot


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
    snapshot = (
        _wait_for_verified_next_detail(
            proxy, snapshot, seed_samples=identity_seed_samples
        )
        if current_detail_only
        else _ensure_plain_detail(proxy, snapshot)
    )
    detail_identity = _confirm_fresh_detail_identity(
        proxy, snapshot, seed_samples=identity_seed_samples
    )
    if detail_identity is None:
        emit(
            "status",
            message=(
                f"第 {index} 只未取得三张新鲜详情身份帧；"
                "可能是 MCP 回放旧截图，已保留原名并继续下一只。"
            ),
        )
        return snapshot, "unreadable"
    snapshot, detail_name = detail_identity
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
    if not _appraisal_identity_matches_current_detail(name, detail_name):
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
    if not name.is_default:
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
    try:
        detail = _commit_after_dismissing_keyboard(
            proxy,
            current_name=detail_name.species,
            species=detail_name.species,
            nickname=nickname,
        )
    except RenameFieldVerificationUnavailable as exc:
        base._validate_expected("DETAIL", exc.snapshot)
        emit(
            "status",
            message=(
                f"第 {index} 只输入字段暂时不可核验；"
                "已取消未提交编辑、保留原名并继续下一只。"
            ),
        )
        return exc.snapshot, "unreadable"
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
        v14._ORIGINAL_NAVIGATE = _navigate_with_read_only_measurement_retry
        # Appraisal navigation uses this hook.  It must never turn a transient
        # black MCP frame into a Home/launch/restart sequence.
        v14._wait_until_visible = _wait_without_game_restart
        try:
            client = ResilientStreamableHTTPClient(settings, timeout=120.0)
            device = base._device_details(client.call_tool("get_device_info", {}))
            if str(device.get("machine", "")) != "iPad14,6":
                raise PolicyViolation("批量横屏流程目前只支持已校准的 iPad14,6")
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

            def device_aware_next_snapshot(
                active_proxy: SafeProxy, delay: float = 2.5
            ) -> Snapshot:
                fresh = previous_next_snapshot(active_proxy, delay)
                return wait_for_unlocked_snapshot(active_proxy, fresh)

            # Every downstream module resolves base._next_snapshot at call
            # time.  Installing one device-state gate here makes lock/off
            # recovery consistent during appraisal, rename verification and
            # next-Pokémon swipes, including non-black lock-screen captures.
            base._next_snapshot = device_aware_next_snapshot
            snapshot = wait_for_capture_channel(
                proxy, screen_snapshot(proxy), allow_game_restart=False
            )
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
            current_detail_only = _current_detail_only(snapshot)
            if current_detail_only:
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
            counts = {"renamed": 0, "skipped": 0, "scanned": 0, "unreadable": 0}
            pause = _pause_file(settings)
            index = 1
            transient_recoveries = 0
            identity_seed_samples: tuple[Snapshot, ...] = ()

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
                    detail, outcome = _process_one(
                        proxy,
                        snapshot,
                        mode=mode,
                        index=index,
                        current_detail_only=current_detail_only,
                        identity_seed_samples=seed_samples,
                    )
                    detail, fingerprint = wait_for_stable_detail_fingerprint(
                        proxy, detail
                    )
                    _remember_fresh_frames(proxy, [_snapshot_digest(detail)])
                except (PolicyViolation, ValueError) as exc:
                    if (
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
                try:
                    next_detail: VerifiedNextDetail = swipe_to_verified_next(
                        proxy, detail, before=fingerprint
                    )
                    snapshot = next_detail.snapshot
                    identity_seed_samples = next_detail.samples
                except VerifiedEndOfStorage:
                    emit(
                        "status",
                        message=(
                            "纯详情页连续四次翻页后仍为同一稳定身份；"
                            "已验证当前盒子末尾。"
                        ),
                    )
                    break
                except NoNextPokemon as exc:
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
                    f"批量完成：处理 {sum(counts.values())} 只，"
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
