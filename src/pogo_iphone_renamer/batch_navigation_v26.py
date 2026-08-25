from __future__ import annotations

import base64
import hashlib
import os
import re
from dataclasses import dataclass

from . import ipad_landscape_agent as base
from .appraisal_agent import Snapshot
from .landscape_cv_calibrated import measure_ipad14_6_appraisal
from .local_ocr import ocr_mcp_screenshot
from .local_ocr_v3 import analyze_name_region
from .policy import PolicyViolation
from .server import SafeProxy
from .species_db import traditional_chinese_species


CP_TOKEN = re.compile(r"^CP\s*\d+$", re.IGNORECASE)
HP_TOKEN = re.compile(r"^\d+\s*/\s*\d+\s*HP$", re.IGNORECASE)
WEIGHT_TOKEN = re.compile(r"^\d+(?:[.,]\d+)?\s*kg$", re.IGNORECASE)
HEIGHT_TOKEN = re.compile(r"^\d+(?:[.,]\d+)?\s*m$", re.IGNORECASE)


class NoNextPokemon(PolicyViolation):
    """A bounded verified swipe could not reach a different detail page."""


class VerifiedEndOfStorage(NoNextPokemon):
    """Four swipes remained on the same verified plain-detail identity."""


@dataclass(frozen=True)
class VerifiedNextDetail:
    """A next-detail identity proven by three fresh post-swipe frames.

    ``samples`` are deliberately returned rather than placed in a mutable
    proxy cache.  The batch worker will independently re-check every one for
    a default species name before it may reuse them as rename authorization.
    """

    snapshot: Snapshot
    fingerprint: "DetailFingerprint"
    samples: tuple[Snapshot, ...]


MAX_VERIFIED_SWIPE_ATTEMPTS = 4
OBSERVATIONS_PER_SWIPE = 8
OBSERVATION_DELAY_SECONDS = 0.8
FAST_OBSERVATION_DELAY_SECONDS = 0.6
CHANGED_IDENTITY_CONFIRMATIONS = 3
BASELINE_OBSERVATIONS = 5
BASELINE_CONFIRMATIONS = 2


def _persist_post_swipe_wait_enabled() -> bool:
    """Keep the direct-detail batch alive while visual identity briefly drops."""

    return os.getenv("POGO_PERSIST_CAPTURE_WAIT", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _snapshot_digest(snapshot: Snapshot) -> str:
    if not snapshot.image:
        return ""
    try:
        return hashlib.sha256(base64.b64decode(snapshot.image)).hexdigest()
    except Exception:
        return ""


def _blocked_frame_hashes(proxy: SafeProxy) -> set[str]:
    history = getattr(proxy, "_pogo_verified_frame_history", None)
    return set(history) if isinstance(history, list) else set()


@dataclass(frozen=True)
class DetailFingerprint:
    name_tokens: tuple[str, ...]
    cp: str
    hp: str
    weight: str
    height: str

    def stable_fields(self) -> tuple[str, ...]:
        return ("|".join(self.name_tokens), self.cp, self.hp, self.weight, self.height)


def detail_fingerprint(
    snapshot: Snapshot, *, require_name: bool = True
) -> DetailFingerprint:
    if not snapshot.image:
        raise PolicyViolation("详情页截图缺失，无法验证翻页身份")
    # Do not route this identity check through local_page_state().  That broad
    # page classifier can briefly miss a perfectly valid detail page when OCR
    # drops one of the HP/kg tokens.  The identity fields below are a stronger
    # proof of a detail page and are exactly what navigation needs.
    try:
        measure_ipad14_6_appraisal(snapshot.image, base.ORIENTATION)
    except ValueError:
        pass
    else:
        raise PolicyViolation("鉴定条仍可见；不能在覆盖层上翻页")
    lowered = snapshot.text.casefold().replace("\\/", "/")
    if "清除文本" in lowered and ("完成" in lowered or "取消" in lowered):
        raise PolicyViolation("改名窗口仍可见；不能在输入层上翻页")
    name = analyze_name_region(snapshot.image, base.ORIENTATION)
    name_tokens = tuple(
        _normalized(token)
        for token in name.evidence
        if token.strip() and not HP_TOKEN.fullmatch(token.strip())
    )
    cp = hp = weight = height = ""
    full_screen_lines = ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
    for line in full_screen_lines:
        if line.confidence < 0.72:
            continue
        text = line.text.strip()
        normalized = _normalized(text)
        if not cp and CP_TOKEN.fullmatch(text.replace(" ", "")):
            cp = normalized
        elif not hp and HP_TOKEN.fullmatch(text):
            hp = normalized
        elif not weight and WEIGHT_TOKEN.fullmatch(text):
            weight = normalized
        elif not height and HEIGHT_TOKEN.fullmatch(text):
            height = normalized
    if not name_tokens:
        # The narrow calibrated crop can temporarily land on the HP row while
        # a detail transition is settling.  A full-frame species read is
        # sufficient for *identity-only* navigation: it never authorizes a
        # rename, but prevents a harmless, recoverable OCR miss from ending
        # the whole batch before it can move to the next card.
        known_species = {
            _normalized(line.text)
            for line in full_screen_lines
            if line.confidence >= 0.85
            and line.text in traditional_chinese_species()
        }
        if len(known_species) == 1:
            name_tokens = tuple(known_species)
    fingerprint = DetailFingerprint(name_tokens, cp, hp, weight, height)
    numeric_fields = (cp, hp, weight, height)
    if (require_name and not name_tokens) or not any(numeric_fields):
        raise PolicyViolation("详情页稳定身份字段不足；不会自动翻页")
    return fingerprint


def fingerprints_differ(before: DetailFingerprint, after: DetailFingerprint) -> bool:
    pairs = [
        (old, new)
        for old, new in zip(before.stable_fields(), after.stable_fields())
        if old and new
    ]
    return any(old != new for old, new in pairs)


def wait_for_stable_detail_fingerprint(
    proxy: SafeProxy,
    snapshot: Snapshot,
    *,
    verified_navigation_fallback: DetailFingerprint | None = None,
) -> tuple[Snapshot, DetailFingerprint]:
    """Read only until a known detail regains enough fields for navigation.

    This is deliberately narrower than page recovery: it applies only after
    the batch has already proven a DETAIL page and a later frame temporarily
    loses its CP/HP/size text.  In persistent direct-detail mode we keep that
    same page untouched and wait; an overlay or any other unsafe page still
    raises immediately.

    A just-verified rename or three-frame-confirmed custom nickname can make
    the title unavailable to OCR.  In that case, the caller may pass a
    name-free fingerprint made from the same fully verified detail frame. Its
    immutable CP/HP/weight/height fields are enough to prove the following
    swipe reached a different detail, while the fallback never authorizes a
    rename itself.
    """

    try:
        return snapshot, detail_fingerprint(snapshot)
    except PolicyViolation as initial_error:
        if (
            verified_navigation_fallback is not None
            and "详情页稳定身份字段不足" in str(initial_error)
            and any(
                (
                    verified_navigation_fallback.cp,
                    verified_navigation_fallback.hp,
                    verified_navigation_fallback.weight,
                    verified_navigation_fallback.height,
                )
            )
        ):
            base.emit(
                "status",
                message=(
                    "当前详情已被安全确认，但标题无法由 OCR 读取；"
                    "本次仅使用已确认的 CP/HP/体型字段验证下一次翻页。"
                ),
            )
            return snapshot, verified_navigation_fallback
        if (
            not _persist_post_swipe_wait_enabled()
            or "详情页稳定身份字段不足" not in str(initial_error)
        ):
            raise
    base.emit(
        "status",
        message=(
            "已验证的详情页身份字段暂时不完整；后台保持运行并只读等待恢复，"
            "不会滑动、结束任务或重新打开游戏。"
        ),
    )
    while True:
        candidate = base._next_snapshot(proxy, 3.0)
        try:
            return candidate, detail_fingerprint(candidate)
        except PolicyViolation as error:
            if "详情页稳定身份字段不足" in str(error):
                continue
            raise


def _swipe_next_once(proxy: SafeProxy, *, direction: str = "left") -> None:
    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 未返回触控空间")
    if direction not in {"left", "right"}:
        raise ValueError(f"unsupported swipe direction: {direction}")
    from_ratio, to_ratio = (0.78, 0.22) if direction == "left" else (0.22, 0.78)
    from_x, from_y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        from_ratio,
        0.50,
        geometry=base.current_stage_geometry(proxy),
    )
    to_x, to_y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        to_ratio,
        0.50,
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
            "_intent": f"navigate {direction} to next Pokemon detail",
            "_expected_after": "DETAIL for a different Pokemon",
        },
    )


def _stable_baseline(
    proxy: SafeProxy,
    detail: Snapshot,
    initial: DetailFingerprint,
) -> tuple[Snapshot, DetailFingerprint]:
    """Read a modal pre-swipe identity so one OCR variant is not the baseline."""

    counts: dict[DetailFingerprint, int] = {initial: 1}
    snapshots: dict[DetailFingerprint, Snapshot] = {initial: detail}
    for _ in range(BASELINE_OBSERVATIONS - 1):
        candidate = base._next_snapshot(proxy, FAST_OBSERVATION_DELAY_SECONDS)
        try:
            fingerprint = detail_fingerprint(candidate)
        except PolicyViolation:
            continue
        counts[fingerprint] = counts.get(fingerprint, 0) + 1
        snapshots[fingerprint] = candidate
        if counts[fingerprint] >= BASELINE_CONFIRMATIONS:
            return snapshots[fingerprint], fingerprint
    if _persist_post_swipe_wait_enabled():
        base.emit(
            "status",
            message=(
                "翻页前详情身份帧暂未凑齐两帧一致；后台保持运行并只读等待，"
                "不会滑动、结束任务或重新打开游戏。"
            ),
        )
        while True:
            candidate = base._next_snapshot(proxy, 3.0)
            try:
                fingerprint = detail_fingerprint(candidate)
            except PolicyViolation:
                continue
            counts[fingerprint] = counts.get(fingerprint, 0) + 1
            snapshots[fingerprint] = candidate
            if counts[fingerprint] >= BASELINE_CONFIRMATIONS:
                return snapshots[fingerprint], fingerprint
    raise NoNextPokemon("翻页前无法取得两帧一致的详情身份")


def _observe_after_swipe(
    proxy: SafeProxy,
    previous: DetailFingerprint,
) -> tuple[Snapshot, DetailFingerprint, bool, tuple[Snapshot, ...]] | None:
    """Observe a bounded settling window after a swipe.

    A changed identity wins only after the same complete fingerprint appears
    in three independent screenshots.  A single transition-frame OCR error
    must never turn the current Pokemon into a false "next" Pokemon.  Otherwise
    the most recent strongly verified copy of the previous detail is returned,
    allowing a swallowed swipe to be retried from a known-safe page.  Frames
    that are transitioning, temporarily unclassified, or have incomplete OCR
    are observation failures, not evidence that navigation is impossible.
    """

    same: tuple[Snapshot, DetailFingerprint, bool, tuple[Snapshot, ...]] | None = None
    blocked = _blocked_frame_hashes(proxy)
    changed_samples: dict[DetailFingerprint, list[tuple[Snapshot, str]]] = {}
    for observation_index in range(OBSERVATIONS_PER_SWIPE):
        # Let the gesture settle for the first capture, then collect the
        # remaining independent identity proofs sooner.  All eight reads and
        # the three-matching-fingerprint threshold remain intact.
        delay = (
            OBSERVATION_DELAY_SECONDS
            if observation_index == 0
            else FAST_OBSERVATION_DELAY_SECONDS
        )
        snapshot = base._next_snapshot(proxy, delay)
        digest = _snapshot_digest(snapshot)
        if not digest or digest in blocked:
            # This exact pixel frame belonged to the Pokemon before the swipe.
            # A missing digest cannot prove that independently captured pixels
            # changed either.  In both cases this frame must never authorize a
            # new identity or a subsequent rename.
            continue
        try:
            # This proves navigation only.  The subsequent per-Pokémon
            # identity gate still needs three fresh name reads before it can
            # appraise or rename anything, so an unreadable custom nickname
            # must not turn a valid post-swipe detail into an endless wait.
            current = detail_fingerprint(snapshot, require_name=False)
        except PolicyViolation:
            continue
        if fingerprints_differ(previous, current):
            samples = changed_samples.setdefault(current, [])
            if digest in {sample_digest for _sample, sample_digest in samples}:
                # Replayed post-swipe pixels are not three independent proofs
                # of a new detail page.  Do not turn one cached frame into a
                # reusable three-frame identity.
                continue
            samples.append((snapshot, digest))
            if len(samples) >= CHANGED_IDENTITY_CONFIRMATIONS:
                return (
                    snapshot,
                    current,
                    True,
                    tuple(sample for sample, _digest in samples),
                )
            continue
        same = snapshot, current, False, ()
    return same


def _wait_for_post_swipe_identity(
    proxy: SafeProxy,
    previous: DetailFingerprint,
) -> tuple[Snapshot, DetailFingerprint, bool, tuple[Snapshot, ...]] | None:
    """Read only until a post-swipe detail can again be identified.

    A game detail can remain visually present while OCR temporarily supplies
    no usable text.  With the headless persistence switch enabled, do not
    turn that read-side outage into a batch failure or issue another swipe.
    The first re-proven old detail permits the normal bounded swipe sequence
    to continue; a different detail still requires three distinct frames.
    """

    if not _persist_post_swipe_wait_enabled():
        return None
    emit_message = getattr(base, "emit", None)
    # batch_navigation deliberately has no UI dependency.  The caller owns
    # user-facing events; this marker is retained only for test-free local
    # tracing when the base agent exposes its normal emitter.
    if callable(emit_message):
        emit_message(
            "status",
            message=(
                "翻页后身份字段暂不可读；后台保持运行并只读等待详情页恢复，"
                "不会重复滑动或结束任务。"
            ),
        )
    blocked = _blocked_frame_hashes(proxy)
    changed_samples: dict[DetailFingerprint, list[tuple[Snapshot, str]]] = {}
    while True:
        snapshot = base._next_snapshot(proxy, 3.0)
        digest = _snapshot_digest(snapshot)
        if not digest or digest in blocked:
            continue
        try:
            # See _observe_after_swipe: numeric identity may carry a newly
            # reached detail through a temporary title-OCR gap, but it never
            # authorizes a rename on its own.
            current = detail_fingerprint(snapshot, require_name=False)
        except PolicyViolation:
            continue
        if not fingerprints_differ(previous, current):
            return snapshot, current, False, ()
        samples = changed_samples.setdefault(current, [])
        if digest in {sample_digest for _sample, sample_digest in samples}:
            continue
        samples.append((snapshot, digest))
        if len(samples) >= CHANGED_IDENTITY_CONFIRMATIONS:
            return snapshot, current, True, tuple(sample for sample, _digest in samples)


def swipe_to_verified_next(
    proxy: SafeProxy,
    detail: Snapshot,
    *,
    before: DetailFingerprint | None = None,
) -> VerifiedNextDetail:
    previous = before or detail_fingerprint(detail)
    if previous.name_tokens:
        detail, previous = _stable_baseline(proxy, detail, previous)
    else:
        # A name-free fingerprint is created only immediately after a rename
        # has been committed and character-for-character verified.  The
        # nickname may have truncated the species title beyond OCR recovery,
        # but its pre-rename CP/HP/size fields still identify the current
        # detail for the purpose of proving a different post-swipe detail.
        # Re-running the name-dependent baseline here would otherwise wait
        # forever on a known-good, short nickname before issuing no swipe.
        base.emit(
            "status",
            message=(
                "已核验短昵称的改名后详情；直接使用改名前不可变字段验证下一次翻页。"
            ),
        )
    unchanged_confirmations = 0
    established_direction = getattr(proxy, "_batch_swipe_direction", None)
    if established_direction in {"left", "right"}:
        directions = [established_direction] * MAX_VERIFIED_SWIPE_ATTEMPTS
    else:
        # Storage sort order can place the first visible card at either end.
        # Probe both directions only until the direction is established.  Once
        # established, never reverse at the far end and accidentally walk back
        # through already processed Pokemon.
        directions = ["left", "left", "right", "right"]
    for direction in directions:
        _swipe_next_once(proxy, direction=direction)
        observed = _observe_after_swipe(proxy, previous)
        if observed is None:
            observed = _wait_for_post_swipe_identity(proxy, previous)
        if observed is None:
            raise NoNextPokemon(
                "横向翻页后连续只读采样仍无法确认安全详情页"
            )
        snapshot, current, changed, samples = observed
        if changed:
            try:
                setattr(proxy, "_batch_swipe_direction", direction)
            except AttributeError:
                pass
            return VerifiedNextDetail(snapshot, current, samples)
        unchanged_confirmations += 1
        detail = snapshot
    if unchanged_confirmations == MAX_VERIFIED_SWIPE_ATTEMPTS:
        raise VerifiedEndOfStorage(
            "已在纯详情页连续四次翻页，稳定身份均未变化"
        )
    raise NoNextPokemon(
        "横向翻页后未验证到不同宝可梦；"
        "无法证明已到盒子末尾"
    )
