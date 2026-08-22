from __future__ import annotations

import unittest
from unittest.mock import patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.ipad_landscape_agent_v24 import (
    AppraisalMeasurementUnavailable,
    _READ_ONLY_RETRY_LIMIT,
    _navigate_with_read_only_measurement_retry,
)
from pogo_iphone_renamer.landscape_cv import IVMeasurement


class StableFrameRetryTests(unittest.TestCase):
    def test_retries_reads_without_repeating_navigation(self) -> None:
        stable = Snapshot("", "image")
        measurement = IVMeasurement(3, 0, 0, 0.98, (1, 2, 3), (4, 5, 6))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24._BASE_ORIGINAL_NAVIGATE",
            side_effect=ValueError("transition"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.base._next_snapshot",
            return_value=stable,
        ) as read, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.base.measure_ipad14_6_appraisal",
            return_value=measurement,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.v14.snapshot_is_black",
            return_value=False,
        ):
            returned = _navigate_with_read_only_measurement_retry(object(), Snapshot("", "old"))
        self.assertEqual(returned, (stable, measurement))
        read.assert_called_once_with(unittest.mock.ANY, 1.5)

    def test_stable_dialogue_is_advanced_exactly_once(self) -> None:
        dialogue = Snapshot("", "dialogue")
        bars = Snapshot("", "bars")
        measurement = IVMeasurement(3, 0, 0, 0.98, (1, 2, 3), (4, 5, 6))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24._BASE_ORIGINAL_NAVIGATE",
            side_effect=ValueError("dialogue"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.base._next_snapshot",
            side_effect=[dialogue, bars],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.base.measure_ipad14_6_appraisal",
            side_effect=[ValueError("no bars"), measurement],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.base._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.v14.snapshot_is_black",
            return_value=False,
        ):
            returned = _navigate_with_read_only_measurement_retry(
                object(), Snapshot("", "old")
            )

        self.assertEqual(returned, (bars, measurement))
        tap.assert_called_once_with(unittest.mock.ANY, "APPRAISAL_DIALOG")

    def test_exhaustion_returns_typed_error_with_last_snapshot(self) -> None:
        last = Snapshot("", "still-transitioning")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24._BASE_ORIGINAL_NAVIGATE",
            side_effect=ValueError("transition"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.base._next_snapshot",
            return_value=last,
        ) as read, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.base.measure_ipad14_6_appraisal",
            side_effect=ValueError("no tracks"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.base._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.v14.snapshot_is_black",
            return_value=False,
        ):
            with self.assertRaises(AppraisalMeasurementUnavailable) as raised:
                _navigate_with_read_only_measurement_retry(object(), Snapshot("", "old"))

        self.assertIs(raised.exception.snapshot, last)
        self.assertEqual(read.call_count, _READ_ONLY_RETRY_LIMIT + 1)
        tap.assert_called_once_with(unittest.mock.ANY, "APPRAISAL_DIALOG")

    def test_black_retry_recovers_capture_before_pixel_measurement(self) -> None:
        black = Snapshot("", "black")
        stable = Snapshot("", "stable")
        measurement = IVMeasurement(5, 0, 6, 0.96, (1, 2, 3), (4, 5, 6))
        proxy = object()
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24._BASE_ORIGINAL_NAVIGATE",
            side_effect=ValueError("transition"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.base._next_snapshot",
            return_value=black,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.v14.snapshot_is_black",
            return_value=True,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.wait_for_capture_channel",
            return_value=stable,
        ) as recover, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v24.base.measure_ipad14_6_appraisal",
            return_value=measurement,
        ):
            returned = _navigate_with_read_only_measurement_retry(
                proxy, Snapshot("", "old")
            )

        self.assertEqual(returned, (stable, measurement))
        recover.assert_called_once_with(proxy, black, allow_game_restart=False)


if __name__ == "__main__":
    unittest.main()
