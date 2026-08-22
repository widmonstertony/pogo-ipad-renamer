from __future__ import annotations

import unittest
from unittest.mock import patch

from pogo_iphone_renamer.keyboard_control_v22 import (
    dismiss_active_keyboard,
    exact_accessibility_tap_point,
)
from pogo_iphone_renamer.policy import Observation, PolicyViolation


class _Proxy:
    def __init__(self) -> None:
        self.observation = Observation("token", 0, "", 1024, 1366)


class KeyboardControlTests(unittest.TestCase):
    def test_stage_manager_does_not_use_keyboard_local_coordinates(self) -> None:
        proxy = _Proxy()
        with patch.object(proxy, "call_tool", create=True) as call_tool:
            self.assertFalse(dismiss_active_keyboard(proxy))
        call_tool.assert_not_called()

    def test_exact_keyboard_dismiss_point(self) -> None:
        with patch(
            "pogo_iphone_renamer.keyboard_control_v22._all_elements",
            return_value=[
                {
                    "text": "收起键盘",
                    "clickable": True,
                    "tap": {"x": 950.5, "y": 56.5},
                }
            ],
        ):
            self.assertEqual(
                exact_accessibility_tap_point(_Proxy(), "收起键盘"),
                (950.5, 56.5),
            )

    def test_duplicate_control_is_rejected(self) -> None:
        element = {
            "text": "收起键盘",
            "clickable": True,
            "tap": {"x": 950.5, "y": 56.5},
        }
        with patch(
            "pogo_iphone_renamer.keyboard_control_v22._all_elements",
            return_value=[element, dict(element)],
        ):
            with self.assertRaises(PolicyViolation):
                exact_accessibility_tap_point(_Proxy(), "收起键盘")


if __name__ == "__main__":
    unittest.main()
