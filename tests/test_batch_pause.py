from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pogo_iphone_renamer.batch_pause import BatchPauseFile


class BatchPauseFileTests(unittest.TestCase):
    def test_request_and_resume_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            control = BatchPauseFile(Path(directory) / "batch.pause")
            self.assertFalse(control.requested)
            control.request()
            self.assertTrue(control.requested)
            control.resume()
            self.assertFalse(control.requested)


if __name__ == "__main__":
    unittest.main()
