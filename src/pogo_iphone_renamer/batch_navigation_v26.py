from __future__ import annotations

import re
from dataclasses import dataclass

from . import ipad_landscape_agent as base
from .appraisal_agent import Snapshot
from .landscape_cv_calibrated import measure_ipad14_6_appraisal
from .local_ocr import ocr_mcp_screenshot
from .local_ocr_v3 import analyze_name_region
from .policy import PolicyViolation
from .server import SafeProxy


CP_TOKEN = re.compile(r"^CP\s*\d+$", re.IGNORECASE)
HP_TOKEN = re.compile(r"^\d+\s*/\s*\d+\s*HP$", re.IGNORECASE)
WEIGHT_TOKEN = re.compile(r"^\d+(?:[.,]\d+)?\s*kg$", re.IGNORECASE)
HEIGHT_TOKEN = re.compile(r"^\d+(?:[.,]\d+)?\s*m$", re.IGNORECASE)


class NoNextPokemon(PolicyViolation):
    """A bounded verified swipe could not reach a different detail page."""


class VerifiedEndOfStorage(NoNextPokemon):
    """Four swipes remained on the same verified plain-detail identity."""


MAX_VERIFIED_SWIPE_ATTEMPTS = 4
OBSERVATIONS_PER_SWIPE = 8
OBSERVATION_DELAY_SECONDS = 0.8


def _normalized(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


@dataclass(frozen=True)
class DetailFingerprint:
    name_tokens: tuple[str, ...]
    cp: str
    hp: str
    weight: str
    height: str

    def stable_fields(self) -> tuple[str, ...]:
        return ("|".join(self.name_tokens), self.cp, self.hp, self.weight, self.height)


def detail_fingerprint(snapshot: Snapshot) -> DetailFingerprint:
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
    for line in ocr_mcp_screenshot(snapshot.image, base.ORIENTATION):
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
    fingerprint = DetailFingerprint(name_tokens, cp, hp, weight, height)
    numeric_fields = (cp, hp, weight, height)
    if not name_tokens or not any(numeric_fields):
        raise PolicyViolation("详情页稳定身份字段不足；不会自动翻页")
    return fingerprint


def fingerprints_differ(before: DetailFingerprint, after: DetailFingerprint) -> bool:
    pairs = [
        (old, new)
        for old, new in zip(before.stable_fields(), after.stable_fields())
        if old and new
    ]
    return any(old != new for old, new in pairs)


def _swipe_next_once(proxy: SafeProxy) -> None:
    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 未返回触控空间")
    from_x, from_y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        0.78,
        0.50,
        geometry=base.current_stage_geometry(proxy),
    )


def _observe_after_swipe(
    proxy: SafeProxy,
    previous: DetailFingerprint,
) -> tuple[Snapshot, DetailFingerprint, bool] | None:
    """Observe a bounded settling window after a swipe.

    A changed identity wins immediately.  Otherwise the most recent strongly
    verified copy of the previous detail is returned, allowing a swallowed
    swipe to be retried from a known-safe page.  Frames that are transitioning,
    temporarily unclassified, or have incomplete OCR are observation failures,
    not evidence that navigation is impossible.
    """

    same: tuple[Snapshot, DetailFingerprint, bool] | None = None
    for _ in range(OBSERVATIONS_PER_SWIPE):
        snapshot = base._next_snapshot(proxy, OBSERVATION_DELAY_SECONDS)
        try:
            current = detail_fingerprint(snapshot)
        except PolicyViolation:
            continue
        if fingerprints_differ(previous, current):
            return snapshot, current, True
        same = snapshot, current, False
    return same
    to_x, to_y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        0.22,
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
            "_intent": "navigate horizontally to next Pokemon detail",
            "_expected_after": "DETAIL for a different Pokemon",
        },
    )


def swipe_to_verified_next(
    proxy: SafeProxy,
    detail: Snapshot,
    *,
    before: DetailFingerprint | None = None,
) -> tuple[Snapshot, DetailFingerprint]:
    previous = before or detail_fingerprint(detail)
    unchanged_confirmations = 0
    for _attempt in range(MAX_VERIFIED_SWIPE_ATTEMPTS):
        _swipe_next_once(proxy)
        observed = _observe_after_swipe(proxy, previous)
        if observed is None:
            raise NoNextPokemon(
                "横向翻页后连续只读采样仍无法确认安全详情页"
            )
        snapshot, current, changed = observed
        if changed:
            return snapshot, current
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
