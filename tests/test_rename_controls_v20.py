from __future__ import annotations

import unittest

from pogo_iphone_renamer.local_ocr_v4 import LocatedText, OCRTextBox
from pogo_iphone_renamer.policy import PolicyViolation
from pogo_iphone_renamer.rename_controls_v20 import scaled_text_center


class RenameControlMappingTests(unittest.TestCase):
    def test_ok_center_scales_to_mcp_touch_space(self) -> None:
        located = LocatedText(
            OCRTextBox("OK", 0.99, 655, 555, 711, 595), 1366, 1024
        )
        x, y = scaled_text_center(
            located,
            observation_width=1024,
            observation_height=1366,
            x_range=(0.36, 0.64),
            y_range=(0.48, 0.64),
        )
        self.assertAlmostEqual(x, 512.0, places=1)
        self.assertAlmostEqual(y, 767.0, delta=2.0)

    def test_cancel_center_scales_to_lower_control(self) -> None:
        located = LocatedText(
            OCRTextBox("取消", 0.99, 650, 680, 716, 710), 1366, 1024
        )
        _, y = scaled_text_center(
            located,
            observation_width=1024,
            observation_height=1366,
            x_range=(0.36, 0.64),
            y_range=(0.60, 0.76),
        )
        self.assertAlmostEqual(y, 927.0, delta=2.0)

    def test_rejects_button_outside_safe_region(self) -> None:
        located = LocatedText(
            OCRTextBox("OK", 0.99, 50, 50, 100, 90), 1366, 1024
        )
        with self.assertRaises(PolicyViolation):
            scaled_text_center(
                located,
                observation_width=1024,
                observation_height=1366,
                x_range=(0.36, 0.64),
                y_range=(0.48, 0.64),
            )


if __name__ == "__main__":
    unittest.main()
