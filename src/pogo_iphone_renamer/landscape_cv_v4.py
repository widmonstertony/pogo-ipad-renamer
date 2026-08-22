from __future__ import annotations

import itertools

from .landscape_cv import IVMeasurement, _is_attack_fill, _is_gold_fill, rotate_mcp_image_upright
from .landscape_cv_v2 import _is_gray_track
from .landscape_cv_v3 import _endpoint_value


def _select_track_rows(image) -> list[int]:
    width, height = image.size
    x_start = round(width * 0.087)
    x_end = round(width * 0.355)
    span = x_end - x_start + 1

    def support(y: int) -> int:
        return sum(
            1
            for x in range(x_start, x_end + 1)
            if (
                _is_attack_fill(image.getpixel((x, y)))
                or _is_gold_fill(image.getpixel((x, y)))
                or _is_gray_track(image.getpixel((x, y)))
            )
        )

    active = []
    for y in range(round(height * 0.68), round(height * 0.91) + 1):
        score = support(y)
        if score >= span * 0.24:
            active.append((y, score))
    groups: list[list[tuple[int, int]]] = []
    for item in active:
        if not groups or item[0] - groups[-1][-1][0] > 2:
            groups.append([item])
        else:
            groups[-1].append(item)
    peaks = [max(group, key=lambda item: item[1]) for group in groups if len(group) >= 3]

    expected_gap = height * 0.052
    choices: list[tuple[float, tuple[tuple[int, int], ...]]] = []
    for triple in itertools.combinations(peaks, 3):
        first_gap = triple[1][0] - triple[0][0]
        second_gap = triple[2][0] - triple[1][0]
        if not (height * 0.035 <= first_gap <= height * 0.070):
            continue
        if not (height * 0.035 <= second_gap <= height * 0.070):
            continue
        weak_penalty = sum(max(0.0, span * 0.70 - peak[1]) for peak in triple) / span
        cost = (
            abs(first_gap - expected_gap)
            + abs(second_gap - expected_gap)
            + 2.0 * abs(first_gap - second_gap)
            + weak_penalty
        )
        choices.append((cost, triple))
    if not choices:
        raise ValueError(f"no three evenly spaced appraisal tracks among {peaks}")
    triple = min(choices, key=lambda item: item[0])[1]
    return [item[0] for item in triple]


def measure_ipad14_6_appraisal_v4(image_base64: str, orientation: str) -> IVMeasurement:
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, height = image.size
    if orientation != "STAGE_MANAGER_MAXIMIZED" and width <= height:
        raise ValueError(f"expected landscape appraisal image, got {width}x{height}")
    rows = _select_track_rows(image)
    attack = _endpoint_value(image, rows[0], _is_attack_fill)
    defense = _endpoint_value(image, rows[1], _is_gold_fill)
    stamina = _endpoint_value(image, rows[2], _is_gold_fill)
    return IVMeasurement(
        attack=attack[0],
        defense=defense[0],
        stamina=stamina[0],
        confidence=min(attack[2], defense[2], stamina[2]),
        endpoints=(attack[1], defense[1], stamina[1]),
        row_centers=(rows[0], rows[1], rows[2]),
    )
