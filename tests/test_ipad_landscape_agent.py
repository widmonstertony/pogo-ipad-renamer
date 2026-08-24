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

    def test_complete_pixel_rename_dialog_beats_map_fallback(self) -> None:
        snapshot = Snapshot(text="unrelated desktop", image="rename-dialog")
        lines = (
            unittest.mock.Mock(text="設定暱稱", confidence=0.99),
            unittest.mock.Mock(text="OK", confidence=0.99),
            unittest.mock.Mock(text="取消", confidence=0.99),
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent.measure_ipad14_6_appraisal",
            side_effect=ValueError("not appraisal"),
        ), patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=lines,
        ):
            self.assertEqual(local_page_state(snapshot), "RENAME_DIALOG")

    def test_occluded_multitasking_detail_remains_a_detail(self) -> None:
        snapshot = Snapshot(text="unrelated adjacent-window text", image="detail")
        lines = (
            unittest.mock.Mock(text="光蚪仔", confidence=0.99),
            unittest.mock.Mock(text="dH66/66", confidence=0.99),
            unittest.mock.Mock(text="0.49kg", confidence=0.99),
            unittest.mock.Mock(text="強化", confidence=0.99),
            unittest.mock.Mock(text="進化", confidence=0.99),
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent.measure_ipad14_6_appraisal",
            side_effect=ValueError("not appraisal"),
        ), patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=lines,
        ):
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

    def test_missing_appraisal_capture_is_a_retryable_read_error(self) -> None:
        detail = Snapshot(text="CP713 95/95HP 31.22kg 1.26m", image="detail")
        missing_capture = Snapshot(text="", image=None)
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
            side_effect=[detail, missing_capture],
        ):
            with self.assertRaisesRegex(ValueError, "鉴定页截图缺失"):
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
