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
from .landscape_cv_v2 import _is_gray_track
from .landscape_cv_v4 import _select_track_rows


PixelPredicate = Callable[[tuple[int, int, int]], bool]


def _is_track_pixel(pixel: tuple[int, int, int]) -> bool:
    return _is_any_iv_fill(pixel) or _is_gray_track(pixel)


def _absence_runs(
    image: Image.Image,
    y: int,
    *,
    x_start: int,
    x_end: int,
) -> list[tuple[int, int]]:
    """Return white gaps surrounded by appraisal-track pixels on one row."""

    gaps: list[tuple[int, int]] = []
    current: list[int] = []
    for x in range(x_start, x_end + 1):
        if not _is_track_pixel(image.getpixel((x, y))):
            current.append(x)
            continue
        if current:
            gaps.append((current[0], current[-1]))
            current = []
    if current:
        gaps.append((current[0], current[-1]))
    return [
        (left, right)
        for left, right in gaps
        if left > x_start
        and right < x_end
        and _is_track_pixel(image.getpixel((left - 1, y)))
        and _is_track_pixel(image.getpixel((right + 1, y)))
    ]


def _tick_pair_for_row(
    image: Image.Image,
    y: int,
) -> tuple[float, float] | None:
    """Locate the two white 5/10-IV divider seams on a track row.

    The old implementation used a fixed left edge.  The supervised iPad's
    Stage Manager crop moved that edge by almost exactly one IV unit, causing
    every positive non-perfect value to be rounded one point too high.  The
    divider seams are rendered by the game itself and remain exactly five IV
    units apart, so they are the reliable scale reference.
    """

    width, _ = image.size
    search_start = round(width * 0.13)
    search_end = round(width * 0.40)
    maximum_gap = max(10, round(width * 0.012))
    gaps = [
        (left, right, (left + right) / 2.0)
        for left, right in _absence_runs(
            image,
            y,
            x_start=search_start,
            x_end=search_end,
        )
        if 2 <= right - left + 1 <= maximum_gap
    ]
    candidates: list[tuple[float, float, float]] = []
    for first_index, (_, _, first) in enumerate(gaps):
        for _, _, second in gaps[first_index + 1 :]:
            distance = second - first
            if not width * 0.07 <= distance <= width * 0.13:
                continue
            inferred_start = first - distance
            inferred_end = second + distance - 1.0
            if not width * 0.06 <= inferred_start <= width * 0.15:
                continue
            if not width * 0.32 <= inferred_end <= width * 0.49:
                continue
            # Both the historical full-screen crop and the current Stage
            # Manager crop live near these broad priors.  They only break ties;
            # the returned geometry comes exclusively from the two seams.
            cost = (
                abs(inferred_start / width - 0.10)
                + abs(inferred_end / width - 0.36)
            )
            candidates.append((cost, first, second))
    if not candidates:
        return None
    _, first, second = min(candidates)
    return first, second


def _detected_bar_geometry(
    image: Image.Image,
    row_centers: list[int],
) -> tuple[float, float, float]:
    """Return dynamic fill start/end plus confidence from divider consensus."""

    pairs: list[tuple[float, float]] = []
    for center_y in row_centers:
        for y in range(max(0, center_y - 2), min(image.height, center_y + 3)):
            pair = _tick_pair_for_row(image, y)
            if pair is not None:
                pairs.append(pair)
    if len(pairs) < 6:
        raise ValueError("appraisal divider seams were not detected consistently")

    first = statistics.median(pair[0] for pair in pairs)
    second = statistics.median(pair[1] for pair in pairs)
    distance = second - first
    if distance <= 0:
        raise ValueError("invalid appraisal divider spacing")
    deviations = [
        max(abs(pair[0] - first), abs(pair[1] - second)) for pair in pairs
    ]
    inliers = [deviation for deviation in deviations if deviation <= 2.5]
    if len(inliers) < math.ceil(len(pairs) * 0.80):
        raise ValueError("appraisal divider seams disagree across rows")

    x_start = first - distance
    full_endpoint = second + distance - 1.0
    full_span = full_endpoint - x_start + 1.0
    if not image.width * 0.22 <= full_span <= image.width * 0.38:
        raise ValueError("invalid appraisal divider-derived span")
    spread = statistics.median(inliers) if inliers else 3.0
    confidence = max(0.0, min(1.0, 1.0 - spread / 10.0))
    return x_start, full_endpoint, confidence


def _is_any_iv_fill(pixel: tuple[int, int, int]) -> bool:
    """Accept the gold partial fill and the red fill used for a perfect 15."""

    return _is_attack_fill(pixel) or _is_gold_fill(pixel)


def _row_consensus_endpoint(
    image: Image.Image,
    center_y: int,
    fill: PixelPredicate,
    *,
    geometry: tuple[float, float] | None = None,
) -> tuple[int, int, float]:
    """Measure one IV bar from several horizontal slices.

    Poke GO renders rounded/anti-aliased bar ends, so the last accepted pixel
    can move by a few columns even when the IV is unchanged.  Converting each
    strong row to an integer first, rejecting edge-row outliers, and then
    voting is considerably more stable than treating one 7-row majority
    endpoint as an exact physical coordinate.
    """

    width, height = image.size
    if geometry is None:
        # Kept only for direct unit-level callers.  Production measurement
        # always supplies geometry derived from the current screenshot.
        x_start = float(round(width * 0.087))
        full_endpoint = float(round(width * 0.348))
    else:
        x_start, full_endpoint = geometry
    search_start = max(0, math.floor(x_start - 3.0))
    search_end = min(width - 1, math.ceil(full_endpoint + 3.0))
    full_span = full_endpoint - x_start + 1.0
    if full_span <= 0:
        raise ValueError("invalid appraisal-bar geometry")

    slices: list[tuple[int, int, int]] = []
    for y in range(max(0, center_y - 3), min(height, center_y + 4)):
        colored = [
            x
            for x in range(search_start, search_end + 1)
            if fill(image.getpixel((x, y)))
        ]
        if colored:
            slices.append((y, len(colored), max(colored)))
    if not slices:
        # Track-row selection has already verified the gray/fill geometry.  A
        # complete absence of the correct fill colour therefore represents 0.
        return 0, round(x_start - 1.0), 1.0

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
        raw = 15.0 * (endpoint - x_start + 1.0) / full_span
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
    if median_error > 0.24:
        raise ValueError(
            f"appraisal endpoint is too close to an IV rounding boundary: {median_error:.3f}"
        )
    agreement = winning / total_weight
    # All strong slices selecting the same integer is the primary evidence.
    # Residual endpoint distance and inter-row spread refine the last few
    # percentage points without turning a rounded-cap offset into a false low
    # score (the v4 single-endpoint failure mode).
    closeness = max(0.0, 1.0 - median_error / 0.5)
    confidence = agreement * (0.96 + 0.04 * closeness)
    return value, endpoint, max(0.0, min(1.0, confidence))


def measure_upright_appraisal_v5(image: Image.Image) -> IVMeasurement:
    """Measure an already-upright Pokémon GO appraisal screenshot."""

    rows = _select_track_rows(image)
    x_start, full_endpoint, geometry_confidence = _detected_bar_geometry(
        image, rows
    )
    geometry = (x_start, full_endpoint)
    attack = _row_consensus_endpoint(
        image, rows[0], _is_any_iv_fill, geometry=geometry
    )
    defense = _row_consensus_endpoint(
        image, rows[1], _is_any_iv_fill, geometry=geometry
    )
    stamina = _row_consensus_endpoint(
        image, rows[2], _is_any_iv_fill, geometry=geometry
    )
    return IVMeasurement(
        attack=attack[0],
        defense=defense[0],
        stamina=stamina[0],
        confidence=min(
            geometry_confidence,
            attack[2],
            defense[2],
            stamina[2],
        ),
        endpoints=(attack[1], defense[1], stamina[1]),
        row_centers=(rows[0], rows[1], rows[2]),
    )


def measure_ipad14_6_appraisal_v5(
    image_base64: str, orientation: str
) -> IVMeasurement:
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, height = image.size
    if orientation != "STAGE_MANAGER_MAXIMIZED" and width <= height:
        raise ValueError(f"expected landscape appraisal image, got {width}x{height}")
    return measure_upright_appraisal_v5(image)
