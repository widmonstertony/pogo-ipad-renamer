from __future__ import annotations

from typing import Callable

from PIL import Image

from .landscape_cv import (
    IVMeasurement,
    _is_attack_fill,
    _is_gold_fill,
    rotate_mcp_image_upright,
)
from .landscape_cv_v2 import _is_gray_track


PixelPredicate = Callable[[tuple[int, int, int]], bool]


def _track_rows(image: Image.Image) -> list[int]:
    width, height = image.size
    x_start = round(width * 0.087)
    x_end = round(width * 0.355)
    span = x_end - x_start + 1

    def support(y: int) -> int:
        count = 0
        for x in range(x_start, x_end + 1):
            pixel = image.getpixel((x, y))
            if _is_attack_fill(pixel) or _is_gold_fill(pixel) or _is_gray_track(pixel):
                count += 1
        return count

    candidates = [
        (y, support(y))
        for y in range(round(height * 0.68), round(height * 0.90) + 1)
    ]
    active = [(y, score) for y, score in candidates if score >= span * 0.24]
    groups: list[list[tuple[int, int]]] = []
    for item in active:
        if not groups or item[0] - groups[-1][-1][0] > 2:
            groups.append([item])
        else:
            groups[-1].append(item)
    centers = [max(group, key=lambda item: item[1])[0] for group in groups if len(group) >= 3]
    if len(centers) != 3:
        raise ValueError(f"expected exactly three appraisal tracks, found {len(centers)} at {centers}")
    return centers


def _endpoint_value(
    image: Image.Image,
    center_y: int,
    fill: PixelPredicate,
) -> tuple[int, int, float]:
    width, height = image.size
    x_start = round(width * 0.087)
    full_endpoint = round(width * 0.348)
    search_end = round(width * 0.355)
    full_span = full_endpoint - x_start + 1
    colored: list[int] = []
    for x in range(x_start, search_end + 1):
        votes = sum(
            1
            for y in range(max(0, center_y - 3), min(height, center_y + 4))
            if fill(image.getpixel((x, y)))
        )
        if votes >= 4:
            colored.append(x)
    if not colored:
        return 0, x_start - 1, 1.0
    endpoint = max(colored)
    raw = 15.0 * (endpoint - x_start + 1) / full_span
    value = max(0, min(15, round(raw)))
    confidence = max(0.0, min(1.0, 1.0 - abs(raw - value) / 2.0))
    return value, endpoint, confidence


def measure_ipad14_6_appraisal_v3(image_base64: str, orientation: str) -> IVMeasurement:
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, height = image.size
    if width <= height:
        raise ValueError(f"expected landscape appraisal image, got {width}x{height}")
    rows = _track_rows(image)
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
