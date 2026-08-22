from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from pogo_iphone_renamer.device_run_lock import DeviceRunLock
from pogo_iphone_renamer.policy import PolicyViolation


class DeviceRunLockTests(unittest.TestCase):
    def test_second_lock_is_rejected_and_lock_can_be_reacquired(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "device.lock"
            with DeviceRunLock(path):
                with self.assertRaises(PolicyViolation):
                    with DeviceRunLock(path):
                        pass
            with DeviceRunLock(path):
                pass


if __name__ == "__main__":
    unittest.main()
