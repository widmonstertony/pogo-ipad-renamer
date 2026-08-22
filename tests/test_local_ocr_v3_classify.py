from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from pogo_iphone_renamer import local_ocr_v3
from pogo_iphone_renamer.local_ocr import OCRLine


class NameRegionClassificationTests(unittest.TestCase):
    def classify(self, lines):
        with (
            patch.object(
                local_ocr_v3,
                "rotate_mcp_image_upright",
                return_value=Image.new("RGB", (1366, 1024)),
            ),
            patch.object(local_ocr_v3, "ocr_image", return_value=tuple(lines)),
        ):
            return local_ocr_v3.analyze_name_region("unused", "unused")

    def test_plain_default_name_is_accepted(self) -> None:
        result = self.classify(
            (OCRLine("輕飄飄", 0.99), OCRLine("95 / 95 HP", 0.96))
        )
        self.assertTrue(result.is_default)
        self.assertEqual(result.species, "輕飄飄")

    def test_clipped_hp_suffix_does_not_skip_plain_cramorant(self) -> None:
        result = self.classify(
            (OCRLine("古月鳥", 0.99), OCRLine("108/108H", 0.96))
        )
        self.assertTrue(result.is_default)
        self.assertEqual(result.species, "古月鳥")

    def test_circled_iv_rendered_as_numbers_is_custom(self) -> None:
        result = self.classify(
            (
                OCRLine("輕飄飄", 0.99),
                OCRLine("15", 1.0),
                OCRLine("14", 1.0),
                OCRLine("14", 1.0),
                OCRLine("96", 1.0),
                OCRLine("95 / 95 HP", 0.96),
            )
        )
        self.assertFalse(result.is_default)
        self.assertEqual(result.species, "輕飄飄")


if __name__ == "__main__":
    unittest.main()
