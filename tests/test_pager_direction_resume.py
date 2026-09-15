import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from pogo_iphone_renamer.ipad_landscape_batch_agent_v26 import _load_pager_direction, _save_pager_direction


class PagerDirectionResumeTests(unittest.TestCase):
    def test_direction_is_bound_to_device_and_validated(self):
        with tempfile.TemporaryDirectory() as directory:
            settings = SimpleNamespace(journal_path=Path(directory) / "actions.jsonl", mcp_url="http://ipad/mcp")
            self.assertIsNone(_load_pager_direction(settings))
            _save_pager_direction(settings, "right")
            self.assertEqual("right", _load_pager_direction(settings))
            _save_pager_direction(settings, "invalid")
            self.assertEqual("right", _load_pager_direction(settings))
            settings.mcp_url = "http://other/mcp"
            self.assertIsNone(_load_pager_direction(settings))
            path = Path(directory) / "pager-direction.json"
            path.write_text("[]")
            self.assertIsNone(_load_pager_direction(settings))
            path.write_text("broken")
            self.assertIsNone(_load_pager_direction(settings))
