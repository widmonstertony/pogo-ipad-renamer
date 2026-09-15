from contextlib import ExitStack
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from pogo_iphone_renamer import ipad_landscape_agent as base
from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.policy import PolicyViolation


class AppraisalMenuResponseTests(unittest.TestCase):
    def test_all_supported_profiles_locate_the_exact_appraisal_label(self):
        for orientation in ("STAGE_MANAGER_MAXIMIZED", "STAGE_MANAGER_PORTRAIT_WINDOW", "PORTRAIT_FULLSCREEN"):
            proxy = SimpleNamespace(observation=SimpleNamespace(width=1366, height=1024, token="fresh"))
            with self.subTest(orientation=orientation), patch.object(base, "ORIENTATION", orientation), patch(
                "pogo_iphone_renamer.rename_controls_v20.tap_ocr_control"
            ) as tap:
                base._tap(proxy, "DETAIL_MENU")
                tap.assert_called_once()
                self.assertEqual(tap.call_args.args[1], "調查寶可夢")

    def navigation_mocks(self, stack, states):
        state = stack.enter_context(patch.object(base, "local_page_state", side_effect=states))
        stack.enter_context(patch.object(base, "_ensure_stage_geometry_for_state", side_effect=lambda p, s, key: s))
        tap = stack.enter_context(patch.object(base, "_tap"))
        capture = stack.enter_context(patch.object(base, "_next_snapshot", return_value=Snapshot("", "frame")))
        stack.enter_context(patch.object(base, "measure_ipad14_6_appraisal", return_value="verified-bars"))
        emit = stack.enter_context(patch.object(base, "emit"))
        return state, tap, capture, emit

    def test_delayed_overlay_is_read_again_without_another_tap(self):
        with ExitStack() as stack:
            _, tap, capture, emit = self.navigation_mocks(
                stack, ["DETAIL_MENU", "DETAIL_MENU", "DETAIL_MENU", "APPRAISAL_DIALOG"]
            )
            _, measurement = base.navigate_to_appraisal(object(), Snapshot("", "menu"))
            self.assertEqual(measurement, "verified-bars")
            tap.assert_called_once_with(unittest.mock.ANY, "DETAIL_MENU")
            self.assertEqual(capture.call_count, 2)
            events = [call.kwargs.get("stage", call.kwargs.get("state")) for call in emit.call_args_list]
            self.assertLess(events.index("等待鉴定菜单响应"), events.index("APPRAISAL"))

    def test_unresponsive_menu_stops_after_bounded_reads(self):
        with ExitStack() as stack:
            _, tap, capture, emit = self.navigation_mocks(stack, ["DETAIL_MENU"] * 6)
            with self.assertRaisesRegex(PolicyViolation, "没有验证到鉴定覆盖层"):
                base.navigate_to_appraisal(object(), Snapshot("", "menu"))
            tap.assert_called_once()
            self.assertEqual(capture.call_count, 3)
            self.assertFalse(any(call.kwargs.get("state") == "APPRAISAL" for call in emit.call_args_list))

    def test_unexpected_page_is_not_retried(self):
        with ExitStack() as stack:
            _, tap, capture, _ = self.navigation_mocks(stack, ["DETAIL_MENU", "DETAIL", "DETAIL"])
            with self.assertRaises(PolicyViolation):
                base.navigate_to_appraisal(object(), Snapshot("", "menu"))
            tap.assert_called_once()
            capture.assert_called_once()


if __name__ == "__main__":
    unittest.main()
