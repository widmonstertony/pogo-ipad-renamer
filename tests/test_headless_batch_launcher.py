from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from pogo_iphone_renamer.gui import AppSettings
from pogo_iphone_renamer.headless_batch_launcher import (
    background_environment,
    runner_command,
    start_from_current_detail,
)


class _FakeProcess:
    pid = 9876


class HeadlessBatchLauncherTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = AppSettings(
            mcp_url="http://192.168.68.67:8090/mcp",
            model="RapidOCR + 像素测量（无需设置）",
            batch_limit=50,
            unlimited=True,
        )

    def test_environment_forces_direct_current_detail_and_no_restart(self) -> None:
        root = Path("/tmp/pogo")
        environment = background_environment(root, self.settings)

        self.assertEqual(environment["POGO_BATCH_LIMIT"], "0")
        self.assertEqual(environment["POGO_START_FROM_CURRENT_DETAIL"], "true")
        self.assertEqual(environment["POGO_ALLOW_GAME_RESTART"], "false")
        self.assertEqual(environment["IPHONE_MCP_URL"], self.settings.mcp_url)

    def test_launcher_detaches_current_source_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pause = root / ".pogo-data" / "batch.pause"
            pause.parent.mkdir(parents=True)
            pause.write_text("pause\n", encoding="utf-8")
            calls: list[tuple[object, object]] = []

            def popen(command, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((command, kwargs))
                return _FakeProcess()

            pid = start_from_current_detail(
                root, mode="rename", settings=self.settings, popen=popen
            )

            self.assertFalse(pause.exists())

        self.assertEqual(pid, 9876)
        self.assertEqual(calls[0][0], runner_command(root.resolve(), "rename"))
        kwargs = calls[0][1]
        self.assertEqual(kwargs["env"]["POGO_START_FROM_CURRENT_DETAIL"], "true")
        self.assertEqual(kwargs["env"]["POGO_ALLOW_GAME_RESTART"], "false")
        self.assertEqual(kwargs["start_new_session"], os.name != "nt")


if __name__ == "__main__":
    unittest.main()
