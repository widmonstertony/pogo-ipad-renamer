from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.batch_navigation_v26 import DetailFingerprint
from pogo_iphone_renamer.batch_pause import BatchPauseFile
from pogo_iphone_renamer.ipad_landscape_agent_v24 import (
    AppraisalMeasurementUnavailable,
)
from pogo_iphone_renamer.ipad_landscape_agent_v22 import (
    RenameFieldVerificationUnavailable,
)
from pogo_iphone_renamer.ipad_landscape_agent_v16 import (
    RenamePencilLocalizationUnavailable,
)
from pogo_iphone_renamer.ipad_landscape_batch_agent_v26 import (
    _close_appraisal,
    _confirm_low_confidence_measurement,
    _ensure_game_foreground,
    _process_one,
    _wait_at_safe_pause_boundary,
)
from pogo_iphone_renamer.landscape_cv import IVMeasurement
from pogo_iphone_renamer.local_ocr_v3 import NameRegionResult
from pogo_iphone_renamer.policy import Observation, PolicyViolation


class BatchUnreadableAppraisalTests(unittest.TestCase):
    def test_manual_unlock_home_launches_only_configured_game(self) -> None:
        snapshot = Snapshot("SpringBoard", "home")

        class Proxy:
            settings = SimpleNamespace(
                pokemon_go_bundle_id="com.nianticlabs.pokemongo"
            )
            observation = Observation("home-token", 0.0, "SpringBoard", 1366, 1024)

            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            def call_tool(self, name: str, arguments: dict) -> dict:
                self.calls.append((name, arguments))
                if name == "get_frontmost_app":
                    return {
                        "content": [
                            {"type": "text", "text": "com.apple.springboard"}
                        ]
                    }
                if name == "launch_app":
                    self.observation = Observation(
                        "game-token",
                        0.0,
                        "com.nianticlabs.pokemongo",
                        1366,
                        1024,
                    )
                return {}

        proxy = Proxy()
        game = Snapshot("Pokemon GO HP kg", "game")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=game,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.wait_for_capture_channel",
            return_value=game,
        ):
            result = _ensure_game_foreground(proxy, snapshot)

        self.assertIs(result, game)
        self.assertEqual(
            [name for name, _ in proxy.calls],
            ["get_frontmost_app", "launch_app"],
        )
        self.assertEqual(
            proxy.calls[1][1]["bundle_id"], "com.nianticlabs.pokemongo"
        )
        self.assertEqual(proxy.calls[1][1]["_observation_token"], "home-token")

    def test_close_appraisal_recovers_black_capture_before_validation(self) -> None:
        black = Snapshot("", "black")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        proxy = object()
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=black,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.snapshot_is_black",
            return_value=True,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.wait_for_capture_channel",
            return_value=detail,
        ) as recover, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ) as validate:
            returned = _close_appraisal(proxy)

        self.assertIs(returned, detail)
        tap.assert_called_once_with(proxy, "APPRAISAL_CLOSE")
        recover.assert_called_once_with(proxy, black, allow_game_restart=False)
        validate.assert_called_once_with("DETAIL", detail)

    def test_close_appraisal_does_not_leak_raw_measurement_error(self) -> None:
        unknown = Snapshot("", "unknown")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._tap"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=unknown,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.snapshot_is_black",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected",
            side_effect=PolicyViolation("not detail"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.measure_ipad14_6_appraisal",
            side_effect=ValueError("no tracks"),
        ):
            with self.assertRaises(PolicyViolation) as raised:
                _close_appraisal(object())

        self.assertNotIsInstance(raised.exception, ValueError)
        self.assertIn("未重复点击", str(raised.exception))

    def test_pause_waits_without_phone_io_then_refreshes_same_detail(self) -> None:
        counts = {"renamed": 2, "skipped": 3, "scanned": 0, "unreadable": 1}
        fingerprint = DetailFingerprint(("皮卡丘",), "cp1", "1/1hp", "1kg", "1m")
        detail = Snapshot("CP1 1/1HP 1kg 1m", "detail")
        with tempfile.TemporaryDirectory() as directory:
            pause = BatchPauseFile(Path(directory) / "batch.pause")
            pause.request()

            def resume_after_one_poll(_seconds: float) -> None:
                pause.resume()

            with patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.time.sleep",
                side_effect=resume_after_one_poll,
            ), patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.screen_snapshot",
                return_value=detail,
            ) as refresh, patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
            ), patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.detail_fingerprint",
                return_value=fingerprint,
            ), patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._emit_progress"
            ) as progress, patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
            ):
                returned = _wait_at_safe_pause_boundary(
                    object(),
                    detail,
                    fingerprint=fingerprint,
                    index=6,
                    limit=0,
                    counts=counts,
                    pause=pause,
                )

        self.assertIs(returned, detail)
        refresh.assert_called_once()
        self.assertEqual(
            [item.kwargs["phase"] for item in progress.call_args_list],
            ["paused", "resumed"],
        )

    def test_unreadable_appraisal_is_preserved_and_skipped(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        error = AppraisalMeasurementUnavailable(
            appraisal, ValueError("no appraisal tracks")
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            side_effect=error,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ) as close, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned, outcome = _process_one(
                object(), Snapshot("", "before"), mode="rename", index=10
            )

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "unreadable")
        close.assert_called_once()
        self.assertIn("继续下一只", emit.call_args.kwargs["message"])

    def test_low_confidence_measurement_preserves_one_and_continues(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        measurement = IVMeasurement(
            8, 4, 8, 0.881, (1, 2, 3), (4, 5, 6)
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ) as close, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region"
        ) as analyze_name, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned, outcome = _process_one(
                object(), Snapshot("", "before"), mode="rename", index=1
            )

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "unreadable")
        close.assert_called_once()
        analyze_name.assert_not_called()
        self.assertIn("继续下一只", emit.call_args.kwargs["message"])

    def test_low_confidence_measurement_accepts_three_matching_frames(self) -> None:
        first_snapshot = Snapshot("", "first")
        second_snapshot = Snapshot("", "second")
        third_snapshot = Snapshot("", "third")
        first = IVMeasurement(12, 4, 3, 0.881, (1, 2, 3), (4, 5, 6))
        second = IVMeasurement(12, 4, 3, 0.864, (1, 2, 3), (4, 5, 6))
        third = IVMeasurement(12, 4, 3, 0.887, (1, 2, 3), (4, 5, 6))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=[second_snapshot, third_snapshot],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.snapshot_is_black",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.measure_ipad14_6_appraisal",
            side_effect=[second, third],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned = _confirm_low_confidence_measurement(
                object(), first_snapshot, first
            )

        self.assertEqual(returned, (third_snapshot, third))
        self.assertIn("多帧 IV 一致确认", emit.call_args.kwargs["message"])

    def test_existing_iv_nickname_never_opens_rename_dialog(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        measurement = IVMeasurement(15, 14, 14, 0.99, (1, 2, 3), (4, 5, 6))
        existing = NameRegionResult(
            species="輕飄飄",
            is_default=False,
            confidence=0.99,
            evidence=("輕飄飄", "15", "14", "14", "96"),
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=existing,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.open_dynamic_rename_from_detail"
        ) as open_dialog, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._commit_after_dismissing_keyboard"
        ) as commit, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned, outcome = _process_one(
                object(), Snapshot("", "before"), mode="rename", index=3
            )

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "skipped")
        open_dialog.assert_not_called()
        commit.assert_not_called()

    def test_unreadable_name_boundary_preserves_one_and_continues(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        measurement = IVMeasurement(0, 1, 3, 0.97, (1, 2, 3), (4, 5, 6))
        default = NameRegionResult(
            species="可達鴨",
            is_default=True,
            confidence=0.99,
            evidence=("可達鴨",),
        )
        error = RenamePencilLocalizationUnavailable(
            detail, PolicyViolation("empty OCR")
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=default,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.open_dynamic_rename_from_detail",
            side_effect=error,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._commit_after_dismissing_keyboard"
        ) as commit, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ) as validate, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned, outcome = _process_one(
                object(), Snapshot("", "before"), mode="rename", index=7
            )

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "unreadable")
        validate.assert_called_once_with("DETAIL", detail)
        commit.assert_not_called()
        self.assertIn("继续下一只", emit.call_args.kwargs["message"])

    def test_unverifiable_typed_field_is_preserved_and_continues(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        measurement = IVMeasurement(6, 2, 12, 0.97, (1, 2, 3), (4, 5, 6))
        default = NameRegionResult(
            species="滑滑小子",
            is_default=True,
            confidence=0.99,
            evidence=("滑滑小子",),
        )
        error = RenameFieldVerificationUnavailable(detail, "")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=default,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.open_dynamic_rename_from_detail"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._commit_after_dismissing_keyboard",
            side_effect=error,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ) as validate, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned, outcome = _process_one(
                object(), Snapshot("", "before"), mode="rename", index=1
            )

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "unreadable")
        validate.assert_called_once_with("DETAIL", detail)
        self.assertIn("继续下一只", emit.call_args.kwargs["message"])


if __name__ == "__main__":
    unittest.main()
