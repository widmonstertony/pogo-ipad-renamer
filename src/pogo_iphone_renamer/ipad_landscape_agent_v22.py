from __future__ import annotations

import argparse

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v13 as v13
from . import ipad_landscape_agent_v14 as v14
from . import ipad_landscape_agent_v16 as v16
from .config import Settings
from .device_run_lock import DeviceRunLock
from .ipad_landscape_agent_v10 import _mark_rename_observation
from .ipad_landscape_agent_v12 import _backspace_current_name
from .ipad_landscape_agent_v20 import (
    _navigate_with_stale_dialog_recovery,
    _verified_entered_value,
)
from .keyboard_control_v22 import (
    dismiss_active_keyboard,
    exact_accessibility_tap_point,
)
from .local_ocr import ocr_mcp_screenshot, rename_dialog_visible
from .native_agent import emit
from .policy import PolicyViolation, normalize_text
from .rename_controls_v20 import tap_ok
from .rename_controls_v20 import tap_cancel
from .server import SafeProxy


_FIELD_READ_RETRY_LIMIT = 3
_DIALOG_READ_RETRY_LIMIT = 5
_SUBMIT_TAP_LIMIT = 4
_SUBMIT_OUTCOME_READ_LIMIT = 3


class RenameFieldVerificationUnavailable(PolicyViolation):
    """The unsubmitted rename was cancelled and the original name preserved."""

    def __init__(self, snapshot, actual: str) -> None:
        super().__init__("输入字段在有限只读重测后仍不可核验；已取消未提交内容")
        self.snapshot = snapshot
        self.actual = actual


def _verified_entered_value_with_read_only_retry(
    proxy: SafeProxy, nickname: str
) -> str:
    last = ""
    for attempt in range(1, _FIELD_READ_RETRY_LIMIT + 1):
        try:
            last = _verified_entered_value(proxy)
        except PolicyViolation:
            last = ""
        if last == nickname:
            if attempt > 1:
                emit(
                    "status",
                    message=f"输入字段在第 {attempt} 次只读复核时恢复并逐字一致。",
                )
            return last
        if attempt < _FIELD_READ_RETRY_LIMIT:
            emit(
                "status",
                message=(
                    f"输入字段第 {attempt} 次未返回完整值；"
                    "只读取等待 accessibility 稳定，不重复输入、不点击 OK。"
                ),
            )
            base._next_snapshot(proxy, 0.8)
    return last


def _cancel_unverified_input(proxy: SafeProxy, actual: str) -> None:
    """Cancel an unsubmitted edit and prove return to DETAIL."""

    dismissed = dismiss_active_keyboard(proxy)
    dialog = base._next_snapshot(proxy, 0.8 if dismissed else 0.4)
    if not dialog.image or not rename_dialog_visible(
        ocr_mcp_screenshot(dialog.image, base.ORIENTATION)
    ):
        raise PolicyViolation(
            "输入字段不可核验且无法确认改名弹窗仍在；未点击 OK，也未猜测取消位置"
        )
    if proxy.observation is None:
        raise PolicyViolation("取消不可核验输入前缺少安全观察")
    proxy.observation.text += "\n重新命名（输入字段不可核验；仅取消恢复，不提交）"
    tap_cancel(proxy)
    detail = base._next_snapshot(proxy, 1.5)
    base._validate_expected("DETAIL", detail)
    proxy.pending_name = None
    raise RenameFieldVerificationUnavailable(detail, actual)


def _submit_with_one_verified_retry(
    proxy: SafeProxy, *, nickname: str, prefer_accessibility_first: bool = False
):
    """Submit an exactly verified field with bounded, evidence-gated retries.

    Pokémon GO can consume the first tap while the iOS input layer is still
    settling, or keep the dialog visible for several seconds while the rename
    request is processed.  The old two-tap flow treated either case as a hard
    failure.  Every retry here is authorized only after a fresh read proves
    that the same exact nickname is still in the live field.
    """

    for attempt in range(_SUBMIT_TAP_LIMIT):
        if attempt > 0:
            entered_value = _verified_entered_value_with_read_only_retry(
                proxy, nickname
            )
            if entered_value != nickname:
                pending = normalize_text(proxy.pending_name or "")
                if dialog_visible and pending == normalize_text(nickname):
                    # iOS sometimes removes the text-field accessibility value
                    # after the first OK merely dismisses the input layer.  The
                    # exact value was proven immediately before the first tap,
                    # SafeProxy still owns the same pending value, the complete
                    # rename dialog is freshly OCR-verified, and no intervening
                    # text write is permitted.  Retrying the unchanged OK is
                    # therefore as safe as the original verified tap.
                    emit(
                        "status",
                        message=(
                            "accessibility 暂未返回输入框值；同一改名弹窗和"
                            "安全代理中的完整待提交昵称仍一致，继续有界重试 OK。"
                        ),
                    )
                else:
                    emit(
                        "status",
                        message=(
                            "提交重试前无法再次证明昵称字段或待提交值；"
                            "不会继续点击，正在取消未提交编辑。"
                        ),
                    )
                    _cancel_unverified_input(proxy, entered_value)

        use_accessibility = prefer_accessibility_first or attempt > 0
        if not use_accessibility or not _tap_accessibility_ok(proxy):
            tap_ok(proxy)

        candidate = None
        dialog_visible = False
        for read_attempt in range(1, _SUBMIT_OUTCOME_READ_LIMIT + 1):
            candidate = base._next_snapshot(
                proxy,
                3.0 if attempt == 0 and read_attempt == 1 else 1.25,
            )
            if not candidate.image:
                if read_attempt < _SUBMIT_OUTCOME_READ_LIMIT:
                    emit(
                        "status",
                        message="提交后的截图通道暂时为空；只读等待，不重复点击。",
                    )
                    continue
                raise PolicyViolation("提交后连续截图缺失")

            dialog_visible = rename_dialog_visible(
                ocr_mcp_screenshot(candidate.image, base.ORIENTATION)
            )
            if dialog_visible:
                # One additional read distinguishes a slow response from an
                # immediately unchanged dialog without issuing another tap.
                if read_attempt < 2:
                    emit(
                        "status",
                        message="OK 后弹窗仍显示；先只读等待响应，不立即重试。",
                    )
                    continue
                break
            if v14.robust_page_state(candidate) == "DETAIL":
                return candidate
            if read_attempt < _SUBMIT_OUTCOME_READ_LIMIT:
                emit(
                    "status",
                    message="提交后处于页面过渡；只读等待稳定，不重复点击。",
                )

        assert candidate is not None
        if dialog_visible:
            if attempt + 1 < _SUBMIT_TAP_LIMIT:
                emit(
                    "status",
                    message=(
                        f"第 {attempt + 1} 次 OK 后同一改名弹窗仍完整显示；"
                        "下一次点击前会重新逐字核验字段。"
                    ),
                )
                continue
            emit(
                "status",
                message=(
                    f"连续 {_SUBMIT_TAP_LIMIT} 次有证据的 OK 后弹窗仍显示；"
                    "不会无限点击，正在取消并保留原名。"
                ),
            )
            _cancel_unverified_input(proxy, nickname)

        if v14.robust_page_state(candidate) == "DETAIL":
            return candidate
        raise PolicyViolation("点击 OK 后既未验证到详情页，也未发现仍可取消的改名弹窗")

    raise PolicyViolation("改名提交有界重试流程未返回可验证页面")


def _dialog_evidence_after_keyboard_dismiss(
    proxy: SafeProxy,
):
    """Wait through transient empty OCR and prove the unchanged rename dialog.

    The nickname field has already been verified exactly before this helper is
    called.  This helper performs reads only.  It accepts either a fresh local
    OCR proof or both live accessibility buttons from the same dialog; the
    latter lets submission use the exact accessibility OK point instead of
    depending on another OCR frame.
    """

    last = None
    for attempt in range(1, _DIALOG_READ_RETRY_LIMIT + 1):
        last = base._next_snapshot(proxy, 0.8 if attempt == 1 else 1.0)
        if last.image and rename_dialog_visible(
            ocr_mcp_screenshot(last.image, base.ORIENTATION)
        ):
            if attempt > 1:
                emit(
                    "status",
                    message=f"改名弹窗在第 {attempt} 次只读复核时恢复。",
                )
            return last, False

        # get_ui_elements is a fresh read of the current screen.  Requiring
        # both exact, unique and clickable controls prevents a stale lone
        # label from authorizing a write.
        ok_point = exact_accessibility_tap_point(proxy, "OK")
        cancel_point = exact_accessibility_tap_point(proxy, "取消")
        if ok_point is not None and cancel_point is not None:
            emit(
                "status",
                message=(
                    "本地 OCR 暂未返回弹窗文字；accessibility 已同时验证到"
                    "精确 OK/取消控件，将使用精确 OK 触点。"
                ),
            )
            return last, True

        if attempt < _DIALOG_READ_RETRY_LIMIT:
            emit(
                "status",
                message=(
                    f"收起输入层后的第 {attempt} 帧未完整显示改名弹窗；"
                    "只读等待界面稳定，不重复输入、不点击。"
                ),
            )

    raise PolicyViolation(
        "收起输入层后连续只读复核仍无法证明改名弹窗；未点击 OK"
    )


def _tap_accessibility_ok(proxy: SafeProxy) -> bool:
    """Use MCP's exact clickable OK point as the bounded retry channel."""

    point = exact_accessibility_tap_point(proxy, "OK")
    if point is None:
        return False
    observation = proxy.observation
    if observation is None:
        raise PolicyViolation("accessibility OK 缺少安全观察")
    proxy.call_tool(
        "tap_screen",
        {
            "x": point[0],
            "y": point[1],
            "_observation_token": observation.token,
            "_intent": "rename submit exact verified nickname using accessibility OK retry",
            "_expected_after": "DETAIL",
        },
    )
    emit("status", message="本次提交使用 accessibility 返回的精确 OK 触点。")
    return True


def _finalize_verified_commit(
    proxy: SafeProxy,
    *,
    verified_before: int,
    current_name: str,
    species: str,
    nickname: str,
) -> None:
    expected_count = verified_before + 1
    if proxy.pending_name is None:
        if proxy.verified_renames != expected_count:
            raise PolicyViolation("提交后待确认状态已消失，但本轮成功计数不一致")
        return

    if normalize_text(proxy.pending_name) != normalize_text(nickname):
        raise PolicyViolation("提交后的待确认昵称与本轮目标昵称不一致")
    if proxy.verified_renames != verified_before:
        raise PolicyViolation("提交后的待确认状态与本轮成功计数不一致")

    proxy.verified_renames = expected_count
    proxy.pending_name = None
    proxy.journal.append(
        "verified_rename_keyboard_dismissed_dynamic_ok",
        {
            "species": species,
            "old_name": current_name,
            "new_name": nickname,
            "evidence": "exact field + keyboard dismissed + OCR OK + dialog gone + DETAIL",
        },
    )


def _commit_after_dismissing_keyboard(
    proxy: SafeProxy,
    *,
    current_name: str,
    species: str,
    nickname: str,
) -> None:
    verified_before = proxy.verified_renames
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
    entered_value = _verified_entered_value_with_read_only_retry(proxy, nickname)
    if entered_value != nickname:
        emit(
            "status",
            message=(
                f"输入后字段未能逐字核验：期望 {nickname!r}，实际 {entered_value!r}；"
                "不会点击 OK，正在取消本次未提交编辑。"
            ),
        )
        _cancel_unverified_input(proxy, entered_value)
    emit("status", message="完整昵称逐字核验通过；正在安全收起输入层。")

    dismissed = dismiss_active_keyboard(proxy)
    prefer_accessibility_first = False
    if dismissed:
        dialog, prefer_accessibility_first = (
            _dialog_evidence_after_keyboard_dismiss(proxy)
        )
        assert proxy.observation is not None
        evidence = "accessibility" if prefer_accessibility_first else "OCR"
        proxy.observation.text += (
            f"\n重新命名（键盘已收起；{evidence} 验证弹窗仍在）"
        )
    emit("status", message="正在按当前截图定位并点击 OK。")

    detail = _submit_with_one_verified_retry(
        proxy,
        nickname=nickname,
        prefer_accessibility_first=prefer_accessibility_first,
    )

    _finalize_verified_commit(
        proxy,
        verified_before=verified_before,
        current_name=current_name,
        species=species,
        nickname=nickname,
    )


def run(mode: str, settings: Settings) -> int:
    with DeviceRunLock(settings.journal_path.parent / "iphone-mcp.lock"):
        previous_commit = v13._commit_with_transition_verification
        previous_navigate = v14.navigate_to_appraisal_v14
        v13._commit_with_transition_verification = _commit_after_dismissing_keyboard
        v14.navigate_to_appraisal_v14 = _navigate_with_stale_dialog_recovery
        try:
            return v16.run(mode, settings)
        finally:
            v13._commit_with_transition_verification = previous_commit
            v14.navigate_to_appraisal_v14 = previous_navigate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pure-Python locked iPad renamer with keyboard dismissal v22"
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
