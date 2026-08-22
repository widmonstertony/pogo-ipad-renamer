from __future__ import annotations

import json
import unittest

from pogo_iphone_renamer.gui_ipad_landscape import friendly_ipad_landscape_event


class IPadLandscapeGuiLogTests(unittest.TestCase):
    def test_rotation_is_a_supported_navigation_state(self) -> None:
        line = json.dumps(
            {
                "type": "navigation",
                "state": "DETAIL",
                "orientation": "ROTATED_90_COUNTERCLOCKWISE",
            }
        )
        message = friendly_ipad_landscape_event(line)
        self.assertIn("详情页", message or "")
        self.assertNotIn("异常", message or "")

    def test_result_shows_both_confidences(self) -> None:
        line = json.dumps(
            {
                "type": "pokemon",
                "species": "輕飄飄",
                "attack": 15,
                "defense": 14,
                "stamina": 14,
                "percent": 96,
                "nickname": "輕飄飄⓯⓮⓮⁹⁶",
                "confidence": 0.9958,
                "name_confidence": 0.9987,
            },
            ensure_ascii=False,
        )
        message = friendly_ipad_landscape_event(line) or ""
        self.assertIn("輕飄飄⓯⓮⓮⁹⁶", message)
        self.assertIn("名称=99.9%", message)


if __name__ == "__main__":
    unittest.main()
