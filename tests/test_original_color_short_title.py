import unittest
from unittest.mock import patch

from PIL import Image

from pogo_iphone_renamer import local_ocr_v3 as names
from pogo_iphone_renamer.ipad_landscape_batch_agent_v26 import _detail_name_key
from pogo_iphone_renamer.local_ocr import OCRLine


class OriginalColorShortTitleTests(unittest.TestCase):
    def setUp(self):
        self.image = Image.new("RGB", (1024, 1366))
        self.full = (
            OCRLine("長尾火72B", .76642, (326, 652, 657, 719)),
            OCRLine("53", 1, (653, 655, 694, 687)),
            OCRLine("63 / 63 HP", .89621, (458, 754, 566, 781)),
        )
        self.native = (
            OCRLine("長尾火", .99909, (164, 42, 393, 128)),
            OCRLine("2", .99761, (391, 62, 440, 115)),
            OCRLine("15", .99644, (431, 50, 502, 114)),
            OCRLine("53", 1, (496, 52, 542, 94)),
        )

    def classify(self, lines=None, full=None):
        with patch.object(names, "ocr_image", return_value=self.native if lines is None else lines):
            return names._preserved_short_title_from_original_crop(
                self.image, self.full if full is None else full, {"長尾火狐", "火狐狸"}
            )

    def test_recorded_merged_title_is_preserved_without_expanding_or_reconstructing(self):
        result = self.classify()
        self.assertIsNone(result.species)
        self.assertFalse(result.is_default)
        self.assertEqual(result.evidence, ("長尾火", "2", "15", "53", "63 / 63 HP"))
        self.assertEqual(_detail_name_key(result)[0], "custom")

    def test_default_unknown_stem_extras_and_insufficient_annotations_are_rejected(self):
        for stem in ("火狐狸", "無名氏"):
            with self.subTest(stem=stem):
                self.assertIsNone(self.classify((OCRLine(stem, .999, self.native[0].bounds), *self.native[1:])))
        self.assertIsNone(self.classify(self.native[:3]))
        self.assertIsNone(self.classify((*self.native, OCRLine("我的", .5, (20, 20, 60, 40)))))
        self.assertIsNone(self.classify((OCRLine("長尾火", .97, self.native[0].bounds), *self.native[1:])))
        self.assertIsNone(self.classify((self.native[0], OCRLine("2", .94, self.native[1].bounds), *self.native[2:])))

    def test_independent_positioned_hp_and_matching_title_annotation_are_required(self):
        for full in (self.full[:2], self.full[2:],
                     (self.full[0], OCRLine("53", 1, (653, 1000, 694, 1032)), self.full[2]),
                     (self.full[0], OCRLine("99", 1, self.full[1].bounds), self.full[2]),
                     (self.full[0], self.full[1], OCRLine("63 / 63 HP", .99, None))):
            with self.subTest(full=full):
                self.assertIsNone(self.classify(full=full))

    def test_fallback_integration_after_enhanced_crop_loses_title(self):
        enhanced = (OCRLine("長尾火0②1", .80939), OCRLine("53", 1), OCRLine("63 / 63 HP", .88016))
        with (
            patch.object(names, "rotate_mcp_image_upright", return_value=self.image),
            patch.object(names, "ocr_image", side_effect=(enhanced, (), self.full, self.native)),
        ):
            result = names.analyze_name_region("unused", "unused")
        self.assertFalse(result.is_default)
        self.assertIsNotNone(_detail_name_key(result))


if __name__ == "__main__":
    unittest.main()
