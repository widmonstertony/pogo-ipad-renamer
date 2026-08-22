from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any, Callable


ES_SYSTEM_REQUIRED = 0x00000001
ES_DISPLAY_REQUIRED = 0x00000002
ES_CONTINUOUS = 0x80000000


class AwakeGuard:
    """Temporarily prevent host sleep while a batch worker is alive.

    Windows execution state is scoped to the worker thread.  macOS uses a
    private ``caffeinate`` child and terminates it in ``release``.  No persistent
    power-plan setting is changed on either platform.
    """

    def __init__(
        self,
        *,
        platform: str | None = None,
        windows_api: Any | None = None,
        popen: Callable[..., Any] = subprocess.Popen,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self.platform = platform or sys.platform
        self._windows_api = windows_api
        self._popen = popen
        self._which = which
        self._active = False
        self._caffeinate: Any | None = None

    def acquire(self) -> str | None:
        if self._active:
            return self.description
        if self.platform == "win32":
            api = self._windows_api
            if api is None:
                import ctypes

                api = ctypes.windll.kernel32.SetThreadExecutionState
            result = api(ES_CONTINUOUS | ES_SYSTEM_REQUIRED | ES_DISPLAY_REQUIRED)
            if not result:
                raise OSError("Windows 拒绝了临时防睡眠请求")
            self._windows_api = api
            self._active = True
            return self.description
        if self.platform == "darwin":
            executable = self._which("caffeinate")
            if not executable:
                raise OSError("macOS 未找到系统 caffeinate 工具")
            self._caffeinate = self._popen(
                [executable, "-dimsu"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self._active = True
            return self.description
        return None

    @property
    def description(self) -> str | None:
        if self.platform == "darwin":
            return "macOS 系统与显示器防睡眠已启用"
        if self.platform == "win32":
            return "Windows 系统与显示器防睡眠已启用"
        return None

    def release(self) -> None:
        if not self._active:
            return
        try:
            if self.platform == "win32":
                assert self._windows_api is not None
                self._windows_api(ES_CONTINUOUS)
            elif self._caffeinate is not None and self._caffeinate.poll() is None:
                self._caffeinate.terminate()
                try:
                    self._caffeinate.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._caffeinate.kill()
                    self._caffeinate.wait(timeout=3)
        finally:
            self._active = False
            self._caffeinate = None

    def __enter__(self) -> AwakeGuard:
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
