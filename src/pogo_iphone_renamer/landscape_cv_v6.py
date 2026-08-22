from __future__ import annotations

import math
import statistics

from PIL import Image

from .landscape_cv import IVMeasurement, rotate_mcp_image_upright
from .landscape_cv_v4 import _select_track_rows
from .landscape_cv_v5 import (
    _detected_bar_geometry,
    _is_any_iv_fill,
    _is_track_pixel,
    _row_consensus_endpoint,
)


def _track_component_at(image: Image.Image, y: int, probe: float) -> tuple[int, int] | None:
    """Return the uninterrupted track component containing a safe probe."""

    center = round(probe)
    candidates = sorted(
        range(max(0, center - 3), min(image.width - 1, center + 3) + 1),
        key=lambda x: abs(x - probe),
    )
    seed = next(
        (x for x in candidates if _is_track_pixel(image.getpixel((x, y)))),
        None,
    )
    if seed is None:
        return None
    left = seed
    right = seed
    while left > 0 and _is_track_pixel(image.getpixel((left - 1, y))):
        left -= 1
    while right + 1 < image.width and _is_track_pixel(image.getpixel((right + 1, y))):
        right += 1
    return left, right


def _verify_track_extent(
    image: Image.Image,
    row_centers: list[int],
    geometry: tuple[float, float],
) -> float:
    """Cross-check divider geometry against the physical final-track edge.

    The neutral empty track can visually merge into the neutral card
    background immediately to its left when an IV is zero.  Its *right* edge
    remains isolated for every value from 0 through 15, so the last-cell
    component is the invariant independent geometry check.
    """

    right_edges: list[int] = []
    x_start, full_endpoint = geometry
    unit = (full_endpoint - x_start + 1.0) / 15.0
    for center_y in row_centers:
        for y in range(max(0, center_y - 2), min(image.height, center_y + 3)):
            final_segment = _track_component_at(
                image, y, x_start + unit * 14.5
            )
            if final_segment is not None:
                right_edges.append(final_segment[1])
    if len(right_edges) < 9:
        raise ValueError("appraisal track extent was not independently detected")

    track_right = statistics.median(right_edges)
    right_error = abs(track_right - full_endpoint)
    if right_error > max(3.0, unit * 0.22):
        raise ValueError("divider and track right edges disagree")

    right_inliers = [
        edge for edge in right_edges if abs(edge - track_right) <= 3.0
    ]
    if len(right_inliers) < math.ceil(len(right_edges) * 0.70):
        raise ValueError("appraisal track edges disagree across rows")
    return max(0.0, min(1.0, 1.0 - right_error / max(1.0, unit)))


def _cell_occupancy_value(
    image: Image.Image,
    center_y: int,
    geometry: tuple[float, float],
) -> tuple[int, float]:
    """Decode IV using 15 cell centres, independently of the bar endpoint."""

    x_start, full_endpoint = geometry
    unit = (full_endpoint - x_start + 1.0) / 15.0
    if unit <= 4.0:
        raise ValueError("appraisal cells are too narrow to sample safely")
    x_radius = max(1, round(unit * 0.12))
    scores: list[float] = []
    for cell in range(15):
        center_x = x_start + (cell + 0.5) * unit
        xs = range(
            max(0, round(center_x) - x_radius),
            min(image.width - 1, round(center_x) + x_radius) + 1,
        )
        # The real game sometimes renders a bar only three or four physical
        # pixels thick at the row selected by the track detector.  Averaging
        # the whole five-row band therefore diluted a perfectly solid cell to
        # 0.76 merely because one anti-aliased edge row was transparent.  Use
        # the median of the three strongest *horizontal* slices instead.  A
        # cell still needs colour support on three separate rows, so a single
        # noisy line cannot make an empty cell look filled; the independent
        # endpoint decoder remains an exact second check.
        slice_scores = [
            sum(
                _is_any_iv_fill(image.getpixel((x, y)))
                for x in xs
            )
            / len(xs)
            for y in range(
                max(0, center_y - 2),
                min(image.height - 1, center_y + 2) + 1,
            )
        ]
        strongest_three = sorted(slice_scores, reverse=True)[:3]
        scores.append(statistics.median(strongest_three))

    filled = [score >= 0.60 for score in scores]
    value = 0
    while value < 15 and filled[value]:
        value += 1
    if any(filled[value:]):
        raise ValueError("appraisal cell occupancy is not a single filled prefix")
    filled_scores = scores[:value]
    empty_scores = scores[value:]
    if filled_scores and min(filled_scores) < 0.78:
        raise ValueError("a filled IV cell has insufficient colour support")
    if empty_scores and max(empty_scores) > 0.22:
        raise ValueError("an empty IV cell has unexpected colour support")
    margins = [
        (score - 0.5) / 0.28 if index < value else (0.5 - score) / 0.28
        for index, score in enumerate(scores)
    ]
    return value, max(0.0, min(1.0, min(margins)))


def measure_upright_appraisal_v6(image: Image.Image) -> IVMeasurement:
    """Require endpoint, cell occupancy and track geometry to all agree."""

    rows = _select_track_rows(image)
    x_start, full_endpoint, divider_confidence = _detected_bar_geometry(image, rows)
    geometry = (x_start, full_endpoint)
    extent_confidence = _verify_track_extent(image, rows, geometry)
    endpoint_results = [
        _row_consensus_endpoint(
            image,
            row,
            _is_any_iv_fill,
            geometry=geometry,
        )
        for row in rows
    ]
    cell_results = [_cell_occupancy_value(image, row, geometry) for row in rows]
    endpoint_values = tuple(result[0] for result in endpoint_results)
    cell_values = tuple(result[0] for result in cell_results)
    if endpoint_values != cell_values:
        raise ValueError(
            f"independent IV decoders disagree: endpoint={endpoint_values}, cells={cell_values}"
        )
    return IVMeasurement(
        attack=endpoint_values[0],
        defense=endpoint_values[1],
        stamina=endpoint_values[2],
        confidence=min(
            divider_confidence,
            extent_confidence,
            *(result[2] for result in endpoint_results),
            *(result[1] for result in cell_results),
        ),
        endpoints=tuple(result[1] for result in endpoint_results),
        row_centers=tuple(rows),
    )


def measure_ipad14_6_appraisal_v6(
    image_base64: str, orientation: str
) -> IVMeasurement:
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, height = image.size
    if orientation != "STAGE_MANAGER_MAXIMIZED" and width <= height:
        raise ValueError(f"expected landscape appraisal image, got {width}x{height}")
    return measure_upright_appraisal_v6(image)
