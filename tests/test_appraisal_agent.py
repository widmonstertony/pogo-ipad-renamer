from __future__ import annotations

from types import SimpleNamespace
import json
import unittest
from unittest.mock import Mock, patch

from pogo_iphone_renamer.appraisal_agent import screen_snapshot, target_allowed


class AppraisalNavigationSafetyTests(unittest.TestCase):
    def test_inline_jpeg_is_not_ui_text_or_a_separate_capture(self):
        # Random base64 substrings must not trigger Dock/lock/menu heuristics.
        encoded = 'abcdock' * 8000
        response = {'content': [
            {'type': 'text', 'text': json.dumps({
                'frontmost': {'bundleId': 'com.nianticlabs.pokemongo'},
                'screenshot': encoded, 'screenshot_mime': 'image/jpeg',
                'elements': [{'text': '噴嚏熊'}],
            })},
            {'type': 'text', 'text': 'SAFETY_OBSERVATION_TOKEN=fresh'},
        ]}
        proxy = SimpleNamespace(call_tool=Mock(return_value=response))
        snapshot = screen_snapshot(proxy)
        self.assertEqual(snapshot.image, encoded)
        self.assertNotIn('dock', snapshot.text)
        self.assertIn('噴嚏熊', snapshot.text)
        self.assertIn('SAFETY_OBSERVATION_TOKEN=fresh', snapshot.text)
        proxy.call_tool.assert_called_once()
        from pogo_iphone_renamer.protocol import text_from_content
        self.assertNotIn(encoded, text_from_content(response))

    def test_fresh_read_session_precedes_the_observation_and_screenshot(self) -> None:
        """A read-session reset must not split the click token from its pixels."""

        call_order: list[str] = []

        def reset() -> None:
            call_order.append("reset")

        def call_tool(name: str, _arguments: dict) -> dict:
            call_order.append(name)
            if name == "describe_screen":
                return {
                    "content": [
                        {"type": "text", "text": "fresh detail"},
                        {"type": "image", "data": "fresh-pixels"},
                    ]
                }
            raise AssertionError(f"unexpected compatibility read: {name}")

        proxy = SimpleNamespace(
            client=SimpleNamespace(reset_read_session=Mock(side_effect=reset)),
            call_tool=Mock(side_effect=call_tool),
        )
        with patch.dict(
            "os.environ", {"POGO_RESET_MCP_SCREENSHOT_SESSION": "true"}, clear=False
        ):
            snapshot = screen_snapshot(proxy)

        self.assertEqual(call_order, ["reset", "describe_screen"])
        proxy.call_tool.assert_called_once_with(
            "describe_screen",
            {"include_screenshot": True, "include_ocr": False, "clickable_only": False},
        )
        self.assertEqual(snapshot.text, "fresh detail")
        self.assertEqual(snapshot.image, "fresh-pixels")

    def test_detail_menu_allows_only_appraisal(self) -> None:
        self.assertTrue(target_allowed("DETAIL_MENU", "OPEN_APPRAISAL", "寶可夢鑑定", "tap"))
        self.assertFalse(target_allowed("DETAIL_MENU", "OPEN_APPRAISAL", "傳送", "tap"))

    def test_detail_goal_separates_menu_and_pencil(self) -> None:
        self.assertTrue(target_allowed("DETAIL", "OPEN_APPRAISAL", "更多選單", "tap"))
        self.assertFalse(target_allowed("DETAIL", "OPEN_APPRAISAL", "名稱鉛筆", "tap"))
        self.assertTrue(target_allowed("DETAIL", "OPEN_RENAME_DIALOG", "名稱鉛筆", "tap"))

    def test_unknown_navigation_is_rejected(self) -> None:
        self.assertFalse(target_allowed("UNKNOWN", "OPEN_APPRAISAL", "anything", "tap"))


if __name__ == "__main__":
    unittest.main()
