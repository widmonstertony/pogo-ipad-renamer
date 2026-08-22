from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from pogo_iphone_renamer.landscape_cv_v4 import _select_track_rows


class LandscapeCVV4DynamicTests(unittest.TestCase):
    def test_selects_evenly_spaced_tracks_not_distractors(self) -> None:
        width, height = 1366, 1024
        image = Image.new("RGB", (width, height), "white")
        draw = ImageDraw.Draw(image)
        start = round(width * 0.087)
        end = round(width * 0.355)
        for y in (704, 772, 827, 879, 921):
            color = (210, 130, 133) if y in (704, 772) else (230, 169, 89)
            draw.rectangle((start, y - 5, end, y + 5), fill=color)
        selected = _select_track_rows(image)
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected[1] - selected[0], 55)
        self.assertEqual(selected[2] - selected[1], 52)
        self.assertGreater(selected[0], 750)
        self.assertLess(selected[2], 900)


if __name__ == "__main__":
    unittest.main()
