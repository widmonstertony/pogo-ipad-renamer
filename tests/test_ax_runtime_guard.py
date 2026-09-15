import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pogo_iphone_renamer import rename_submission as agent
from pogo_iphone_renamer.appraisal_agent import Snapshot


class AXRuntimeGuardTests(unittest.TestCase):
    def test_only_explicit_structured_inactive_mode_is_accepted(self):
        inactive = {"ok": False, "error": "timeout", "axRuntimeMode": "inactive"}
        self.assertTrue(agent._ax_runtime_is_inactive({"structuredContent": inactive}))
        self.assertTrue(agent._ax_runtime_is_inactive({"content": [{
            "type": "text", "text": json.dumps({"accessibilityState": inactive})
        }]}))
        for result in ({}, {"structuredContent": {"error": "timeout"}},
                       {"structuredContent": {"axRuntimeMode": "active"}},
                       {"content": [{"type": "text", "text": "axRuntimeMode: inactive"}]}):
            self.assertFalse(agent._ax_runtime_is_inactive(result))

    def test_inactive_runtime_has_bounded_read_only_retries(self):
        proxy = SimpleNamespace(observation=None, call_tool=Mock(return_value={
            "structuredContent": {"axRuntimeMode": "inactive"}
        }))
        with patch.object(agent.time, "sleep"), patch.object(agent, "emit"), self.assertRaises(agent.AccessibilityRuntimeUnavailable):
            agent._require_name_field_runtime(proxy, "呱呱泡蛙")
        self.assertEqual(proxy.call_tool.call_count, 3)
        proxy.call_tool.assert_called_with("get_ui_elements", {
            "visible_only": False, "clickable_only": False, "limit": 160, "debug": True,
        })

    def test_second_fresh_full_tree_can_recover_without_any_input(self):
        value = {"elements": [{"type": "control", "text": "呱呱泡蛙", "rect": {"width": 887}}]}
        proxy = SimpleNamespace(observation=None, call_tool=Mock(side_effect=[
            {"structuredContent": {"axRuntimeMode": "inactive"}},
            {"content": [{"type": "text", "text": json.dumps(value)}]},
        ]))
        proxy.client = SimpleNamespace(reset_read_session=Mock())
        with patch.object(agent.time, "sleep"), patch.object(agent, "emit"):
            agent._require_name_field_runtime(proxy, "呱呱泡蛙")
        self.assertTrue(proxy._prefer_complete_field_tree)
        self.assertEqual(proxy.call_tool.call_count, 2)
        proxy.client.reset_read_session.assert_called_once_with()

    def test_successful_full_field_wins_over_nested_inactive_diagnostic(self):
        value = {"elements": [{"type": "control", "text": "呱呱泡蛙", "rect": {"width": 887}}],
                 "diagnostics": {"axRuntimeMode": "inactive"}}
        proxy = SimpleNamespace(observation=None, call_tool=Mock(return_value={
            "content": [{"type": "text", "text": json.dumps(value)}],
        }))
        agent._require_name_field_runtime(proxy, "呱呱泡蛙")
        self.assertTrue(proxy._prefer_complete_field_tree)

    def test_wrong_field_or_error_result_never_authorizes_clearing(self):
        for actual, error in (("別的名稱", False), ("呱呱泡蛙", True)):
            value = {"elements": [{"type": "control", "text": actual, "rect": {"width": 887}}]}
            proxy = SimpleNamespace(observation=None, call_tool=Mock(return_value={
                "content": [{"type": "text", "text": json.dumps(value)}], "isError": error,
            }))
            with self.subTest(actual=actual, error=error), self.assertRaises(agent.PolicyViolation):
                agent._require_name_field_runtime(proxy, "呱呱泡蛙")

    def test_cached_exact_default_field_needs_no_extra_ax_request(self):
        proxy = SimpleNamespace(observation=SimpleNamespace(text=json.dumps({"elements": [
            {"type": "control", "text": "呱呱泡蛙", "rect": {"width": 600}}
        ]})), call_tool=Mock())
        agent._require_name_field_runtime(proxy, "呱呱泡蛙")
        proxy.call_tool.assert_not_called()

    def test_inactive_runtime_cancels_before_any_clear_or_input_and_stops(self):
        proxy = SimpleNamespace(verified_renames=0, call_tool=Mock())
        error = agent.AccessibilityRuntimeUnavailable("axRuntimeMode: inactive")
        with (
            patch.object(agent, "_focus_ocr_default_name_field", side_effect=error),
            patch.object(agent, "_backspace_current_name") as clear,
            patch.object(agent, "_cancel_unverified_input", side_effect=
                         agent.RenameFieldVerificationUnavailable(Snapshot("", "detail"), "")) as cancel,
            patch.object(agent, "emit"),
        ):
            with self.assertRaises(agent.AccessibilityRuntimeUnavailable):
                agent._commit_after_dismissing_keyboard(proxy, current_name="呱呱泡蛙",
                                                      species="呱呱泡蛙", nickname="呱呱泡⓫⓬❿⁷³")
        clear.assert_not_called()
        proxy.call_tool.assert_not_called()
        cancel.assert_called_once_with(proxy, "呱呱泡蛙")


if __name__ == "__main__":
    unittest.main()
