from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from pogo_iphone_renamer.live_activity import publish_preview, update_live_activity


class LiveActivityTests(unittest.TestCase):
    def test_detail_and_measurement_are_retained_while_steps_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "activity.json"
            update_live_activity(
                {
                    "type": "detail",
                    "species": "劈斧螳螂",
                    "current_name": "劈斧螳螂",
                    "is_default": True,
                },
                path=path,
            )
            update_live_activity(
                {"type": "iv_measurement", "attack": 15, "defense": 14, "stamina": 13},
                path=path,
            )
            activity = update_live_activity(
                {"type": "status", "message": "正在逐字核验昵称"},
                path=path,
            )

            self.assertEqual(activity["pokemon"]["name"], "劈斧螳螂")
            self.assertEqual(activity["iv"]["attack"], 15)
            self.assertEqual(activity["screen"], "APPRAISAL_BARS")
            self.assertEqual(activity["step"], "正在逐字核验昵称")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["pokemon"]["species"], "劈斧螳螂")

    def test_preview_is_created_from_an_existing_worker_frame(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            buffer = io.BytesIO()
            Image.new("RGB", (1366, 1024), "white").save(buffer, format="JPEG")
            target = Path(directory) / "preview.jpg"

            self.assertTrue(publish_preview(base64.b64encode(buffer.getvalue()).decode(), path=target))

            with Image.open(target) as preview:
                self.assertLessEqual(preview.width, 330)
                self.assertLessEqual(preview.height, 440)


if __name__ == "__main__":
    unittest.main()
