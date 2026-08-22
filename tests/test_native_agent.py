from __future__ import annotations

import unittest

from pogo_iphone_renamer.gui_native import friendly_native_event
from pogo_iphone_renamer.native_agent import (
    available_tool_names,
    normalize_tool_name,
    ollama_tool_schemas,
    tool_result_message,
)
from pogo_iphone_renamer.policy import PolicyViolation


SAMPLE_TOOLS = [
    {
        "name": "describe_screen",
        "description": "observe",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "tap_screen",
        "description": "tap",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "pogo_run_status",
        "description": "status",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


class NativeAgentTests(unittest.TestCase):
    def test_read_only_removes_write_tools_entirely(self) -> None:
        tools = ollama_tool_schemas(SAMPLE_TOOLS, read_only=True)
        names = available_tool_names(tools)
        self.assertIn("describe_screen", names)
        self.assertIn("pogo_run_status", names)
        self.assertNotIn("tap_screen", names)

    def test_exact_and_prefixed_tool_names_resolve(self) -> None:
        available = {"describe_screen"}
        self.assertEqual(normalize_tool_name("describe_screen", available), "describe_screen")
        self.assertEqual(normalize_tool_name("iphone_safe_describe_screen", available), "describe_screen")

    def test_invalid_tool_is_rejected(self) -> None:
        with self.assertRaises(PolicyViolation):
            normalize_tool_name("invalid", {"describe_screen"})

    def test_tool_result_preserves_text_and_one_image(self) -> None:
        message = tool_result_message(
            "screenshot",
            {
                "content": [
                    {"type": "text", "text": "screen"},
                    {"type": "image", "data": "abc"},
                    {"type": "image", "data": "def"},
                ]
            },
        )
        self.assertEqual(message["content"], "screen")
        self.assertEqual(message["images"], ["abc"])

    def test_native_log_is_human_readable(self) -> None:
        self.assertEqual(
            friendly_native_event('{"type":"tool","name":"describe_screen"}'),
            "正在调用：describe_screen",
        )


if __name__ == "__main__":
    unittest.main()

