from __future__ import annotations

import argparse

from . import device_controller as base
from . import rename_transition as transition
from . import game_navigation as navigation
from . import rename_dialog as rename_dialog
from .config import Settings
from .device_run_lock import DeviceRunLock
from .field_verification import _ORIGINAL_NAVIGATE
from .rename_submission import _commit_after_dismissing_keyboard
from .appraisal_retry import _navigate_with_read_only_measurement_retry
from .keyboard_control import dismiss_active_keyboard
from .local_ocr import ocr_mcp_screenshot, rename_dialog_visible
from .native_agent import emit
from .policy import PolicyViolation
from .rename_controls import tap_cancel
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
    return _ORIGINAL_NAVIGATE(proxy, snapshot)


def run(mode: str, settings: Settings) -> int:
    with DeviceRunLock(settings.journal_path.parent / "iphone-mcp.lock"):
        previous_commit = transition._commit_with_transition_verification
        previous_navigate = navigation.navigate_to_appraisal
        previous_original = navigation._ORIGINAL_NAVIGATE
        transition._commit_with_transition_verification = _commit_after_dismissing_keyboard
        navigation.navigate_to_appraisal = _navigate_with_complete_stale_recovery
        navigation._ORIGINAL_NAVIGATE = _navigate_with_read_only_measurement_retry
        try:
            return rename_dialog.run(mode, settings)
        finally:
            transition._commit_with_transition_verification = previous_commit
            navigation.navigate_to_appraisal = previous_navigate
            navigation._ORIGINAL_NAVIGATE = previous_original


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pure-Python locked recoverable iPad renamer"
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
