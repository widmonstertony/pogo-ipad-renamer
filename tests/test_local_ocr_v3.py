from __future__ import annotations

import re
import unittest

from pogo_iphone_renamer.local_ocr_v3 import HP_LINE, NUMBER_TOKEN


class NameAnnotationEvidenceTests(unittest.TestCase):
    def test_iv_and_percentage_tokens_are_annotation_evidence(self) -> None:
        tokens = ["15", "14", "14", "96"]
        self.assertTrue(all(NUMBER_TOKEN.fullmatch(token) for token in tokens))

    def test_normal_hp_line_is_not_annotation_evidence(self) -> None:
        self.assertTrue(HP_LINE.fullmatch("95 / 95 HP"))
        self.assertTrue(HP_LINE.fullmatch("108/108H"))
        self.assertFalse(NUMBER_TOKEN.fullmatch("95 / 95 HP"))


if __name__ == "__main__":
    unittest.main()
