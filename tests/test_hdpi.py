from __future__ import annotations

import unittest

from pogo_iphone_renamer.gui_hdpi import scale_for_display


class HighDpiTests(unittest.TestCase):
    def test_4k_at_100_percent_still_uses_200_percent(self) -> None:
        self.assertEqual(scale_for_display(3840, 2160, 96), 2.0)

    def test_4k_respects_larger_system_dpi(self) -> None:
        self.assertEqual(scale_for_display(3840, 2160, 240), 2.5)

    def test_full_hd_keeps_system_scale(self) -> None:
        self.assertEqual(scale_for_display(1920, 1080, 144), 1.5)

    def test_scale_is_bounded_for_ultrawide(self) -> None:
        self.assertEqual(scale_for_display(7680, 2160, 96), 2.0)


if __name__ == "__main__":
    unittest.main()

