from __future__ import annotations

import base64
import io
import unittest

from PIL import Image, ImageDraw

from pogo_iphone_renamer.landscape_cv_v5 import (
    _is_any_iv_fill,
    _row_consensus_endpoint,
    measure_ipad14_6_appraisal_v5,
    measure_upright_appraisal_v5,
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
            image,
            center_y,
            _is_any_iv_fill,
            geometry=(89.0, 348.0),
        )
        self.assertEqual(value, 14)
        self.assertEqual(endpoint, 330)
        self.assertGreaterEqual(confidence, 0.80)

    def test_stage_manager_crop_does_not_add_one_to_each_iv(self) -> None:
        image = Image.new("RGB", (1024, 1366), "white")
        draw = ImageDraw.Draw(image)
        start = 105
        full_endpoint = 366
        span = full_endpoint - start + 1
        rows = (1046, 1113, 1182)
        values = (2, 13, 11)
        for row, value in zip(rows, values):
            draw.rounded_rectangle(
                (start, row - 5, full_endpoint, row + 5),
                radius=5,
                fill=(228, 228, 228),
            )
            endpoint = start - 1 + round(span * value / 15)
            draw.rounded_rectangle(
                (start, row - 5, endpoint, row + 5),
                radius=5,
                fill=(230, 169, 89),
            )
            for fraction in (1 / 3, 2 / 3):
                tick = round(start + span * fraction)
                draw.rectangle(
                    (tick - 2, row - 7, tick + 2, row + 7),
                    fill="white",
                )
            # Reproduce the label and trainer-colour distractors visible in
            # the real Stage Manager frame.  Neither belongs to the IV track.
            draw.rectangle((70, row - 2, 102, row + 2), fill=(230, 169, 89))
            draw.rectangle((405, row - 2, 500, row + 2), fill=(230, 169, 89))

        result = measure_upright_appraisal_v5(image)
        self.assertEqual(
            (result.attack, result.defense, result.stamina),
            values,
        )
        self.assertNotEqual(
            (result.attack, result.defense, result.stamina),
            (3, 14, 12),
        )
        self.assertGreaterEqual(result.confidence, 0.90)

    def test_phone_reference_geometry_reads_15_14_12_not_15_15_13(self) -> None:
        image = Image.new("RGB", (589, 1280), "white")
        draw = ImageDraw.Draw(image)
        start = 70
        full_endpoint = 272
        span = full_endpoint - start + 1
        rows = (983, 1039, 1093)
        values = (15, 14, 12)
        for row, value in zip(rows, values):
            draw.rounded_rectangle(
                (start, row - 4, full_endpoint, row + 4),
                radius=4,
                fill=(228, 228, 228),
            )
            endpoint = start - 1 + round(span * value / 15)
            draw.rounded_rectangle(
                (start, row - 4, endpoint, row + 4),
                radius=4,
                fill=(210, 130, 133) if value == 15 else (230, 169, 89),
            )
            for fraction in (1 / 3, 2 / 3):
                tick = round(start + span * fraction)
                draw.rectangle(
                    (tick - 1, row - 6, tick + 1, row + 6),
                    fill="white",
                )

        result = measure_upright_appraisal_v5(image)
        self.assertEqual(
            (result.attack, result.defense, result.stamina),
            values,
        )
        self.assertNotEqual(
            (result.attack, result.defense, result.stamina),
            (15, 15, 13),
        )


if __name__ == "__main__":
    unittest.main()
