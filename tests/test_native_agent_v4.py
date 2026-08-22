from __future__ import annotations

import unittest

from pogo_iphone_renamer.native_agent_v4 import readonly_user_message


class DeterministicReadonlyTests(unittest.TestCase):
    def test_observations_become_one_user_message(self) -> None:
        message = readonly_user_message(
            {"content": [{"type": "text", "text": "Pokemon GO"}]},
            {
                "content": [
                    {"type": "text", "text": "screen text"},
                    {"type": "image", "data": "image-base64"},
                ]
            },
        )
        self.assertEqual(message["role"], "user")
        self.assertIn("Pokemon GO", message["content"])
        self.assertIn("screen text", message["content"])
        self.assertEqual(message["images"], ["image-base64"])


if __name__ == "__main__":
    unittest.main()

