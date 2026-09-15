from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pogo_iphone_renamer.gui import AppSettings
from pogo_iphone_renamer.headless_batch_launcher import (
    background_environment,
    main,
    runner_command,
    start_batch,
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

    def test_environment_uses_verified_automatic_entry_and_no_restart(self) -> None:
        root = Path("/tmp/pogo")
        environment = background_environment(root, self.settings)

        self.assertEqual(environment["POGO_BATCH_LIMIT"], "0")
        self.assertEqual(environment["POGO_START_FROM_CURRENT_DETAIL"], "false")
        self.assertEqual(environment["POGO_ALLOW_GAME_RESTART"], "false")
        self.assertEqual(environment["IPHONE_MCP_URL"], self.settings.mcp_url)

    def test_environment_can_opt_into_strict_current_detail_entry(self) -> None:
        environment = background_environment(
            Path("/tmp/pogo"), self.settings, from_current_detail=True
        )

        self.assertEqual(environment["POGO_START_FROM_CURRENT_DETAIL"], "true")

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

    def test_automatic_launcher_does_not_require_a_manual_detail_page(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls: list[tuple[object, object]] = []

            def popen(command, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((command, kwargs))
                return _FakeProcess()

            pid = start_batch(root, mode="rename", settings=self.settings, popen=popen)

        self.assertEqual(pid, 9876)
        self.assertEqual(calls[0][1]["env"]["POGO_START_FROM_CURRENT_DETAIL"], "false")

    def test_explicit_current_detail_start_discards_an_old_pager_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            anchor = root / ".pogo-data" / "pager-resume.json"
            anchor.parent.mkdir(parents=True)
            anchor.write_text('{"fingerprint":{"cp":"cp632"}}', encoding="utf-8")

            start_from_current_detail(
                root,
                mode="rename",
                settings=self.settings,
                popen=lambda *_args, **_kwargs: _FakeProcess(),
            )

            self.assertFalse(anchor.exists())

    def test_new_ipad_enables_fresh_screenshot_sessions(self) -> None:
        settings = AppSettings(
            mcp_url="http://192.168.68.84:8090/mcp",
            model="RapidOCR + 像素测量（无需设置）",
            batch_limit=50,
            unlimited=True,
        )

        environment = background_environment(Path("/tmp/pogo"), settings)

        self.assertEqual(environment["POGO_EXPECTED_DEVICE_MACHINE"], "iPad7,2")
        self.assertEqual(environment["POGO_RESET_MCP_SCREENSHOT_SESSION"], "true")

    def test_focus_game_cli_reports_a_safe_foreground_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "pogo_iphone_renamer.headless_batch_launcher.focus_configured_game",
                return_value=True,
            ) as focus:
                code = main(["--root", directory, "--focus-game"])

        self.assertEqual(code, 0)
        focus.assert_called_once_with(Path(directory).resolve())


if __name__ == "__main__":
    unittest.main()
