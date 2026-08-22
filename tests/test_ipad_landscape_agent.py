from __future__ import annotations

import json
import unittest
from unittest.mock import call, patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.ipad_landscape_agent import (
    ANCHORS,
    _validate_expected,
    local_page_state,
    navigate_to_appraisal,
)
from pogo_iphone_renamer.policy import PolicyViolation


class IPadLandscapeAgentTests(unittest.TestCase):
    def test_calibrated_anchors_are_normalized(self) -> None:
        for key, (x, y, _label, _expected) in ANCHORS.items():
            with self.subTest(key=key):
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x, 1)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(y, 1)

    def test_inventory_detected_from_capacity(self) -> None:
        snapshot = Snapshot(
            text=json.dumps({"ocr_texts": [{"text": "9987 / 10150"}]}),
            image=None,
        )
        self.assertEqual(local_page_state(snapshot), "INVENTORY")

    def test_detail_detected_from_stats(self) -> None:
        snapshot = Snapshot(text="CP713 95/95HP 31.22kg 1.26m", image=None)
        self.assertEqual(local_page_state(snapshot), "DETAIL")

    def test_appraisal_bars_are_never_accepted_as_plain_detail(self) -> None:
        snapshot = Snapshot(
            text="CP713 95/95HP 31.22kg 1.26m",
            image="visible-appraisal-bars",
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent.measure_ipad14_6_appraisal",
            return_value=object(),
        ):
            with self.assertRaisesRegex(PolicyViolation, "鉴定条仍可见"):
                _validate_expected("DETAIL", snapshot)

    def test_transient_appraisal_measurement_does_not_tap_close_point(self) -> None:
        detail = Snapshot(text="CP713 95/95HP 31.22kg 1.26m", image="detail")
        appraisal = Snapshot(text="", image="appraisal-animation")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent.local_page_state",
            return_value="DETAIL",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent._ensure_stage_geometry_for_state",
            side_effect=lambda _proxy, snapshot, _state: snapshot,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.ipad_landscape_agent._next_snapshot",
            side_effect=[detail, appraisal],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent.measure_ipad14_6_appraisal",
            side_effect=ValueError("transition"),
        ):
            with self.assertRaises(ValueError):
                navigate_to_appraisal(object(), detail)

        self.assertEqual(
            tap.call_args_list,
            [
                call(unittest.mock.ANY, "DETAIL"),
                call(unittest.mock.ANY, "DETAIL_MENU"),
            ],
        )


if __name__ == "__main__":
    unittest.main()
