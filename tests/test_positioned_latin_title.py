from __future__ import annotations

import unittest
from unittest.mock import patch

from PIL import Image

from pogo_iphone_renamer import local_ocr_v3 as names
from pogo_iphone_renamer.local_ocr import OCRLine


class PositionedLatinTitleTests(unittest.TestCase):
    def classify(self, title, *, candy=True, hp=True, extras=()):
        # Recorded failure: the crop retained only flipped IV fragments,
        # while full-frame OCR correctly located the existing English name.
        crop = (OCRLine("(+)ε6", .96), OCRLine("15", .99), OCRLine("133 / 133 HP", .99))
        full = [title, *extras]
        if candy:
            full.append(OCRLine("勾帕路翁的糖果", .99932, (455, 1090, 611, 1117)))
        if hp:
            full.append(OCRLine("133 / 133 HP", .94871, (446, 754, 577, 781)))
        with (
            patch.object(names, "rotate_mcp_image_upright", return_value=Image.new("RGB", (1024, 1366))),
            patch.object(names, "ocr_image", side_effect=(crop, (), tuple(full))),
        ):
            return names.analyze_name_region("unused", "unused")

    def test_positioned_full_title_recovers_a_custom_name_without_guessing_unicode(self):
        title = OCRLine("Cobali12 15 15 93(+)", .94039, (302, 644, 722, 721))
        result = self.classify(title)
        self.assertEqual(result.species, "勾帕路翁")
        self.assertFalse(result.is_default)
        self.assertEqual(result.evidence, (title.text,))

    def test_unlocated_out_of_row_or_low_confidence_english_is_not_enough(self):
        for confidence, bounds in ((.99, None), (.99, (302, 1044, 722, 1121)), (.89, (302, 644, 722, 721))):
            with self.subTest(confidence=confidence, bounds=bounds):
                self.assertIsNone(self.classify(OCRLine("Cobali", confidence, bounds)).species)

    def test_candy_and_hp_are_required(self):
        title = OCRLine("Cobali", .99, (302, 644, 722, 721))
        for candy, hp in ((False, True), (True, False)):
            with self.subTest(candy=candy, hp=hp):
                self.assertIsNone(self.classify(title, candy=candy, hp=hp).species)

    def test_status_badges_and_ambiguous_latin_rows_are_not_custom_name_proof(self):
        bounds = (302, 644, 722, 721)
        for label in ("Shiny", "Lucky", "Dynamax", "Gigantamax"):
            with self.subTest(label=label):
                self.assertIsNone(self.classify(OCRLine(label, .99, bounds)).species)
        result = self.classify(OCRLine("Cobali", .99, bounds), extras=(OCRLine("Other", .99, bounds),))
        self.assertIsNone(result.species)


if __name__ == "__main__":
    unittest.main()
