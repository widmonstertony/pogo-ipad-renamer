from __future__ import annotations

import unittest

from pogo_iphone_renamer.local_ocr import (
    OCRLine,
    exact_species_from_lines,
    rename_dialog_visible,
)
from pogo_iphone_renamer.policy import PolicyViolation


class LocalOCRSafetyTests(unittest.TestCase):
    def test_exact_species_requires_local_traditional_match(self) -> None:
        name, confidence = exact_species_from_lines(
            (OCRLine("CP713", 0.99), OCRLine("輕飄飄", 0.959))
        )
        self.assertEqual(name, "輕飄飄")
        self.assertEqual(confidence, 0.959)

    def test_unknown_or_custom_name_is_rejected(self) -> None:
        with self.assertRaises(PolicyViolation):
            exact_species_from_lines((OCRLine("我的輕飄飄", 0.99),))

    def test_rename_dialog_requires_all_three_controls(self) -> None:
        self.assertTrue(
            rename_dialog_visible(
                (
                    OCRLine("設定暱稱", 0.98),
                    OCRLine("OK", 0.99),
                    OCRLine("取消", 0.99),
                )
            )
        )
        self.assertFalse(
            rename_dialog_visible((OCRLine("設定暱稱", 0.98), OCRLine("OK", 0.99)))
        )

    def test_rename_dialog_accepts_ios_merged_done_cancel_token(self) -> None:
        self.assertTrue(
            rename_dialog_visible(
                (
                    OCRLine("設定暱稱", 0.98),
                    OCRLine("OK", 0.99),
                    OCRLine("完成取消", 1.0),
                )
            )
        )


if __name__ == "__main__":
    unittest.main()
