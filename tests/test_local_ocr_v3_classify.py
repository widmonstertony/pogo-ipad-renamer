from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from pogo_iphone_renamer import local_ocr_v3
from pogo_iphone_renamer.local_ocr import OCRLine


class NameRegionClassificationTests(unittest.TestCase):
    def test_name_crop_is_limited_to_the_title_row(self) -> None:
        self.assertEqual(local_ocr_v3.NAME_ROW_TOP, 0.47)
        self.assertEqual(local_ocr_v3.NAME_ROW_BOTTOM, 0.55)

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

    def test_rotated_dh_hp_prefix_does_not_skip_plain_tentacool(self) -> None:
        result = self.classify(
            (OCRLine("瑪瑙水母", 1.0), OCRLine("dH66/66", 1.0))
        )
        self.assertTrue(result.is_default)
        self.assertEqual(result.species, "瑪瑙水母")

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

    def test_joined_species_and_iv_digits_is_custom_without_reconstructing_name(self) -> None:
        result = self.classify((OCRLine("炭小侍151513", 0.99),))

        self.assertFalse(result.is_default)
        self.assertEqual(result.species, "炭小侍")
        self.assertEqual(result.evidence, ("炭小侍151513",))

    def test_occluded_title_row_uses_the_shifted_fallback_crop(self) -> None:
        standard_row = (OCRLine("dH66/66", 1.0),)
        occluded_row = (OCRLine("光蚪仔", 1.0),)
        with (
            patch.object(
                local_ocr_v3,
                "rotate_mcp_image_upright",
                return_value=Image.new("RGB", (1366, 1024)),
            ),
            patch.object(
                local_ocr_v3,
                "ocr_image",
                side_effect=(standard_row, occluded_row),
            ),
        ):
            result = local_ocr_v3.analyze_name_region("unused", "unused")

        self.assertTrue(result.is_default)
        self.assertEqual(result.species, "光蚪仔")


if __name__ == "__main__":
    unittest.main()
