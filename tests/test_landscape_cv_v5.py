from __future__ import annotations

import base64
import io
import unittest

from PIL import Image, ImageDraw

from pogo_iphone_renamer.landscape_cv_v5 import (
    _is_any_iv_fill,
    _row_consensus_endpoint,
    measure_ipad14_6_appraisal_v5,
)


def synthetic_raw(value: int, *, offset: int, erode: int) -> str:
    width, height = 1366, 1024
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    start = round(width * 0.087)
    fill_end = round(width * 0.348)
    track_end = round(width * 0.355)
    span = fill_end - start + 1
    rows = (
        (736 + offset, (210, 130, 133)),
        (789 + offset, (230, 169, 89)),
        (842 + offset, (232, 169, 90)),
    )
    for y, color in rows:
        draw.rounded_rectangle(
            (start, y - 5, track_end, y + 5), radius=5, fill=(228, 228, 228)
        )
        if value:
            if value == 15:
                color = (210, 130, 133)
            endpoint = start - 1 + round(span * value / 15) - erode
            draw.rounded_rectangle(
                (start, y - 5, endpoint, y + 5), radius=5, fill=color
            )
        for fraction in (1 / 3, 2 / 3):
            tick = round(start + span * fraction)
            draw.rectangle((tick - 3, y - 7, tick + 3, y + 7), fill="white")
    raw = image.rotate(-90, expand=True)
    output = io.BytesIO()
    raw.save(output, format="JPEG", quality=92)
    return base64.b64encode(output.getvalue()).decode("ascii")


class LandscapeCVV5Tests(unittest.TestCase):
    def test_all_values_survive_offsets_and_render_erosion(self) -> None:
        for offset in (0, 20, 40):
            for erode in (0, 1, 2):
                for value in range(16):
                    with self.subTest(offset=offset, erode=erode, value=value):
                        result = measure_ipad14_6_appraisal_v5(
                            synthetic_raw(value, offset=offset, erode=erode),
                            "ROTATED_90_COUNTERCLOCKWISE",
                        )
                        self.assertEqual(
                            (result.attack, result.defense, result.stamina),
                            (value,) * 3,
                        )
                        self.assertGreaterEqual(result.confidence, 0.90)

    def test_center_weighting_accepts_rounded_cap_edge_rows(self) -> None:
        image = Image.new("RGB", (1024, 240), "white")
        draw = ImageDraw.Draw(image)
        center_y = 120
        x_start = round(image.width * 0.087)
        endpoints = (330, 330, 330, 330, 330, 329, 329)
        for y, endpoint in zip(range(center_y - 3, center_y + 4), endpoints):
            draw.line((x_start, y, endpoint, y), fill=(210, 130, 133))
        value, endpoint, confidence = _row_consensus_endpoint(
            image, center_y, _is_any_iv_fill
        )
        self.assertEqual(value, 14)
        self.assertEqual(endpoint, 330)
        self.assertGreaterEqual(confidence, 0.80)


if __name__ == "__main__":
    unittest.main()
