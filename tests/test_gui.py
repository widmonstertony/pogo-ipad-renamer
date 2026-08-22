from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pogo_iphone_renamer.gui import (
    AppSettings,
    DEFAULT_MCP_URL,
    friendly_event,
    load_settings,
    save_settings,
)
from pogo_iphone_renamer.prompts import READ_ONLY_PROMPT, rename_prompt


class GuiSettingsTests(unittest.TestCase):
    def test_defaults_use_current_mcp_and_model(self) -> None:
        settings = AppSettings()
        self.assertEqual(DEFAULT_MCP_URL, "http://127.0.0.1:8090/mcp")
        self.assertEqual(settings.health_url, "http://127.0.0.1:8090/health")
        self.assertEqual(settings.model, "qwen3.8:27b")
        self.assertTrue(settings.unlimited)

    def test_settings_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected = AppSettings(batch_limit=7)
            save_settings(root, expected)
            self.assertEqual(load_settings(root), expected)

    def test_invalid_batch_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AppSettings(batch_limit=-1).validate()

    def test_finite_batch_has_no_maximum(self) -> None:
        AppSettings(batch_limit=10**12, unlimited=False).validate()


class PromptTests(unittest.TestCase):
    def test_read_only_prompt_forbids_writes(self) -> None:
        self.assertIn("绝对不要调用任何写工具", READ_ONLY_PROMPT)

    def test_rename_prompt_has_batch_and_transfer_guard(self) -> None:
        prompt = rename_prompt(20)
        self.assertIn("最多处理 20 只", prompt)
        self.assertIn("绝不点击或尝试传送", prompt)
        self.assertIn("Poke Genie", prompt)

    def test_rename_prompt_allows_one_thousand(self) -> None:
        self.assertIn("最多处理 1000 只", rename_prompt(1000))

    def test_rename_prompt_supports_unlimited(self) -> None:
        self.assertIn("直到没有下一只或用户停止", rename_prompt(0))

    def test_event_text_is_extracted(self) -> None:
        line = json.dumps({"type": "text", "part": {"text": "观察完成"}})
        self.assertEqual(friendly_event(line), "观察完成")


if __name__ == "__main__":
    unittest.main()
