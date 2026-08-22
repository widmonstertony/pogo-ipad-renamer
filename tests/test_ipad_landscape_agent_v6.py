from __future__ import annotations

import json
import unittest
from types import SimpleNamespace

from pogo_iphone_renamer.ipad_landscape_agent_v6 import _fresh_name_field


class ImmediateRenameFieldTests(unittest.TestCase):
    def test_reads_exact_traditional_name_from_post_write_observation(self) -> None:
        elements = {
            "elements": [
                {
                    "type": "control",
                    "text": "輕飄飄",
                    "rect": {"x": 300, "y": 400, "width": 430, "height": 50},
                },
                {"type": "control", "text": "清除文本", "rect": {"width": 40}},
                {"type": "control", "text": "完成", "rect": {"width": 40}},
                {"type": "control", "text": "取消", "rect": {"width": 40}},
            ]
        }
        proxy = SimpleNamespace(
            observation=SimpleNamespace(text="SCREEN\n" + json.dumps(elements, ensure_ascii=False))
        )

        snapshot, value = _fresh_name_field(proxy)

        self.assertEqual(value, "輕飄飄")
        self.assertIn("清除文本", snapshot.text)


if __name__ == "__main__":
    unittest.main()
