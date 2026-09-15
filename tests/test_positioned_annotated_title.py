from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from pogo_iphone_renamer import local_ocr_v3 as names
from pogo_iphone_renamer.local_ocr import OCRLine


class PositionedAnnotatedTitleTests(unittest.TestCase):
    def classify(self, text="雷电雲121212", *, bounds=(327, 652, 660, 717), confidence=.94958, candy=True, hp=True):
        crop = tuple(OCRLine(text, .99) for text in ("80", "12", "12", "12", "119 / 119 HP"))
        full = [OCRLine(text, confidence, bounds)]
        if candy:
            full.append(OCRLine("雷電雲的糖果", .98932, (463, 1086, 602, 1119)))
        if hp:
            full.append(OCRLine("119 /119 HP", .9374, (446, 754, 577, 781)))
        with (
            patch.object(names, "rotate_mcp_image_upright", return_value=Image.new("RGB", (1024, 1366))),
            patch.object(names, "ocr_image", side_effect=(crop, (), tuple(full))),
        ):
            return names.analyze_name_region("unused", "unused")

    def test_mixed_han_ocr_with_numeric_suffix_only_authorizes_preservation(self):
        result = self.classify()
        self.assertFalse(result.is_default)
        self.assertEqual(result.species, "雷電雲")
        self.assertEqual(result.evidence, ("雷电雲121212",))

    def test_plain_mixed_han_title_is_not_misclassified_as_existing_nickname(self):
        for title in ("雷电雲", "雷电雲1", "雷电雲的糖果", "雷121212", "雷电雲的糖果XL"):
            with self.subTest(title=title):
                result = self.classify(title)
                self.assertIsNone(result.species)
                self.assertFalse(result.is_default)

    def test_position_confidence_and_independent_fields_remain_required(self):
        for kwargs in ({"bounds": None}, {"bounds": (327, 1052, 660, 1117)}, {"confidence": .89}, {"candy": False}, {"hp": False}):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(self.classify(**kwargs).species)


if __name__ == "__main__":
    unittest.main()
