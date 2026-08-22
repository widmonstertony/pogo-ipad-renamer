from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from pogo_iphone_renamer.ipad_landscape_agent_v10 import _field_value


def proxy_with_field(text: str):
    payload = {
        "elements": [
            {
                "type": "control",
                "text": text,
                "rect": {"x": 20, "y": 893, "width": 887, "height": 34},
            },
            {
                "type": "control",
                "text": "0,空格",
                "rect": {"x": 278, "y": 280, "width": 558, "height": 64},
            },
        ]
    }
    return SimpleNamespace(observation=SimpleNamespace(text=json.dumps(payload, ensure_ascii=False)))


class RenameFieldReplacementTests(unittest.TestCase):
    def test_empty_placeholder_is_read_from_widest_field(self) -> None:
        self.assertEqual(_field_value(proxy_with_field("文本")), "文本")

    def test_exact_special_character_nickname_is_preserved(self) -> None:
        nickname = "輕飄飄⓯⓮⓮⁹⁶"
        self.assertEqual(_field_value(proxy_with_field(nickname)), nickname)


if __name__ == "__main__":
    unittest.main()
