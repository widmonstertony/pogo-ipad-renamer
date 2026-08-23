from __future__ import annotations

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pogo_iphone_renamer.background_batch_runner import (
    background_run_is_active,
    request_background_stop,
    run_background_batch,
    worker_command,
)


class _FakeAwake:
    def __init__(self) -> None:
        self.released = False

    def acquire(self) -> str:
        return "测试防睡眠已启用"

    def release(self) -> None:
        self.released = True


class _FakeProcess:
    pid = 4321

    def __init__(self) -> None:
        self.stdout = io.StringIO(
            '{"type":"progress","current":2,"limit":null,"phase":"processing"}\n'
            '{"type":"status","message":"正在处理第 2 只…"}\n'
        )

    def poll(self) -> None:
        return None

    def wait(self) -> int:
        return 0

    def terminate(self) -> None:
        return None


class BackgroundBatchRunnerTests(unittest.TestCase):
    def test_worker_uses_its_own_interpreter(self) -> None:
        command = worker_command("rename")
        self.assertEqual(command[0], os.sys.executable)
        self.assertEqual(command[-2:], ["--mode", "rename"])

    def test_runner_writes_durable_log_and_finished_state(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            awake = _FakeAwake()
            calls: list[tuple[object, object]] = []

            def popen(command, **kwargs):  # type: ignore[no-untyped-def]
                calls.append((command, kwargs))
                return _FakeProcess()

            code = run_background_batch(
                "rename",
                root=root,
                environment={
                    "POGO_BACKGROUND_LOG": str(root / "worker.log"),
                    "POGO_BATCH_STATE": str(root / "state.json"),
                },
                popen=popen,
                awake_factory=lambda: awake,  # type: ignore[arg-type]
            )

            self.assertEqual(code, 0)
            self.assertTrue(awake.released)
            self.assertEqual(calls[0][0], worker_command("rename"))
            state = json.loads((root / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "finished")
            self.assertEqual(state["exit_code"], 0)
            log = (root / "worker.log").read_text(encoding="utf-8")
            self.assertIn("后台批量工作进程已启动", log)
            self.assertIn("后台任务正常结束", log)

    def test_active_state_requires_a_live_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"status": "running", "pid": os.getpid()}),
                encoding="utf-8",
            )
            self.assertTrue(background_run_is_active(path))
            path.write_text(
                json.dumps({"status": "finished", "pid": os.getpid()}),
                encoding="utf-8",
            )
            self.assertFalse(background_run_is_active(path))

    def test_stop_request_signals_live_runner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "state.json"
            path.write_text(
                json.dumps({"status": "running", "pid": os.getpid()}),
                encoding="utf-8",
            )
            with patch(
                "pogo_iphone_renamer.background_batch_runner.os.kill"
            ) as kill:
                # The active check uses signal 0 first, then the safe stop signal.
                kill.side_effect = [None, None]
                self.assertTrue(request_background_stop(path))
            self.assertEqual(kill.call_args_list[-1].args[0], os.getpid())


if __name__ == "__main__":
    unittest.main()
