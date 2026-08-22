from __future__ import annotations

import unittest

from pogo_iphone_renamer.native_agent_v3 import FOLLOWUP_TEXT, qwen_safe_messages


class QwenToolContinuationTests(unittest.TestCase):
    def test_tool_tail_gets_recent_user_continuation(self) -> None:
        messages = [
            {"role": "user", "content": "original"},
            {"role": "assistant", "content": "", "tool_calls": []},
            {"role": "tool", "tool_name": "status", "content": "ok"},
        ]
        normalized = qwen_safe_messages(messages)
        self.assertEqual(normalized[-1], {"role": "user", "content": FOLLOWUP_TEXT})

    def test_tool_image_moves_to_user_continuation(self) -> None:
        normalized = qwen_safe_messages(
            [
                {"role": "user", "content": "original"},
                {
                    "role": "tool",
                    "tool_name": "screenshot",
                    "content": "image",
                    "images": ["abc"],
                },
            ]
        )
        self.assertNotIn("images", normalized[-2])
        self.assertEqual(normalized[-1]["images"], ["abc"])

    def test_normal_assistant_tail_is_unchanged(self) -> None:
        messages = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "a"}]
        self.assertEqual(qwen_safe_messages(messages), messages)


if __name__ == "__main__":
    unittest.main()

