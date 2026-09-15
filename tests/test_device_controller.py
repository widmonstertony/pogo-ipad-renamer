from __future__ import annotations

import json
import unittest
from unittest.mock import call, patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer import device_controller as base
from pogo_iphone_renamer.device_controller import (
    ANCHORS,
    _validate_expected,
    local_page_state,
    navigate_to_appraisal,
)
from pogo_iphone_renamer.policy import PolicyViolation


class IPadLandscapeAgentTests(unittest.TestCase):
    def test_fixed_portrait_stage_manager_profile_needs_no_legacy_geometry(self) -> None:
        previous = base.ORIENTATION
        self.addCleanup(setattr, base, "ORIENTATION", previous)
        base.ORIENTATION = "STAGE_MANAGER_PORTRAIT_WINDOW"
        proxy = object()
        snapshot = Snapshot(text="CP530 91/91 HP", image="detail")

        self.assertIsNone(base.current_stage_geometry(proxy))
        self.assertIs(
            snapshot,
            base._ensure_stage_geometry_for_state(proxy, snapshot, "DETAIL"),
        )
        x, y = base.upright_ratio_to_touch(1366, 1024, 0.78, 0.5)
        visible_x = 1366 * (0.232 + 0.536 * 0.78)
        visible_y = 1024 * (0.049 + 0.93 * 0.5)
        self.assertAlmostEqual((1024 - visible_y) * 1366 / 1024, x)
        self.assertAlmostEqual(visible_x * 1024 / 1366, y)

    def test_portrait_window_close_uses_calibrated_landscape_coordinates(self) -> None:
        previous = base.ORIENTATION
        self.addCleanup(setattr, base, "ORIENTATION", previous)
        base.ORIENTATION = "STAGE_MANAGER_PORTRAIT_WINDOW"
        calls = []

        class Proxy:
            observation = type("Observation", (), {"width": 1366, "height": 1024, "token": "fresh"})()

            def call_tool(self, name, arguments):
                calls.append((name, arguments))

        base._tap(Proxy(), "APPRAISAL_CLOSE")

        self.assertEqual("tap_screen", calls[0][0])
        self.assertAlmostEqual(104.9, calls[0][1]["x"], places=1)
        self.assertAlmostEqual(512.0, calls[0][1]["y"], places=1)

    def test_live_cubchoo_menu_point_preserves_portrait_digitizer_fraction(self):
        with patch.object(base, 'ORIENTATION', 'STAGE_MANAGER_PORTRAIT_WINDOW'), patch.object(
            base, '_PORTRAIT_WINDOW_INPUT_MAPPING', 'digitizer_normalized'
        ):
            # Independently observed portrait screenshot menu center: 96,973.
            game_x = (973 / 1366 - 0.232) / 0.536
            game_y = (928 / 1024 - 0.049) / 0.930
            x, y = base.upright_ratio_to_touch(1366, 1024, game_x, game_y)
            self.assertAlmostEqual(x / 1366, 96 / 1024)
            self.assertAlmostEqual(y / 1024, 973 / 1366)
            start = base.upright_ratio_to_touch(1366, 1024, .8, .5)
            end = base.upright_ratio_to_touch(1366, 1024, .2, .5)
            self.assertEqual(start[0], end[0])
            self.assertGreater(start[1], end[1])

    def test_calibrated_anchors_are_normalized(self) -> None:
        for key, (x, y, _label, _expected) in ANCHORS.items():
            with self.subTest(key=key):
                self.assertGreaterEqual(x, 0)
                self.assertLessEqual(x, 1)
                self.assertGreaterEqual(y, 0)
                self.assertLessEqual(y, 1)

    def test_detail_menu_requires_visible_appraise_control_before_next_tap(self) -> None:
        snapshot = Snapshot(text="still detail", image="detail")
        with patch(
            "pogo_iphone_renamer.device_controller.local_page_state",
            return_value="DETAIL",
        ):
            with self.assertRaisesRegex(PolicyViolation, "没有验证到“鉴定”菜单"):
                _validate_expected("DETAIL_MENU", snapshot)

    def test_appraisal_requires_visible_overlay_before_next_tap(self) -> None:
        snapshot = Snapshot(text="still detail", image="detail")
        with patch(
            "pogo_iphone_renamer.device_controller.local_page_state",
            return_value="DETAIL",
        ):
            with self.assertRaisesRegex(PolicyViolation, "没有验证到鉴定覆盖层"):
                _validate_expected("APPRAISAL", snapshot)

    def test_inventory_detected_from_capacity(self) -> None:
        snapshot = Snapshot(
            text=json.dumps({"ocr_texts": [{"text": "9987 / 10150"}]}),
            image=None,
        )
        self.assertEqual(local_page_state(snapshot), "INVENTORY")

    def test_detail_detected_from_stats(self) -> None:
        snapshot = Snapshot(text="CP713 95/95HP 31.22kg 1.26m", image=None)
        self.assertEqual(local_page_state(snapshot), "DETAIL")

    def test_portrait_window_detail_uses_size_and_action_when_title_is_occluded(self) -> None:
        previous = base.ORIENTATION
        self.addCleanup(setattr, base, "ORIENTATION", previous)
        base.ORIENTATION = "STAGE_MANAGER_PORTRAIT_WINDOW"
        snapshot = Snapshot(text="desktop settings", image="portrait-window-detail")
        lines = (
            unittest.mock.Mock(text="29.32kg", confidence=0.99),
            unittest.mock.Mock(text="0.91m", confidence=0.99),
            unittest.mock.Mock(text="強化", confidence=0.99),
        )
        with patch(
            "pogo_iphone_renamer.device_controller.measure_ipad14_6_appraisal",
            side_effect=ValueError("not appraisal"),
        ), patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=lines,
        ):
            self.assertEqual(local_page_state(snapshot), "DETAIL")

    def test_complete_pixel_rename_dialog_beats_map_fallback(self) -> None:
        snapshot = Snapshot(text="unrelated desktop", image="rename-dialog")
        lines = (
            unittest.mock.Mock(text="設定暱稱", confidence=0.99),
            unittest.mock.Mock(text="OK", confidence=0.99),
            unittest.mock.Mock(text="取消", confidence=0.99),
        )
        with patch(
            "pogo_iphone_renamer.device_controller.measure_ipad14_6_appraisal",
            side_effect=ValueError("not appraisal"),
        ), patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=lines,
        ):
            self.assertEqual(local_page_state(snapshot), "RENAME_DIALOG")

    def test_occluded_detail_menu_is_not_mislabeled_as_map(self) -> None:
        snapshot = Snapshot(text="unrelated desktop", image="detail-menu")
        lines = (
            unittest.mock.Mock(text="道具", confidence=0.99),
            unittest.mock.Mock(text="調查寶可夢", confidence=0.99),
            unittest.mock.Mock(text="傳送", confidence=0.99),
        )
        with patch(
            "pogo_iphone_renamer.device_controller.measure_ipad14_6_appraisal",
            side_effect=ValueError("not appraisal"),
        ), patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=lines,
        ):
            self.assertEqual(local_page_state(snapshot), "DETAIL_MENU")

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
            "pogo_iphone_renamer.device_controller.measure_ipad14_6_appraisal",
            side_effect=ValueError("not appraisal"),
        ), patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=lines,
        ):
            self.assertEqual(local_page_state(snapshot), "DETAIL")

    def test_merged_iv_nickname_remains_a_detail_but_not_a_default_name(self) -> None:
        snapshot = Snapshot(text="unrelated adjacent-window text", image="detail")
        lines = (
            unittest.mock.Mock(text="光蚪仔131111", confidence=0.99),
            unittest.mock.Mock(text="dH66/66", confidence=0.99),
            unittest.mock.Mock(text="0.49kg", confidence=0.99),
            unittest.mock.Mock(text="強化", confidence=0.99),
            unittest.mock.Mock(text="進化", confidence=0.99),
        )
        with patch(
            "pogo_iphone_renamer.device_controller.measure_ipad14_6_appraisal",
            side_effect=ValueError("not appraisal"),
        ), patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=lines,
        ):
            self.assertEqual(local_page_state(snapshot), "DETAIL")

    def test_team_leader_dialogue_is_not_mislabeled_as_map(self) -> None:
        snapshot = Snapshot(text="desktop", image="appraisal-dialog")
        lines = (
            unittest.mock.Mock(text="光蚪仔", confidence=0.99),
            unittest.mock.Mock(
                text="你好！我們來看看你的光蚪仔吧！", confidence=0.99
            ),
        )
        with patch(
            "pogo_iphone_renamer.device_controller.measure_ipad14_6_appraisal",
            side_effect=ValueError("not bars yet"),
        ), patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=lines,
        ):
            self.assertEqual(local_page_state(snapshot), "APPRAISAL_DIALOG")

    def test_detail_menu_prefers_exact_ocr_appraisal_control(self) -> None:
        from types import SimpleNamespace

        proxy = SimpleNamespace(
            observation=SimpleNamespace(width=1366, height=1024, token="fresh")
        )
        with patch(
            "pogo_iphone_renamer.rename_controls.tap_ocr_control"
        ) as tap_exact:
            from pogo_iphone_renamer import device_controller as base

            base._tap(proxy, "DETAIL_MENU")

        tap_exact.assert_called_once()
        self.assertEqual(tap_exact.call_args.args[1], "調查寶可夢")

    def test_appraisal_bars_are_never_accepted_as_plain_detail(self) -> None:
        snapshot = Snapshot(
            text="CP713 95/95HP 31.22kg 1.26m",
            image="visible-appraisal-bars",
        )
        with patch(
            "pogo_iphone_renamer.device_controller.measure_ipad14_6_appraisal",
            return_value=object(),
        ):
            with self.assertRaisesRegex(PolicyViolation, "鉴定条仍可见"):
                _validate_expected("DETAIL", snapshot)

    def test_transient_appraisal_measurement_does_not_tap_close_point(self) -> None:
        detail = Snapshot(text="CP713 95/95HP 31.22kg 1.26m", image="detail")
        appraisal = Snapshot(text="", image="appraisal-animation")
        with patch(
            "pogo_iphone_renamer.device_controller.local_page_state",
            side_effect=["DETAIL", "DETAIL_MENU", "APPRAISAL_DIALOG"],
        ), patch(
            "pogo_iphone_renamer.device_controller._ensure_stage_geometry_for_state",
            side_effect=lambda _proxy, snapshot, _state: snapshot,
        ), patch(
            "pogo_iphone_renamer.device_controller._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.device_controller._next_snapshot",
            side_effect=[detail, appraisal],
        ), patch(
            "pogo_iphone_renamer.device_controller.measure_ipad14_6_appraisal",
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
            "pogo_iphone_renamer.device_controller.local_page_state",
            side_effect=["DETAIL", "DETAIL_MENU", "APPRAISAL_DIALOG"],
        ), patch(
            "pogo_iphone_renamer.device_controller._ensure_stage_geometry_for_state",
            side_effect=lambda _proxy, snapshot, _state: snapshot,
        ), patch(
            "pogo_iphone_renamer.device_controller._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.device_controller._next_snapshot",
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
