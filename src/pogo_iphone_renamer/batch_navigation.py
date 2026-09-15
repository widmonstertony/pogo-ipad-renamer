from __future__ import annotations

import base64
import hashlib
import os
import re
import time
from dataclasses import dataclass

from . import device_controller as base
from .appraisal_agent import Snapshot
from .appraisal_calibration import measure_ipad14_6_appraisal
from .landscape_cv import rotate_mcp_image_upright
from .local_ocr import ocr_mcp_screenshot
from .name_recognition import analyze_name_region
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


class DetailExitedToOverview(NoNextPokemon):
    """A verified detail swipe visibly returned to a game overview page."""

    def __init__(self, snapshot: Snapshot, state: str) -> None:
        super().__init__(f"横向翻页后离开详情页：{state}")
        self.snapshot = snapshot
        self.state = state


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
# A screenshot stream may briefly produce featureless/old frames, but an
# unlimited read loop makes the desktop indistinguishable from a frozen task.
# The caller retries a bounded observation cycle with a fresh read session;
# it never sends a blind extra gesture while a cycle is unresolved.
MAX_PERSISTENT_READ_ONLY_ATTEMPTS = 12
# All lanes are inside the *upper Pokémon artwork*, not the white information
# sheet.  On the current iPad7,2 Stage Manager capture, the previous 0.40
# lane lands exactly on the sheet's top edge.  Pokémon GO interprets that
# drag as dismissing the detail rather than its left/right pager.  These lanes
# deliberately stay clear of the top ellipsis and star/camera controls too.
SAFE_PAGER_Y_RATIOS = (0.28, 0.25, 0.31, 0.34)
# A page swipe is a short, ordinary finger gesture.  The longer 650 ms motion
# was reliably accepted by MCP but was consumed by the sheet/Stage Manager
# gesture recognizer before Pokémon GO could page.
PAGER_SWIPE_DURATION_MS = 320
PAGER_SWIPE_STEPS = 20
# Pokémon GO's detail pager places the following storage item to the visual
# right of the current card. A finger swipe from visual right to left reveals
# it. Merely seeing a different identity cannot establish direction: the
# opposite gesture also changes identity, but walks to the previous item.
NEXT_PAGER_DIRECTION = "left"


def _emit_read_only_wait(
    *,
    stage: str,
    reason: str,
    attempt: int,
    started_at: float,
    next_action: str,
) -> None:
    """Publish a live explanation for a deliberately non-interactive wait.

    Screenshot/OCR recovery can take longer than the visible animation.  The
    worker must not turn that into a blind extra swipe, but the desktop must
    also not look frozen while it is collecting proof.  This event is local
    telemetry only; it never adds an MCP request or changes the iPad.
    """

    base.emit(
        "waiting",
        stage=stage,
        reason=reason,
        attempt=attempt,
        elapsed_seconds=max(0, int(time.monotonic() - started_at)),
        next_action=next_action,
        user_action="无需操作；此时没有足够证据安全点击，后台只读等待。",
    )


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


def _detail_card_visual_signature(snapshot: Snapshot) -> str:
    """Return a compact, non-authorizing signature of the detail text card.

    Pokémon models and weather/shiny particles animate continuously, which
    makes a whole-screenshot digest unsuitable for pager confirmation. The
    title/HP/size card is stable by contrast. This signature is used only to
    tell whether the card visibly changed after a swipe; a later, independent
    three-frame title OCR gate remains mandatory before appraisal or rename.
    """

    if not snapshot.image:
        return ""
    try:
        image = rotate_mcp_image_upright(snapshot.image, base.ORIENTATION)
        width, height = image.size
        # Exclude the moving model, background leaves and action buttons.
        card = image.crop(
            (
                round(width * 0.20),
                round(height * 0.48),
                round(width * 0.80),
                round(height * 0.69),
            )
        ).convert("L").resize((24, 12))
        # Thirty-two luminance levels over deliberately coarse cells retain
        # the title/stat layout while tolerating JPEG compression, subpixel
        # antialiasing and the animated particle layer outside this crop.
        values = bytes(pixel >> 5 for pixel in card.getdata())
    except Exception:
        return ""
    return hashlib.sha256(values).hexdigest()


def _independent_read_key(snapshot: Snapshot, digest: str) -> str:
    """Distinguish actual repeated reads from test fixtures or reused data.

    iPadOS can encode a stable detail page into byte-identical JPEGs.  A
    different post-swipe digest still proves it is not the previously verified
    Pokémon; subsequent reads are independent because screen_snapshot issued
    a new MCP screenshot request, even when the pixels do not animate.
    Fixtures and manually reused snapshots have no capture id and remain
    deduplicated by pixel digest.
    """

    return f"capture:{snapshot.capture_id}" if snapshot.capture_id else f"pixel:{digest}"


def _blocked_frame_hashes(proxy: SafeProxy) -> set[str]:
    history = getattr(proxy, "_pogo_verified_frame_history", None)
    return set(history) if isinstance(history, list) else set()


def _overview_state(snapshot: Snapshot) -> str | None:
    """Recognize a *pixel-proven* game overview before entering a wait.

    On iPad7,2 the MCP accessibility tree can retain an INVENTORY/MAP label
    for one or more fresh screenshots after a detail pager swipe.  That text
    must never overrule a visible Pokémon detail card: it previously made the
    worker reopen an inventory card even when the raw detail pixels (and CP)
    had already changed.
    """

    try:
        state = base.local_page_state(snapshot)
    except Exception:
        return None
    if state not in {"MAP", "MAIN_MENU", "INVENTORY"}:
        return None
    # ``local_page_state`` is intentionally conservative and falls back to
    # MAP whenever local OCR is momentarily empty.  A detail-card visual
    # signature is stronger evidence than that fallback, but is used only to
    # suppress an erroneous exit classification; it never authorizes rename
    # or navigation by itself.
    if _looks_like_detail_visual(snapshot):
        return None
    return state


def _looks_like_detail_visual(snapshot: Snapshot) -> bool:
    """Conservatively identify the normal detail-card layout without OCR.

    Used only when OCR temporarily returns no detections after a swipe.  The
    next loop still needs ordinary page classification and a strict name
    proof before any appraisal or rename action.
    """

    if not snapshot.image:
        return False
    try:
        image = rotate_mcp_image_upright(snapshot.image, base.ORIENTATION)
        width, height = image.size
        card = image.crop((int(width * 0.15), int(height * 0.42), int(width * 0.85), int(height * 0.82)))
        sky = image.crop((int(width * 0.15), int(height * 0.05), int(width * 0.85), int(height * 0.34)))
        card_pixels = list(card.resize((36, 24)).getdata())
        sky_pixels = list(sky.resize((36, 18)).getdata())
    except Exception:
        return False
    white_ratio = sum(
        1 for red, green, blue in card_pixels if min(red, green, blue) >= 215
    ) / len(card_pixels)
    cool_sky_ratio = sum(
        1
        for red, green, blue in sky_pixels
        if (green >= red + 10 and green >= blue)
        or (blue >= red + 10 and blue >= green)
    ) / len(sky_pixels)
    # The card includes the Pokémon model and green stat bar, so its white
    # coverage is materially below a modal dialog.  Current Pokémon GO uses
    # a dark blue presentation background on this device; older captures
    # were green.  Require either cool background hue alongside the large
    # white detail sheet, which keeps the check limited to normal detail
    # layouts without depending on a seasonal background palette.
    return white_ratio >= 0.35 and cool_sky_ratio >= 0.20


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


def navigation_confirmation_key(
    fingerprint: DetailFingerprint,
) -> tuple[str, str, str, str] | tuple[()]:
    """Return the stable numeric pair used only to confirm a completed swipe.

    Title OCR is intentionally strict later, immediately before any appraisal
    or rename.  It is not stable enough to be a swipe-confirmation key on the
    Stage Manager iPad: the same detail can alternate between a title, a
    truncated nickname, and no title while CP/HP remain intact.  Requiring a
    pair of independently visible numeric detail fields prevents that harmless
    title variance from being mistaken for multiple Pokémon, while still
    requiring three fresh captures before navigation can continue.
    """

    ordered_pairs = (
        ("cp", fingerprint.cp, "hp", fingerprint.hp),
        ("cp", fingerprint.cp, "weight", fingerprint.weight),
        ("cp", fingerprint.cp, "height", fingerprint.height),
        ("hp", fingerprint.hp, "weight", fingerprint.weight),
        ("hp", fingerprint.hp, "height", fingerprint.height),
        ("weight", fingerprint.weight, "height", fingerprint.height),
    )
    for first_label, first_value, second_label, second_value in ordered_pairs:
        if first_value and second_value:
            return first_label, first_value, second_label, second_value
    return ()


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
    wait_started = time.monotonic()
    attempt = 0
    while attempt < MAX_PERSISTENT_READ_ONLY_ATTEMPTS:
        attempt += 1
        _emit_read_only_wait(
            stage="详情翻页前身份复核",
            reason="详情页的 CP/HP/体型字段暂时未被 OCR 完整读到。",
            attempt=attempt,
            started_at=wait_started,
            next_action="读取下一张详情截图，恢复足够字段后才翻页。",
        )
        candidate = base._next_snapshot(proxy, 3.0)
        try:
            return candidate, detail_fingerprint(candidate)
        except PolicyViolation as error:
            if "详情页稳定身份字段不足" in str(error):
                continue
            raise
    raise NoNextPokemon(
        "详情身份字段连续 12 次读取仍不完整；将由批量读取会话恢复逻辑重新验证"
    )


def _swipe_next_once(
    proxy: SafeProxy, *, direction: str = "left", swipe_y_ratio: float = 0.28
) -> None:
    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 未返回触控空间")
    if direction not in {"left", "right"}:
        raise ValueError(f"unsupported swipe direction: {direction}")
    if not 0.22 <= swipe_y_ratio <= 0.34:
        raise ValueError(f"unsafe Pokemon-detail pager lane: {swipe_y_ratio}")
    from_ratio, to_ratio = (0.78, 0.22) if direction == "left" else (0.22, 0.78)
    # Keep the lateral gesture wholly in the Pokémon artwork.  The lower
    # title-row lane is swallowed by the detail sheet; the image area is the
    # only region that Pokémon GO exposes to its card pager on this build.
    # Left/right refer to the upright game image, not the raw MCP axes. Use
    # the same calibrated transform as every other control; the iPad7,2
    # digitizer-normalized profile maps visual left-to-right to increasing
    # touch Y. Do not apply another rotation or infer direction from touch X.
    from_x, from_y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        from_ratio,
        swipe_y_ratio,
        geometry=base.current_stage_geometry(proxy),
    )
    to_x, to_y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        to_ratio,
        swipe_y_ratio,
        geometry=base.current_stage_geometry(proxy),
    )
    proxy.call_tool(
        "swipe_screen",
        {
            "fromX": from_x,
            "fromY": from_y,
            "toX": to_x,
            "toY": to_y,
            # The MCP defaults to a short 300 ms/20-step motion. Pokémon GO
            # occasionally consumes that as a sheet/Stage Manager gesture
            # instead of its detail pager. Send one explicit, finger-like
            # motion; SafeProxy still verifies the resulting page before any
            # later action.
            "duration": PAGER_SWIPE_DURATION_MS,
            "steps": PAGER_SWIPE_STEPS,
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
        wait_started = time.monotonic()
        attempt = 0
        while attempt < MAX_PERSISTENT_READ_ONLY_ATTEMPTS:
            attempt += 1
            _emit_read_only_wait(
                stage="翻页前详情稳定性确认",
                reason="尚未取得两张一致的详情身份帧。",
                attempt=attempt,
                started_at=wait_started,
                next_action="读取下一张详情截图；一致后才会执行一次翻页。",
            )
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
    previous_snapshot: Snapshot | None = None,
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
    changed_samples: dict[
        tuple[str, str, str, str], list[tuple[Snapshot, str, str, DetailFingerprint]]
    ] = {}
    previous_key = navigation_confirmation_key(previous)
    previous_visual_key = (
        _detail_card_visual_signature(previous_snapshot)
        if previous_snapshot is not None
        else ""
    )
    visual_changed_samples: dict[str, list[tuple[Snapshot, str]]] = {}
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
        if not digest:
            # This exact pixel frame belonged to the Pokemon before the swipe.
            # A missing digest cannot prove that independently captured pixels
            # changed either.  In both cases this frame must never authorize a
            # new identity or a subsequent rename.
            continue
        state = _overview_state(snapshot)
        if state is not None:
            raise DetailExitedToOverview(snapshot, state)
        visual_key = _detail_card_visual_signature(snapshot)
        read_key = _independent_read_key(snapshot, digest)
        if visual_key and previous_visual_key:
            if visual_key != previous_visual_key:
                visual_samples = visual_changed_samples.setdefault(visual_key, [])
                if read_key not in {key for _sample, key in visual_samples}:
                    visual_samples.append((snapshot, read_key))
                if len(visual_samples) >= CHANGED_IDENTITY_CONFIRMATIONS:
                    return (
                        snapshot,
                        DetailFingerprint((), "", "", "", ""),
                        True,
                        tuple(sample for sample, _key in visual_samples),
                    )
                # Card content has changed but still needs two independent
                # visual reads. Do not spend an expensive OCR pass on this
                # intermediate frame: the visual proof is navigation-only,
                # and the later name gate remains strict.
                continue
            # This is a visual same-card result, not an identity proof from
            # fresh OCR. Return it immediately so the caller can try another
            # safe lane; it is marked non-terminal and can never label a
            # cached/stalled capture as the end of storage.
            return snapshot, previous, False, (snapshot,)
        if digest in blocked:
            # A previously verified pixel frame cannot prove a swipe reached
            # another Pokémon and must never contribute to a storage-end
            # decision. It can still prove that the visible screen is the
            # same harmless detail layout, which is enough to select a
            # different safe gesture lane immediately instead of waiting for
            # minutes on a cached screenshot stream.
            if _looks_like_detail_visual(snapshot):
                return snapshot, previous, False, (snapshot,)
            continue
        try:
            # This proves navigation only.  The subsequent per-Pokémon
            # identity gate still needs three fresh name reads before it can
            # appraise or rename anything, so an unreadable custom nickname
            # must not turn a valid post-swipe detail into an endless wait.
            current = detail_fingerprint(snapshot, require_name=False)
        except PolicyViolation:
            continue
        current_key = navigation_confirmation_key(current)
        if current_key and previous_key and current_key != previous_key:
            samples = changed_samples.setdefault(current_key, [])
            if read_key in {
                sample_key for _sample, _digest, sample_key, _fingerprint in samples
            }:
                continue
            samples.append((snapshot, digest, read_key, current))
            if len(samples) >= CHANGED_IDENTITY_CONFIRMATIONS:
                return (
                    snapshot,
                    samples[-1][3],
                    True,
                    tuple(
                        sample
                        for sample, _digest, _read_key, _fingerprint in samples
                    ),
                )
            continue
        if current_key and previous_key and current_key == previous_key:
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
    changed_samples: dict[
        tuple[str, str, str, str], list[tuple[Snapshot, str, str, DetailFingerprint]]
    ] = {}
    previous_key = navigation_confirmation_key(previous)
    visual_detail_samples: list[tuple[Snapshot, str]] = []
    wait_started = time.monotonic()
    attempt = 0
    while attempt < MAX_PERSISTENT_READ_ONLY_ATTEMPTS:
        attempt += 1
        _emit_read_only_wait(
            stage="翻页后身份变化核验",
            reason="滑动已发出，但新详情的身份字段暂时无法由 OCR 验证。",
            attempt=attempt,
            started_at=wait_started,
            next_action="读取下一张详情截图；收齐三张不同身份帧后才处理下一只。",
        )
        snapshot = base._next_snapshot(proxy, 3.0)
        digest = _snapshot_digest(snapshot)
        if not digest or digest in blocked:
            continue
        state = _overview_state(snapshot)
        if state is not None:
            raise DetailExitedToOverview(snapshot, state)
        # A freshly captured, visually classified detail may occasionally
        # have no OCR numeric fields at all (notably during this iPad's
        # animated shiny background).  It is still safe to carry that page to
        # the next iteration after three independent captures: the next
        # iteration repeats the strict three-frame *name* proof before it can
        # appraise or rename anything.  This only unblocks navigation; it is
        # never a rename authorization.
        try:
            is_visible_detail = base.local_page_state(snapshot) == "DETAIL"
        except Exception:
            is_visible_detail = False
        is_visible_detail = is_visible_detail or _looks_like_detail_visual(snapshot)
        if is_visible_detail:
            read_key = _independent_read_key(snapshot, digest)
            if read_key not in {key for _sample, key in visual_detail_samples}:
                visual_detail_samples.append((snapshot, read_key))
                if len(visual_detail_samples) >= CHANGED_IDENTITY_CONFIRMATIONS:
                    return (
                        snapshot,
                        DetailFingerprint((), "", "", "", ""),
                        True,
                        tuple(sample for sample, _key in visual_detail_samples),
                    )
        try:
            # See _observe_after_swipe: numeric identity may carry a newly
            # reached detail through a temporary title-OCR gap, but it never
            # authorizes a rename on its own.
            current = detail_fingerprint(snapshot, require_name=False)
        except PolicyViolation:
            continue
        current_key = navigation_confirmation_key(current)
        if current_key and previous_key and current_key == previous_key:
            return snapshot, current, False, ()
        if not current_key or not previous_key:
            continue
        samples = changed_samples.setdefault(current_key, [])
        read_key = _independent_read_key(snapshot, digest)
        if read_key in {
            sample_key for _sample, _digest, sample_key, _fingerprint in samples
        }:
            continue
        samples.append((snapshot, digest, read_key, current))
        if len(samples) >= CHANGED_IDENTITY_CONFIRMATIONS:
            return (
                snapshot,
                samples[-1][3],
                True,
                tuple(
                    sample
                    for sample, _digest, _read_key, _fingerprint in samples
                ),
            )
    return None


def swipe_to_verified_next(
    proxy: SafeProxy,
    detail: Snapshot,
    *,
    before: DetailFingerprint | None = None,
    allow_opposite_direction: bool = False,
) -> VerifiedNextDetail:
    previous = before or detail_fingerprint(detail)
    if before is None and previous.name_tokens:
        # Callers without a precomputed fingerprint need this short baseline
        # to prove that their starting page is stable before any swipe.
        detail, previous = _stable_baseline(proxy, detail, previous)
    elif before is not None:
        # The direct-detail batch has already required three independent
        # identity reads immediately before it calls us. Asking it for two
        # more title-OCR matches here is redundant and can deadlock on an
        # otherwise stable iPad7,2 Stage Manager capture when OCR is briefly
        # empty. Keep the existing post-swipe three-frame proof unchanged.
        base.emit(
            "status",
            message="当前详情已由三次独立读取确认；直接验证下一次翻页。",
        )
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
    def _opposite_direction(direction: str) -> str:
        return "right" if direction == "left" else "left"

    # The user-verified base swipe stays the first try. If all lanes fail to
    # show identity progress in a bounded window, retry once with the opposite
    # gesture so a captured coordinate inversion only stalls for one full cycle
    # instead of ending the whole batch.
    # Always begin with the user-verified next direction.  A prior opposite
    # fallback proves only that one inverted/edge gesture happened to move;
    # persisting it would make the following card walk backwards.  The
    # opposite direction remains a bounded last-resort probe for this call.
    preferred_direction = NEXT_PAGER_DIRECTION
    direction_plan: list[str] = [preferred_direction]
    if allow_opposite_direction:
        direction_plan.append(_opposite_direction(preferred_direction))
    direction_plan = direction_plan[:2]

    try:
        setattr(proxy, "_batch_swipe_direction", preferred_direction)
    except AttributeError:
        pass
    continue_with_second_direction = False
    for sweep_index, direction in enumerate(direction_plan):
        sweep_attempts = [
            (direction, lane)
            for lane in SAFE_PAGER_Y_RATIOS[:MAX_VERIFIED_SWIPE_ATTEMPTS]
        ]
        sweep_unchanged = 0
        for attempt_number, (swipe_direction, lane) in enumerate(sweep_attempts, start=1):
            overall_attempt = sweep_index * MAX_VERIFIED_SWIPE_ATTEMPTS + attempt_number
            direction_text = (
                "从右往左（←）" if swipe_direction == "left" else "从左往右（→）"
            )
            status_message = (
                f"翻页尝试 {overall_attempt}/{len(direction_plan) * MAX_VERIFIED_SWIPE_ATTEMPTS}："
                f"在精灵图像区{direction_text}短滑；"
                "随后读取新截图确认游戏是否真的切到下一只。"
            )
            if sweep_index > 0 and attempt_number == 1:
                status_message += "（上次方向未成功，尝试反向补偿）"
            base.emit(
                "status",
                message=status_message,
            )
            _swipe_next_once(proxy, direction=swipe_direction, swipe_y_ratio=lane)
            observed = _observe_after_swipe(proxy, previous, detail)
            if observed is None:
                observed = _wait_for_post_swipe_identity(proxy, previous)
            if observed is None:
                raise NoNextPokemon(
                    "横向翻页后连续只读采样仍无法确认安全详情页"
                )
            snapshot, current, changed, samples = observed
            if changed:
                try:
                    setattr(proxy, "_batch_swipe_direction", swipe_direction)
                except AttributeError:
                    pass
                return VerifiedNextDetail(snapshot, current, samples)
            # A non-empty sample bundle here is the explicit cached-frame marker
            # from _observe_after_swipe. It is useful only to try another safe
            # lane; it is deliberately excluded from the strict end-of-storage
            # proof because the pixels predate this gesture.
            if not samples:
                unchanged_confirmations += 1
                sweep_unchanged += 1
            base.emit(
                "status",
                message=(
                    "本次滑动尚未被三张新画面确认；"
                    "不会把它算作翻页或增加进度，正在换下一条安全图像区尝试。"
                ),
            )
            detail = snapshot
            # Stay in the same verified direction while trying the remaining
            # safe artwork lanes.  The old ``break`` exited after the first
            # swallowed gesture, so neither the four-attempt end proof nor a
            # later successful lane could ever be reached.
            if sweep_unchanged < MAX_VERIFIED_SWIPE_ATTEMPTS:
                continue
        if sweep_unchanged < MAX_VERIFIED_SWIPE_ATTEMPTS:
            continue_with_second_direction = False
            break
        if sweep_index == 0 and allow_opposite_direction:
            continue_with_second_direction = True
            base.emit(
                "status",
                message=(
                    "首选方向连续四次未见身份确认；"
                    "将暂时改用反向手势再验证一次。"
                ),
            )
    if not continue_with_second_direction and sweep_unchanged < MAX_VERIFIED_SWIPE_ATTEMPTS:
        raise NoNextPokemon(
            "横向翻页后未验证到不同宝可梦；"
            "无法证明已到盒子末尾"
        )
    if unchanged_confirmations == MAX_VERIFIED_SWIPE_ATTEMPTS * len(direction_plan):
        raise VerifiedEndOfStorage(
            "已在纯详情页连续尝试后，稳定身份均未变化"
        )
    raise NoNextPokemon(
        "横向翻页后未验证到不同宝可梦；"
        "无法证明已到盒子末尾"
    )
