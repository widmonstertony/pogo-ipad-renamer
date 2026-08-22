from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.deterministic_appraisal_agent import (
    deterministic_navigation,
    ocr_tap,
)
from pogo_iphone_renamer.policy import PolicyViolation


class DeterministicNavigationTests(unittest.TestCase):
    def snapshot(self) -> Snapshot:
        payload = {
            "ocr_texts": [
                {"text": "寶可夢", "tap": {"x": 321, "y": 654}},
                {"text": "傳送", "tap": {"x": 900, "y": 1100}},
                {"text": "鑑定", "tap": {"x": 700, "y": 1000}},
            ]
        }
        return Snapshot(text=json.dumps(payload, ensure_ascii=False), image=None)

    def proxy(self):
        return SimpleNamespace(observation=SimpleNamespace(width=1024.0, height=1366.0))

    def test_ocr_uses_exact_returned_tap_point(self) -> None:
        self.assertEqual(ocr_tap(self.snapshot(), ("寶可夢",)), (321.0, 654.0, "寶可夢"))

    def test_map_coordinate_is_fixed_not_model_coordinate(self) -> None:
        decision = {
            "screen_state": "MAP",
            "content_orientation": "PORTRAIT_UPRIGHT",
            "confidence": 0.99,
            "action": "tap",
            "target_label": "wrong",
            "x": 1,
            "y": 2,
            "expected_after": "wrong",
        }
        resolved = deterministic_navigation(decision, self.snapshot(), self.proxy(), "OPEN_APPRAISAL")
        self.assertEqual(resolved["x"], 512.0)
        self.assertAlmostEqual(resolved["y"], 1222.57)

    def test_rotated_content_is_rejected_before_click(self) -> None:
        decision = {
            "screen_state": "MAP",
            "content_orientation": "ROTATED_90_COUNTERCLOCKWISE",
            "confidence": 0.99,
            "action": "tap",
        }
        with self.assertRaises(PolicyViolation):
            deterministic_navigation(decision, self.snapshot(), self.proxy(), "OPEN_APPRAISAL")


if __name__ == "__main__":
    unittest.main()
