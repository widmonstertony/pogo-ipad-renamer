import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pogo_iphone_renamer import field_verification as agent


class CompleteFieldTreeTests(unittest.TestCase):
    def test_large_diagnostic_payload_is_not_truncated_before_exact_parsing(self):
        nickname = "呱呱泡⓫⓬❿⁷³"
        payload = {"diagnostics": "x" * 60000, "elements": [
            {"type": "control", "text": nickname, "rect": {"width": 887}},
        ]}
        proxy = SimpleNamespace(call_tool=Mock(return_value={"content": [
            {"type": "text", "text": json.dumps(payload)}
        ]}))
        self.assertEqual(agent._field_value_from_all_elements(proxy), nickname)

    def test_full_tree_reads_new_unicode_value_each_time_not_a_cached_field(self):
        values = ("呱呱泡⓫⓬❿⁷³", "呱呱泡⓯⓮⓭⁹³")
        results = [{"content": [{"type": "text", "text": json.dumps({"elements": [
            {"type": "control", "text": value, "rect": {"width": 887}},
            {"type": "control", "text": "空格键", "rect": {"width": 422}},
        ]})}]} for value in values]
        proxy = SimpleNamespace(_prefer_complete_field_tree=True, call_tool=Mock(side_effect=results))
        with patch.object(agent, "_field_value") as cached:
            self.assertEqual(tuple(agent._verified_entered_value(proxy) for _ in values), values)
            cached.assert_not_called()
        self.assertEqual(proxy.call_tool.call_count, 2)
        self.assertFalse(proxy.call_tool.call_args.args[1]["visible_only"])

    def test_error_payload_is_not_exact_field_proof(self):
        proxy = SimpleNamespace(call_tool=Mock(return_value={"isError": True}))
        with self.assertRaises(agent.PolicyViolation):
            agent._field_value_from_all_elements(proxy)
