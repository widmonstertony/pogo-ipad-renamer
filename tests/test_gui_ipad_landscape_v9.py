from __future__ import annotations

import json
import unittest
from pathlib import Path

from pogo_iphone_renamer.gui_ipad_landscape_v9 import (
    background_runner_command,
    batch_progress_event,
)
from pogo_iphone_renamer.gui_native import python_worker_command


class BatchProgressEventTests(unittest.TestCase):
    def test_structured_progress_is_extracted(self) -> None:
        event = {
            "type": "progress",
            "current": 37,
            "limit": None,
            "phase": "paused",
            "renamed": 12,
            "skipped": 24,
        }
        self.assertEqual(batch_progress_event(json.dumps(event)), event)

    def test_non_progress_output_is_ignored(self) -> None:
        self.assertIsNone(batch_progress_event('{"type":"status"}'))
        self.assertIsNone(batch_progress_event("ordinary log"))

    def test_windows_worker_uses_py_version_selector(self) -> None:
        self.assertEqual(
            python_worker_command(r"C:\\Windows\\py.exe", platform_name="nt"),
            [r"C:\\Windows\\py.exe", "-3.13"],
        )

    def test_macos_worker_uses_current_interpreter_directly(self) -> None:
        self.assertEqual(
            python_worker_command("/opt/homebrew/bin/python3", platform_name="posix"),
            ["/opt/homebrew/bin/python3"],
        )

    def test_gui_starts_detached_background_runner(self) -> None:
        self.assertEqual(
            background_runner_command(
                ["/opt/homebrew/bin/python3"],
                mode="rename",
                root=Path("/tmp/pogo"),
            ),
            [
                "/opt/homebrew/bin/python3",
                "-u",
                "-m",
                "pogo_iphone_renamer.background_batch_runner",
                "--mode",
                "rename",
                "--root",
                "/tmp/pogo",
            ],
        )


if __name__ == "__main__":
    unittest.main()
