from __future__ import annotations

import math
import statistics
from collections import Counter
from typing import Callable

from PIL import Image

from .landscape_cv import (
    IVMeasurement,
    _is_attack_fill,
    _is_gold_fill,
    rotate_mcp_image_upright,
)
from .landscape_cv_v4 import _select_track_rows


PixelPredicate = Callable[[tuple[int, int, int]], bool]


def _is_any_iv_fill(pixel: tuple[int, int, int]) -> bool:
    """Accept the gold partial fill and the red fill used for a perfect 15."""

    return _is_attack_fill(pixel) or _is_gold_fill(pixel)


def _row_consensus_endpoint(
    image: Image.Image,
    center_y: int,
    fill: PixelPredicate,
) -> tuple[int, int, float]:
    """Measure one IV bar from several horizontal slices.

    Poke GO renders rounded/anti-aliased bar ends, so the last accepted pixel
    can move by a few columns even when the IV is unchanged.  Converting each
    strong row to an integer first, rejecting edge-row outliers, and then
    voting is considerably more stable than treating one 7-row majority
    endpoint as an exact physical coordinate.
    """

    width, height = image.size
    x_start = round(width * 0.087)
    full_endpoint = round(width * 0.348)
    search_end = round(width * 0.355)
    full_span = full_endpoint - x_start + 1
    if full_span <= 0:
        raise ValueError("invalid appraisal-bar geometry")

    slices: list[tuple[int, int, int]] = []
    for y in range(max(0, center_y - 3), min(height, center_y + 4)):
        colored = [
            x
            for x in range(x_start, search_end + 1)
            if fill(image.getpixel((x, y)))
        ]
        if colored:
            slices.append((y, len(colored), max(colored)))
    if not slices:
        # Track-row selection has already verified the gray/fill geometry.  A
        # complete absence of the correct fill colour therefore represents 0.
        return 0, x_start - 1, 1.0

    strongest = max(count for _, count, _ in slices)
    strong = [
        (y, endpoint)
        for y, count, endpoint in slices
        if count >= max(2, math.ceil(strongest * 0.60))
    ]
    if not strong:
        raise ValueError("no strong appraisal-bar endpoint slices")

    unit = full_span / 15.0
    median_endpoint = statistics.median(endpoint for _, endpoint in strong)
    robust = [
        (y, endpoint)
        for y, endpoint in strong
        if abs(endpoint - median_endpoint) <= max(2.0, unit * 0.55)
    ]
    if len(robust) < min(3, len(strong)):
        raise ValueError("appraisal-bar endpoint slices do not agree")

    votes: list[int] = []
    raw_values: list[float] = []
    weights: list[int] = []
    for y, endpoint in robust:
        raw = 15.0 * (endpoint - x_start + 1) / full_span
        raw_values.append(raw)
        votes.append(max(0, min(15, round(raw))))
        # Rounded caps naturally shorten the top and bottom slices.  Weighting
        # the physical centre more heavily keeps a stable 5:2 centre-majority
        # from being rejected just because two edge rows cross a rounding
        # boundary, without accepting a genuinely split centre vote.
        distance = abs(y - center_y)
        weights.append((6, 4, 2, 1)[min(distance, 3)])
    counts: Counter[int] = Counter()
    for vote, weight in zip(votes, weights):
        counts[vote] += weight
    value, winning = counts.most_common(1)[0]
    total_weight = sum(weights)
    if winning < math.ceil(total_weight * 0.75):
        raise ValueError(f"appraisal-bar row vote is ambiguous: {counts}")

    winning_endpoints = [
        endpoint
        for (_, endpoint), vote in zip(robust, votes)
        if vote == value
    ]
    endpoint = round(statistics.median(winning_endpoints))
    winning_raw = [
        raw for raw, vote in zip(raw_values, votes) if vote == value
    ]
    median_error = abs(statistics.median(winning_raw) - value)
    agreement = winning / total_weight
    # All strong slices selecting the same integer is the primary evidence.
    # Residual endpoint distance and inter-row spread refine the last few
    # percentage points without turning a rounded-cap offset into a false low
    # score (the v4 single-endpoint failure mode).
    closeness = max(0.0, 1.0 - median_error / 0.5)
    confidence = agreement * (0.96 + 0.04 * closeness)
    return value, endpoint, max(0.0, min(1.0, confidence))


def measure_ipad14_6_appraisal_v5(
    image_base64: str, orientation: str
) -> IVMeasurement:
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, height = image.size
    if orientation != "STAGE_MANAGER_MAXIMIZED" and width <= height:
        raise ValueError(f"expected landscape appraisal image, got {width}x{height}")
    rows = _select_track_rows(image)
    attack = _row_consensus_endpoint(image, rows[0], _is_any_iv_fill)
    defense = _row_consensus_endpoint(image, rows[1], _is_any_iv_fill)
    stamina = _row_consensus_endpoint(image, rows[2], _is_any_iv_fill)
    return IVMeasurement(
        attack=attack[0],
        defense=defense[0],
        stamina=stamina[0],
        confidence=min(attack[2], defense[2], stamina[2]),
        endpoints=(attack[1], defense[1], stamina[1]),
        row_centers=(rows[0], rows[1], rows[2]),
    )
