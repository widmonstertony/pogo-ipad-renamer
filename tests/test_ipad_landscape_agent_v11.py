from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from pogo_iphone_renamer.ipad_landscape_agent_v11 import _clear_button_center
from pogo_iphone_renamer.policy import PolicyViolation


def proxy_with_elements(elements):
    return SimpleNamespace(
        observation=SimpleNamespace(
            text=json.dumps({"elements": elements}, ensure_ascii=False)
        )
    )


class DynamicClearButtonTests(unittest.TestCase):
    def test_center_is_derived_from_verified_current_frame(self) -> None:
        proxy = proxy_with_elements(
            [
                {
                    "type": "control",
                    "text": "輕飄飄",
                    "rect": {"x": 20, "y": 893, "width": 887, "height": 34},
                },
                {
                    "type": "control",
                    "text": "清除文本",
                    "rect": {"x": 879, "y": 898, "width": 24, "height": 24},
                },
            ]
        )
        self.assertEqual(_clear_button_center(proxy, "輕飄飄"), (891.0, 910.0))

    def test_clear_is_rejected_without_exact_original_field(self) -> None:
        proxy = proxy_with_elements(
            [
                {
                    "type": "control",
                    "text": "清除文本",
                    "rect": {"x": 879, "y": 898, "width": 24, "height": 24},
                }
            ]
        )
        with self.assertRaises(PolicyViolation):
            _clear_button_center(proxy, "輕飄飄")


if __name__ == "__main__":
    unittest.main()
