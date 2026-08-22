from __future__ import annotations

import base64
import io
import unittest

from PIL import Image

from pogo_iphone_renamer.landscape_cv import rotate_mcp_image_upright


def encoded(width: int, height: int) -> str:
    image = Image.new("RGB", (width, height), (20, 40, 60))
    output = io.BytesIO()
    image.save(output, format="PNG")
    return base64.b64encode(output.getvalue()).decode("ascii")


class AutoLandscapeTests(unittest.TestCase):
    def test_keeps_native_landscape_frame(self) -> None:
        image = rotate_mcp_image_upright(encoded(1366, 1024), "AUTO_LANDSCAPE")
        self.assertEqual(image.size, (1366, 1024))

    def test_rotates_legacy_portrait_encoded_frame(self) -> None:
        image = rotate_mcp_image_upright(encoded(1024, 1366), "AUTO_LANDSCAPE")
        self.assertEqual(image.size, (1366, 1024))


if __name__ == "__main__":
    unittest.main()
