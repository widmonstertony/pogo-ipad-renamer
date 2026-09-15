import unittest
from unittest.mock import patch
from PIL import Image
from pogo_iphone_renamer import name_recognition as ocr
from pogo_iphone_renamer.local_ocr import OCRLine


class FragmentedDefaultTitleTests(unittest.TestCase):
    def classify(self, fragments, full):
        crop = tuple(OCRLine(t, c) for t, c in fragments) + (OCRLine("96 / 96 HP", .99),)
        with patch.object(ocr, "rotate_mcp_image_upright", return_value=Image.new("RGB", (1024, 1366))), patch.object(ocr, "ocr_image", side_effect=(crop, (), tuple(full))):
            return ocr.analyze_name_region("unused", "unused")

    def test_exact_whole_title_corroborates_overlapping_fragments(self):
        result = self.classify((("禿鷹丫", .99), ("丫頭", .99)), (OCRLine("禿鷹丫頭", .99, (404, 653, 621, 715)),))
        self.assertTrue(result.is_default)
        self.assertEqual(result.species, "禿鷹丫頭")
        self.assertEqual(result.evidence, ("禿鷹丫頭",))

    def test_annotations_partial_names_bad_location_and_conflict_never_authorize_rename(self):
        fragments = (("禿鷹丫", .99), ("丫頭", .99))
        exact = OCRLine("禿鷹丫頭", .99, (404, 653, 621, 715))
        for parts, full in (
            (fragments + (("15", .6),), (exact,)),
            (fragments, (exact, OCRLine("15", .6, (630, 654, 658, 711)))),
            ((("禿鷹", .99), ("禿鷹", .99)), (exact,)),
            (fragments, (OCRLine("禿鷹丫頭", .97, exact.bounds),)),
            (fragments, (OCRLine("禿鷹丫頭", .99, (404, 1053, 621, 1115)),)),
            (fragments + (("我的", .99),), (exact,)),
            (fragments, (exact, OCRLine("皮卡丘", .99, (630, 654, 758, 711)))),
        ):
            with self.subTest(parts=parts, full=full):
                self.assertFalse(self.classify(parts, full).is_default)
