import unittest
from unittest.mock import patch

from PIL import Image

from pogo_iphone_renamer import name_recognition as names
from pogo_iphone_renamer.local_ocr import OCRLine
from pogo_iphone_renamer.batch_agent import _detail_name_key


class PositionedMetadataTitleTests(unittest.TestCase):
    width, height = 1024, 1366
    title = OCRLine("火狐狸", .999, (404, 654, 620, 715))
    health = OCRLine("46 / 46 HP", .99, (452, 750, 575, 780))
    gender = OCRLine("Q", .99, (810, 749, 842, 777))

    def crop_line(self, line):
        l, t, r, b = line.bounds
        x, y = round(self.width * names.NAME_ROW_LEFT), round(self.height * names.NAME_ROW_TOP)
        return OCRLine(line.text, line.confidence, (2*(l-x), 2*(t-y), 2*(r-x), 2*(b-y)))

    def classify(self, *, crop_extras=(), full_extras=(), full=None, crop=None):
        if crop is None:
            crop = tuple(map(self.crop_line, (
                self.title, self.gender,
                OCRLine("46", .99, (452, 750, 482, 780)),
                OCRLine("46 HP", .99, (500, 750, 575, 780)),
                *crop_extras,
            )))
        if full is None:
            full = (self.title, self.health, self.gender, *full_extras)
        with patch.object(names, "rotate_mcp_image_upright", return_value=Image.new("RGB", (self.width, self.height))), \
                patch.object(names, "ocr_image", side_effect=(crop, full)):
            return names.analyze_name_region("unused", "STAGE_MANAGER_PORTRAIT_WINDOW")

    def test_located_gender_and_split_hp_are_not_custom_name(self):
        result = self.classify()
        self.assertTrue(result.is_default)
        self.assertEqual(result.evidence, ("火狐狸", "46 / 46 HP"))

    def test_cannot_resolve_without_complete_hp_and_whole_title(self):
        for full in ((self.title, self.gender), (self.health, self.gender),
                     (OCRLine("火狐狸", .97, self.title.bounds), self.health, self.gender)):
            with self.subTest(full=full):
                self.assertFalse(self.classify(full=full).is_default)

    def test_every_extra_on_title_row_vetoes_even_at_low_confidence(self):
        for text in ("Q", "46", "⓯", "⁹⁶", "我的"):
            extra = OCRLine(text, .6, (632, 660, 674, 702))
            with self.subTest(text=text, source="full"):
                self.assertFalse(self.classify(full_extras=(extra,)).is_default)
            with self.subTest(text=text, source="crop"):
                self.assertFalse(self.classify(crop_extras=(extra,)).is_default)

    def test_q_on_name_baseline_is_not_ignored(self):
        crop = tuple(map(self.crop_line, (self.title,
            OCRLine("Q", .99, (810, 660, 842, 712)), self.health)))
        self.assertFalse(self.classify(crop=crop).is_default)

    def test_inline_low_gender_icon_from_real_ipad_frame_is_metadata(self):
        inline_gender = OCRLine("Q", .856, (787, 708, 833, 755))
        crop = tuple(map(self.crop_line, (self.title, inline_gender, self.health)))
        result = self.classify(crop=crop, full=(self.title, self.health))
        self.assertTrue(result.is_default)
        self.assertEqual(result.species, "火狐狸")
        self.assertEqual(result.evidence, ("火狐狸", "46 / 46 HP"))

    def test_unlocated_recorded_tokens_are_uncertain_not_an_automatic_skip(self):
        crop = tuple(OCRLine(t, .99) for t in ("火狐狸", "Q", "46", "46 HP"))
        result = self.classify(crop=crop)
        self.assertFalse(result.is_default)
        self.assertIsNone(_detail_name_key(result))

    def test_digit_not_corroborated_by_hp_is_not_dropped(self):
        self.assertFalse(self.classify(crop_extras=(
            OCRLine("15", .99, (484, 750, 499, 780)),
        )).is_default)

    def test_real_iv_annotations_still_preserved(self):
        annotations = (OCRLine("15", .99, (630, 653, 675, 717)),
                       OCRLine("96", .99, (678, 653, 713, 685)))
        result = self.classify(crop_extras=annotations, full_extras=annotations)
        self.assertFalse(result.is_default)
        self.assertEqual(_detail_name_key(result), ("custom", "火狐狸"))


if __name__ == "__main__":
    unittest.main()
