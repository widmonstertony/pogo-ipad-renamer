from __future__ import annotations

import base64
import io
import unittest

from PIL import Image, ImageDraw

from pogo_iphone_renamer.landscape_cv_v3 import measure_ipad14_6_appraisal_v3


def synthetic_raw(value: int, offset: int) -> str:
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
        draw.rounded_rectangle((start, y - 5, track_end, y + 5), radius=5, fill=(228, 228, 228))
        if value:
            endpoint = start - 1 + round(span * value / 15)
            draw.rounded_rectangle((start, y - 5, endpoint, y + 5), radius=5, fill=color)
        for fraction in (1 / 3, 2 / 3):
            tick = round(start + span * fraction)
            draw.rectangle((tick - 3, y - 7, tick + 3, y + 7), fill="white")
    raw = image.rotate(-90, expand=True)
    output = io.BytesIO()
    raw.save(output, format="JPEG", quality=95)
    return base64.b64encode(output.getvalue()).decode("ascii")


class LandscapeCVV3Tests(unittest.TestCase):
    def test_all_values_at_multiple_vertical_offsets(self) -> None:
        for offset in (0, 20, 40):
            for value in range(16):
                with self.subTest(offset=offset, value=value):
                    result = measure_ipad14_6_appraisal_v3(
                        synthetic_raw(value, offset), "ROTATED_90_COUNTERCLOCKWISE"
                    )
                    self.assertEqual((result.attack, result.defense, result.stamina), (value,) * 3)
                    self.assertGreaterEqual(result.confidence, 0.90)


if __name__ == "__main__":
    unittest.main()
