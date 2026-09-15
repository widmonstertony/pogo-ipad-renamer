from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from pogo_iphone_renamer import local_ocr_v3
from pogo_iphone_renamer.local_ocr import OCRLine


class NameRegionClassificationTests(unittest.TestCase):
    def test_name_crop_is_limited_to_the_title_row(self) -> None:
        self.assertEqual(local_ocr_v3.NAME_ROW_TOP, 0.44)
        self.assertEqual(local_ocr_v3.NAME_ROW_BOTTOM, 0.57)

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

    def test_status_labels_do_not_make_a_default_name_custom(self) -> None:
        result = self.classify(
            (
                OCRLine("輕飄飄", 0.99),
                OCRLine("亮晶晶寶可夢", 0.98),
                OCRLine("Shiny", 0.98),
                OCRLine("超極巨化", 0.98),
                OCRLine("Dynamax", 0.98),
                OCRLine("最佳夥伴", 0.98),
                OCRLine("Best Buddy Ribbon", 0.98),
                OCRLine("95 / 95 HP", 0.96),
            )
        )

        self.assertTrue(result.is_default)
        self.assertEqual(result.species, "輕飄飄")

    def test_unknown_text_next_to_species_remains_a_custom_nickname(self) -> None:
        result = self.classify((OCRLine("輕飄飄", 0.99), OCRLine("我的寶可夢", 0.99)))

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
        full_frame = (
            OCRLine("光蚪仔", 1.0, (500, 400, 680, 450)),
            OCRLine("66/66 HP", 1.0, (540, 485, 710, 520)),
        )
        with (
            patch.object(
                local_ocr_v3,
                "rotate_mcp_image_upright",
                return_value=Image.new("RGB", (1366, 1024)),
            ),
            patch.object(
                local_ocr_v3,
                "ocr_image",
                side_effect=(standard_row, occluded_row, full_frame),
            ),
        ):
            result = local_ocr_v3.analyze_name_region("unused", "unused")

        self.assertTrue(result.is_default)
        self.assertEqual(result.species, "光蚪仔")

    def test_evolved_shortened_name_with_merged_digits_is_preserved(self) -> None:
        from pogo_iphone_renamer.ipad_landscape_batch_agent_v26 import _detail_name_key
        crop = tuple(OCRLine(text, 0.99) for text in ("勇士雄", "1110", "71", "11", "151 / 151 HP"))
        for bounds, suffix, accepted in (
            ((327, 652, 660, 716), "11011", True),
            ((327, 1090, 660, 1130), "11011", False),
            ((327, 652, 660, 716), "的糖果", False),
            (None, "11011", False),
        ):
            with self.subTest(bounds=bounds, suffix=suffix), patch.object(
                local_ocr_v3, "rotate_mcp_image_upright", return_value=Image.new("RGB", (1024, 1366))
            ), patch.object(local_ocr_v3, "ocr_image", side_effect=(crop, (), (
                OCRLine("勇士雄" + suffix, 0.99, bounds),
                OCRLine("毛頭小鷹的糖果", 0.99, (450, 1090, 620, 1130)),
            ))):
                result = local_ocr_v3.analyze_name_region("unused", "unused")
                self.assertFalse(result.is_default)
                self.assertIsNone(result.species)
                self.assertEqual(_detail_name_key(result) is not None, accepted)


if __name__ == "__main__":
    unittest.main()
