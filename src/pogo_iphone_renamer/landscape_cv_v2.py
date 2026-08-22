from __future__ import annotations

from typing import Callable

from PIL import Image

from .landscape_cv import (
    IVMeasurement,
    _is_attack_fill,
    _is_gold_fill,
    rotate_mcp_image_upright,
)


PixelPredicate = Callable[[tuple[int, int, int]], bool]


def _is_gray_track(pixel: tuple[int, int, int]) -> bool:
    red, green, blue = pixel
    return 180 <= (red + green + blue) / 3 <= 242 and max(pixel) - min(pixel) <= 15


def _measure(
    image: Image.Image,
    *,
    y_min_ratio: float,
    y_max_ratio: float,
    fill: PixelPredicate,
) -> tuple[int, int, int, float]:
    width, height = image.size
    x_start = round(width * 0.087)
    full_endpoint = round(width * 0.348)
    search_end = round(width * 0.355)
    full_span = full_endpoint - x_start + 1

    def row_support(y: int) -> int:
        return sum(
            1
            for x in range(x_start, search_end + 1)
            if fill(image.getpixel((x, y))) or _is_gray_track(image.getpixel((x, y)))
        )

    rows = range(round(height * y_min_ratio), round(height * y_max_ratio) + 1)
    center = max(rows, key=row_support)
    support = row_support(center)
    if support < full_span * 0.25:
        raise ValueError("appraisal bar track was not found in its calibrated region")

    colored: list[int] = []
    for x in range(x_start, search_end + 1):
        votes = sum(
            1
            for y in range(max(0, center - 3), min(height, center + 4))
            if fill(image.getpixel((x, y)))
        )
        if votes >= 4:
            colored.append(x)

    if not colored:
        return 0, x_start - 1, center, min(1.0, support / full_span)

    endpoint = max(colored)
    raw = 15.0 * (endpoint - x_start + 1) / full_span
    value = max(0, min(15, round(raw)))
    error = abs(raw - value)
    # A 5/10 tick deliberately removes several colored pixels exactly at the
    # endpoint. Half a point still rounds unambiguously; scale confidence over
    # a two-point band so those known white seams remain above 90%.
    confidence = max(0.0, min(1.0, 1.0 - error / 2.0))
    return value, endpoint, center, confidence


def measure_ipad14_6_appraisal_v2(image_base64: str, orientation: str) -> IVMeasurement:
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, height = image.size
    if width <= height:
        raise ValueError(f"expected landscape appraisal image, got {width}x{height}")

    attack = _measure(
        image,
        y_min_ratio=0.700,
        y_max_ratio=0.735,
        fill=_is_attack_fill,
    )
    defense = _measure(
        image,
        y_min_ratio=0.750,
        y_max_ratio=0.790,
        fill=_is_gold_fill,
    )
    stamina = _measure(
        image,
        y_min_ratio=0.800,
        y_max_ratio=0.840,
        fill=_is_gold_fill,
    )
    return IVMeasurement(
        attack=attack[0],
        defense=defense[0],
        stamina=stamina[0],
        confidence=min(attack[3], defense[3], stamina[3]),
        endpoints=(attack[1], defense[1], stamina[1]),
        row_centers=(attack[2], defense[2], stamina[2]),
    )
