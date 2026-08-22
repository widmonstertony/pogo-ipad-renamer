from __future__ import annotations

import json
import unittest

from pogo_iphone_renamer.gui_deterministic_appraisal import friendly_deterministic_event


class DeterministicGuiLogTests(unittest.TestCase):
    def test_device_is_reported_truthfully(self) -> None:
        line = json.dumps(
            {
                "type": "device",
                "name": "iPad",
                "machine": "iPad14,6",
                "system": "iPadOS",
                "version": "16.1",
                "width": 1024,
                "height": 1366,
            }
        )
        self.assertIn("iPad14,6", friendly_deterministic_event(line) or "")

    def test_rotated_navigation_warns_before_click(self) -> None:
        line = json.dumps(
            {
                "type": "navigation",
                "state": "MAP",
                "orientation": "ROTATED_90_COUNTERCLOCKWISE",
            }
        )
        self.assertIn("不会执行点击", friendly_deterministic_event(line) or "")


if __name__ == "__main__":
    unittest.main()
