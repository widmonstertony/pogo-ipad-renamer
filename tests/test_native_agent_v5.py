from __future__ import annotations

import unittest

from pogo_iphone_renamer.native_agent_v5 import readonly_user_message


class SplitVisionReadonlyTests(unittest.TestCase):
    def test_only_screenshot_image_is_attached(self) -> None:
        message = readonly_user_message(
            {"content": [{"type": "text", "text": "Pokemon GO"}]},
            {"content": [{"type": "text", "text": "OCR Pikachu"}]},
            {"content": [{"type": "image", "data": "jpeg-base64"}]},
        )
        self.assertIn("OCR Pikachu", message["content"])
        self.assertNotIn("jpeg-base64", message["content"])
        self.assertEqual(message["images"], ["jpeg-base64"])


if __name__ == "__main__":
    unittest.main()

