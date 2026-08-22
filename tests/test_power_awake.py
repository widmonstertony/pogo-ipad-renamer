from __future__ import annotations

import unittest
from unittest.mock import Mock

from pogo_iphone_renamer.power_awake import (
    ES_CONTINUOUS,
    ES_DISPLAY_REQUIRED,
    ES_SYSTEM_REQUIRED,
    AwakeGuard,
)


class AwakeGuardTests(unittest.TestCase):
    def test_windows_acquires_and_releases_thread_execution_state(self) -> None:
        api = Mock(return_value=1)
        guard = AwakeGuard(platform="win32", windows_api=api)

        self.assertIn("Windows", guard.acquire() or "")
        guard.release()

        self.assertEqual(
            api.call_args_list[0].args[0],
            ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED,
        )
        self.assertEqual(api.call_args_list[1].args[0], ES_CONTINUOUS)

    def test_windows_failure_is_fatal_and_not_marked_active(self) -> None:
        guard = AwakeGuard(platform="win32", windows_api=Mock(return_value=0))
        with self.assertRaises(OSError):
            guard.acquire()
        guard.release()

    def test_macos_owns_and_terminates_caffeinate_child(self) -> None:
        process = Mock()
        process.poll.return_value = None
        popen = Mock(return_value=process)
        guard = AwakeGuard(
            platform="darwin",
            popen=popen,
            which=lambda name: "/usr/bin/caffeinate" if name == "caffeinate" else None,
        )

        self.assertIn("macOS", guard.acquire() or "")
        guard.release()

        self.assertEqual(popen.call_args.args[0], ["/usr/bin/caffeinate", "-dimsu"])
        process.terminate.assert_called_once_with()
        process.wait.assert_called_once_with(timeout=3)


if __name__ == "__main__":
    unittest.main()
