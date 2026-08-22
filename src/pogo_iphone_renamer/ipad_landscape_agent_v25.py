from __future__ import annotations

import argparse

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v13 as v13
from . import ipad_landscape_agent_v14 as v14
from . import ipad_landscape_agent_v16 as v16
from .config import Settings
from .device_run_lock import DeviceRunLock
from .ipad_landscape_agent_v20 import _ORIGINAL_NAVIGATE_V14
from .ipad_landscape_agent_v22 import _commit_after_dismissing_keyboard
from .ipad_landscape_agent_v24 import _navigate_with_read_only_measurement_retry
from .keyboard_control_v22 import dismiss_active_keyboard
from .local_ocr import ocr_mcp_screenshot, rename_dialog_visible
from .native_agent import emit
from .policy import PolicyViolation
from .rename_controls_v20 import tap_cancel
from .server import SafeProxy


def _navigate_with_complete_stale_recovery(proxy: SafeProxy, snapshot):
    if snapshot.image and rename_dialog_visible(
        ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
    ):
        if proxy.observation is None:
            raise PolicyViolation("遗留改名弹窗缺少安全观察")
        proxy.observation.text += "\n重新命名（验证遗留弹窗；准备安全恢复）"
        if dismiss_active_keyboard(proxy):
            snapshot = base._next_snapshot(proxy, 0.8)
            if not snapshot.image or not rename_dialog_visible(
                ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
            ):
                raise PolicyViolation("收起遗留输入层后弹窗证据不足；不会继续点击")
        if proxy.observation is None:
            raise PolicyViolation("遗留改名弹窗缺少新观察")
        proxy.observation.text += "\n重新命名（输入层已处理；OCR 精确取消）"
        tap_cancel(proxy)
        snapshot = base._next_snapshot(proxy, 1.5)
        base._validate_expected("DETAIL", snapshot)
        emit("status", message="检测到上次遗留的改名弹窗；已取消未提交内容并恢复详情页。")
    return _ORIGINAL_NAVIGATE_V14(proxy, snapshot)


def run(mode: str, settings: Settings) -> int:
    with DeviceRunLock(settings.journal_path.parent / "iphone-mcp.lock"):
        previous_commit = v13._commit_with_transition_verification
        previous_navigate = v14.navigate_to_appraisal_v14
        previous_original = v14._ORIGINAL_NAVIGATE
        v13._commit_with_transition_verification = _commit_after_dismissing_keyboard
        v14.navigate_to_appraisal_v14 = _navigate_with_complete_stale_recovery
        v14._ORIGINAL_NAVIGATE = _navigate_with_read_only_measurement_retry
        try:
            return v16.run(mode, settings)
        finally:
            v13._commit_with_transition_verification = previous_commit
            v14.navigate_to_appraisal_v14 = previous_navigate
            v14._ORIGINAL_NAVIGATE = previous_original


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pure-Python locked recoverable iPad renamer v25"
    )
    parser.add_argument("--mode", choices=("scan", "rename"), required=True)
    args = parser.parse_args(argv)
    try:
        return run(args.mode, Settings.from_env())
    except Exception as exc:
        emit("error", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
