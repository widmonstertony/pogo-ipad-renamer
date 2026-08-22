from __future__ import annotations

import argparse
import base64
import hashlib
import os
import time
from pathlib import Path

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v14 as v14
from .appraisal_agent import Snapshot, screen_snapshot
from .batch_navigation_v26 import (
    DetailFingerprint,
    NoNextPokemon,
    VerifiedEndOfStorage,
    detail_fingerprint,
    fingerprints_differ,
    swipe_to_verified_next,
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
    _commit_after_dismissing_keyboard,
)
from .ipad_landscape_agent_v24 import (
    AppraisalMeasurementUnavailable,
    _navigate_with_read_only_measurement_retry,
)
from .ipad_landscape_agent_v25 import _navigate_with_complete_stale_recovery
from .landscape_cv_v6 import measure_ipad14_6_appraisal_v6
from .local_ocr_v3 import NameRegionResult, analyze_name_region
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
) -> tuple[Snapshot, NameRegionResult] | None:
    """Require three distinct, never-before-used detail frames.

    ios-mcp can replay an old but visually valid screenshot after the real
    device has already navigated.  Pixel hashes make those cached frames
    ineligible, while the local name reader ties the later appraisal back to
    the same default species seen before any appraisal controls are opened.
    """

    blocked = set(_frame_history(proxy))
    samples: dict[tuple[str, str], list[tuple[Snapshot, NameRegionResult, str]]] = {}
    candidates = [snapshot]
    for attempt in range(_DETAIL_IDENTITY_READ_ONLY_RETRIES):
        if attempt:
            candidates.append(base._next_snapshot(proxy, 1.0))
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
                proxy, 1.5 if observation == 0 else 0.8
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


def _ensure_plain_detail(proxy: SafeProxy, snapshot: Snapshot) -> Snapshot:
    """Navigate only as far as a plain detail page, never into appraisal."""

    state = v14.robust_page_state(snapshot)
    if state == "APPRAISAL_BARS":
        return _close_appraisal(proxy)
    if state == "DETAIL":
        base._validate_expected("DETAIL", snapshot)
        return snapshot
    order = ["MAP", "MAIN_MENU", "INVENTORY"]
    if state not in order:
        raise PolicyViolation(f"批量详情入口不支持当前页面：{state}")
    for current in order[order.index(state) :]:
        snapshot = base._ensure_stage_geometry_for_state(
            proxy, snapshot, current, state_reader=v14.robust_page_state
        )
        base._tap(proxy, current)
        snapshot = base._next_snapshot(proxy)
        expected = base.ANCHORS[current][3]
        base._validate_expected(expected, snapshot)
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
        retry = base._next_snapshot(proxy, 1.25)
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
) -> tuple[Snapshot, str]:
    snapshot = _ensure_plain_detail(proxy, snapshot)
    detail_identity = _confirm_fresh_detail_identity(proxy, snapshot)
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
        appraisal, measurement = _navigate_with_complete_stale_recovery(proxy, snapshot)
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
    if (
        not name.is_default
        or not name.species
        or name.species != detail_name.species
    ):
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
        _commit_after_dismissing_keyboard(
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
    detail = screen_snapshot(proxy)
    base._validate_expected("DETAIL", detail)
    emit("renamed", nickname=nickname)
    return detail, "renamed"


def run(mode: str, settings: Settings) -> int:
    with DeviceRunLock(settings.journal_path.parent / "iphone-mcp.lock"):
        previous_original = v14._ORIGINAL_NAVIGATE
        previous_wait_until_visible = v14._wait_until_visible
        previous_next_snapshot = base._next_snapshot
        v14._ORIGINAL_NAVIGATE = _navigate_with_read_only_measurement_retry
        v14._wait_until_visible = wait_for_capture_channel
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
            snapshot = wait_for_capture_channel(proxy, screen_snapshot(proxy))
            snapshot = _ensure_game_foreground(proxy, snapshot)
            counts = {"renamed": 0, "skipped": 0, "scanned": 0, "unreadable": 0}
            pause = _pause_file(settings)
            index = 1

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
                detail, outcome = _process_one(proxy, snapshot, mode=mode, index=index)
                counts[outcome] += 1
                _remember_fresh_frames(proxy, [_snapshot_digest(detail)])
                fingerprint = detail_fingerprint(detail)
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
                    snapshot, next_fingerprint = swipe_to_verified_next(
                        proxy, detail, before=fingerprint
                    )
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
                    raise PolicyViolation(
                        f"{exc}；为避免误报完成，本轮安全停止"
                    ) from exc
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
