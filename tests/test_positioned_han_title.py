from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from pogo_iphone_renamer import name_recognition as names
from pogo_iphone_renamer.local_ocr import OCRLine


class PositionedHanTitleTests(unittest.TestCase):
    def classify(self, title: OCRLine, *, full_title: OCRLine | None = None):
        crop = (title, OCRLine("96 / 96 HP", 0.99))
        full = (
            full_title or title,
            OCRLine("96 / 96 HP", 0.99, (458, 754, 566, 781)),
            OCRLine("百足蜈蚣的糖果", 0.99, (455, 1090, 611, 1117)),
        )
        with patch.object(
            names,
            "rotate_mcp_image_upright",
            return_value=Image.new("RGB", (1024, 1366)),
        ), patch.object(names, "ocr_image", side_effect=(crop, (), full)):
            return names.analyze_name_region("unused", "unused")

    def test_exact_positioned_custom_han_title_is_preserved(self) -> None:
        title = OCRLine("完美蜈蚣", 0.995, (401, 651, 620, 716))

        result = self.classify(title)

        self.assertEqual(result.species, "百足蜈蚣")
        self.assertFalse(result.is_default)
        self.assertEqual(result.evidence, ("完美蜈蚣",))

    def test_unlocated_mismatched_or_low_confidence_han_is_not_enough(self) -> None:
        crop = OCRLine("完美蜈蚣", 0.995, (401, 651, 620, 716))
        variants = (
            OCRLine("完美蜈蚣", 0.995, None),
            OCRLine("完美蜈蚣", 0.995, (401, 1000, 620, 1065)),
            OCRLine("完美蜈蚣", 0.97, (401, 651, 620, 716)),
            OCRLine("最強蜈蚣", 0.995, (401, 651, 620, 716)),
        )
        for full_title in variants:
            with self.subTest(full_title=full_title):
                result = self.classify(crop, full_title=full_title)
                self.assertIsNone(result.species)


if __name__ == "__main__":
    unittest.main()
