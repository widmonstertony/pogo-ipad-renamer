from __future__ import annotations

import argparse

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v13 as v13
from . import ipad_landscape_agent_v14 as v14
from . import ipad_landscape_agent_v16 as v16
from .appraisal_agent import Snapshot
from .config import Settings
from .device_run_lock import DeviceRunLock
from .ipad_landscape_agent_v10 import _field_value, _mark_rename_observation
from .ipad_landscape_agent_v12 import _backspace_current_name
from .ipad_landscape_agent_v5 import exact_name_field
from .local_ocr import ocr_mcp_screenshot, rename_dialog_visible
from .native_agent import emit, tool_result_message
from .policy import PolicyViolation
from .rename_controls_v20 import tap_cancel, tap_ok
from .server import SafeProxy


def _field_value_from_all_elements(proxy: SafeProxy) -> str:
    result = proxy.call_tool("get_ui_elements", {})
    message = tool_result_message("get_ui_elements", result)
    return exact_name_field(Snapshot(str(message.get("content", "")), None))


def _verified_entered_value(proxy: SafeProxy) -> str:
    try:
        return _field_value(proxy)
    except PolicyViolation:
        pass
    try:
        return _field_value_from_all_elements(proxy)
    except PolicyViolation:
        pass
    base._next_snapshot(proxy, 0.6)
    return _field_value(proxy)


def _commit_with_dynamic_controls(
    proxy: SafeProxy,
    *,
    current_name: str,
    species: str,
    nickname: str,
) -> None:
    count = _backspace_current_name(proxy, current_name)
    _mark_rename_observation(proxy, f"已发送与精确原名等长的 {count} 次退格")
    emit("status", message=f"已清除原名称的 {count} 个字符；正在输入并逐字核验目标昵称。")

    assert proxy.observation is not None
    proxy.call_tool(
        "input_text",
        {
            "text": nickname,
            "_observation_token": proxy.observation.token,
            "_intent": "rename exact default species using deterministic pixel IV nickname",
            "_expected_after": "rename field contains exact deterministic nickname",
            "_current_name": current_name,
            "_species": species,
            "_default_name_verified": True,
        },
    )
    entered_value = _verified_entered_value(proxy)
    if entered_value != nickname:
        raise PolicyViolation(
            f"输入后字段不完全一致：期望 {nickname!r}，实际 {entered_value!r}；未点击 OK"
        )
    emit("status", message="完整昵称逐字核验通过；正在定位并点击当前 OK 按钮。")

    tap_ok(proxy)
    detail = base._next_snapshot(proxy, 3.0)
    base._validate_expected("DETAIL", detail)
    if not detail.image:
        raise PolicyViolation("提交后详情页截图缺失")
    if rename_dialog_visible(ocr_mcp_screenshot(detail.image, base.ORIENTATION)):
        raise PolicyViolation("点击 OCR 定位的 OK 后改名弹窗仍可见；未记录成功")

    if proxy.verified_renames < 1:
        proxy.verified_renames += 1
        proxy.pending_name = None
        proxy.journal.append(
            "verified_rename_dynamic_ok",
            {
                "species": species,
                "old_name": current_name,
                "new_name": nickname,
                "evidence": "exact field + OCR-located OK + dialog gone + DETAIL",
            },
        )


_ORIGINAL_NAVIGATE_V14 = v14.navigate_to_appraisal_v14


def _navigate_with_stale_dialog_recovery(proxy: SafeProxy, snapshot: Snapshot):
    if snapshot.image and rename_dialog_visible(
        ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
    ):
        if proxy.observation is None:
            raise PolicyViolation("遗留改名弹窗缺少安全观察")
        proxy.observation.text += "\n重新命名（离线 OCR 验证遗留弹窗；仅取消恢复）"
        tap_cancel(proxy)
        snapshot = base._next_snapshot(proxy, 1.5)
        base._validate_expected("DETAIL", snapshot)
        emit("status", message="检测到上次遗留的改名弹窗；已取消未提交内容并恢复详情页。")
    return _ORIGINAL_NAVIGATE_V14(proxy, snapshot)


def run(mode: str, settings: Settings) -> int:
    lock_path = settings.journal_path.parent / "iphone-mcp.lock"
    with DeviceRunLock(lock_path):
        previous_commit = v13._commit_with_transition_verification
        previous_navigate = v14.navigate_to_appraisal_v14
        v13._commit_with_transition_verification = _commit_with_dynamic_controls
        v14.navigate_to_appraisal_v14 = _navigate_with_stale_dialog_recovery
        try:
            return v16.run(mode, settings)
        finally:
            v13._commit_with_transition_verification = previous_commit
            v14.navigate_to_appraisal_v14 = previous_navigate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pure-Python locked deterministic iPad renamer v20"
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
