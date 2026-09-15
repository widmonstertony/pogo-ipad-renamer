import unittest
from unittest.mock import patch

from PIL import Image

from pogo_iphone_renamer import name_recognition as names
from pogo_iphone_renamer.local_ocr import OCRLine


class DefaultAnnotationVetoTests(unittest.TestCase):
    def classify(self, full):
        # The narrow crop dropped the existing 6/0/6 and superscript 27.
        crop = (OCRLine("火狐狸", .999), OCRLine("11/11 HP", .99))
        with (
            patch.object(names, "rotate_mcp_image_upright", return_value=Image.new("RGB", (1024, 1366))),
            patch.object(names, "ocr_image", side_effect=(crop, tuple(full))),
        ):
            return names.analyze_name_region("unused", "unused")

    def test_full_frame_joined_annotation_vetoes_a_plain_crop(self):
        title = OCRLine("火狐狸606", .96935, (328, 653, 653, 717))
        result = self.classify((title, OCRLine("27", .99, (652, 655, 695, 687))))
        self.assertFalse(result.is_default)
        self.assertEqual(result.species, "火狐狸")
        self.assertIn(title.text, result.evidence)

    def test_single_positioned_percentage_or_unicode_value_is_enough_to_veto(self):
        for text in ("27", "²⁷", "❻"):
            with self.subTest(text=text):
                self.assertFalse(self.classify((OCRLine(text, .99, (652, 655, 695, 687)),)).is_default)

    def test_numbers_outside_title_and_hp_do_not_veto_default_name(self):
        full = (
            OCRLine("CP12", .99, (420, 60, 600, 100)),
            OCRLine("2026", .99, (920, 577, 999, 640)),
            OCRLine("27", .99, (500, 1000, 600, 1100)),
            OCRLine("11/11 HP", .99, (450, 748, 570, 777)),
            OCRLine("火狐狸", .99, (328, 653, 530, 717)),
        )
        self.assertTrue(self.classify(full).is_default)


if __name__ == "__main__":
    unittest.main()
