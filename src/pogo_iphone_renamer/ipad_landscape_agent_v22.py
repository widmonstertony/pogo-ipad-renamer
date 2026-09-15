from __future__ import annotations

import argparse
import json
import os
import time

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v13 as v13
from . import ipad_landscape_agent_v14 as v14
from .appraisal_agent import Snapshot
from . import ipad_landscape_agent_v16 as v16
from .config import Settings
from .device_run_lock import DeviceRunLock
from .ipad_landscape_agent_v10 import _mark_rename_observation
from .ipad_landscape_agent_v5 import exact_name_field
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
from .local_ocr_v4 import locate_exact_text_from_mcp
from .native_agent import emit
from .policy import PolicyViolation, normalize_text
from .rename_controls_v20 import tap_ok
from .rename_controls_v20 import tap_cancel
from .server import SafeProxy
from .protocol import text_from_content


_FIELD_READ_RETRY_LIMIT = 3
_DIALOG_READ_RETRY_LIMIT = 5
_SUBMIT_TAP_LIMIT = 4
_SUBMIT_OUTCOME_READ_LIMIT = 3
_CANCEL_DETAIL_READ_LIMIT = 5
# These are only the initial, read-only fast paths.  Every caller retains its
# existing bounded retry count and later, slower observations.  In particular,
# a fast frame never authorizes an extra OK tap by itself.
_FIELD_READ_RETRY_DELAY_SECONDS = 0.6
_FIRST_SUBMIT_OUTCOME_DELAY_SECONDS = 1.0
_LOCATION_RETRY_DELAY_SECONDS = 5.0
_LOCATION_WAIT_LIMIT_SECONDS = 30 * 60


class RenameFieldVerificationUnavailable(PolicyViolation):
    """The unsubmitted rename was cancelled and the original name preserved."""

    def __init__(self, snapshot, actual: str) -> None:
        super().__init__("输入字段在有限只读重测后仍不可核验；已取消未提交内容")
        self.snapshot = snapshot
        self.actual = actual


class RenameCancelDidNotDismiss(PolicyViolation):
    """A verified Cancel tap left the same rename dialog visibly open."""

    def __init__(self, snapshot: Snapshot) -> None:
        super().__init__("取消后改名弹窗仍由当前像素确认")
        self.snapshot = snapshot


class AccessibilityRuntimeUnavailable(PolicyViolation):
    """MCP explicitly reports its AX runtime unavailable, not a locked iPad."""


def _location_error_banner_visible(lines) -> bool:
    """Recognize Pokémon GO error 12 despite the common 偵測/偵側 OCR swap."""

    for line in lines:
        compact = normalize_text(getattr(line, "text", "")).replace(" ", "")
        if "無法" in compact and "目前位置" in compact and "12" in compact:
            return True
    return False


def _wait_for_submit_environment(
    proxy: SafeProxy, initial: Snapshot
) -> Snapshot:
    """Keep a proven edit untouched while location error 12 disables OK.

    Error 12 visibly greys the game's OK control. Repeatedly tapping that
    disabled control cannot work and used to exhaust the rename retry budget.
    This is an environmental wait only: screenshots are read, no text is
    rewritten, and no control is clicked until the banner has disappeared.
    """

    candidate = initial
    started = time.monotonic()
    attempt = 0
    while True:
        if not candidate.image:
            raise PolicyViolation("提交前缺少改名弹窗截图；未点击 OK")
        lines = ocr_mcp_screenshot(candidate.image, base.ORIENTATION)
        if not _location_error_banner_visible(lines):
            return candidate
        if not rename_dialog_visible(lines):
            raise PolicyViolation("位置错误横幅下无法同时证明改名弹窗；未点击 OK")
        elapsed = time.monotonic() - started
        if elapsed >= _LOCATION_WAIT_LIMIT_SECONDS:
            raise PolicyViolation(
                "位置错误 12 持续 30 分钟，OK 仍被游戏禁用；字段保持未提交"
            )
        attempt += 1
        emit(
            "waiting",
            screen="RENAME_DIALOG",
            stage="等待游戏恢复定位",
            reason="检测到‘无法侦测目前位置 (12)’；游戏已禁用 OK。",
            attempt=attempt,
            total=None,
            elapsed_seconds=int(elapsed),
            next_action="只读等待横幅消失；恢复后才点击一次 OK。",
            user_action="通常无需操作；请保持 Pokémon GO 在前台并允许定位。",
        )
        candidate = base._next_snapshot(proxy, _LOCATION_RETRY_DELAY_SECONDS)


def _ax_runtime_is_inactive(result: dict) -> bool:
    def inactive(value):
        if isinstance(value, dict):
            if value.get("axRuntimeMode") == "inactive":
                return True
            return any(inactive(child) for child in value.values())
        if isinstance(value, list):
            return any(inactive(child) for child in value)
        return False

    if inactive(result.get("structuredContent")):
        return True
    for item in result.get("content", []):
        if not isinstance(item, dict) or item.get("type") != "text":
            continue
        try:
            value = json.loads(item.get("text", ""))
        except (TypeError, ValueError):
            continue
        if inactive(value):
            return True
    return False


def _require_name_field_runtime(proxy: SafeProxy, current_name: str) -> None:
    """Fail before clearing when MCP itself says exact field reads cannot work."""
    observation = proxy.observation
    if observation is not None:
        try:
            if exact_name_field(Snapshot(observation.text, None)) == current_name:
                proxy._prefer_complete_field_tree = True
                return
        except PolicyViolation:
            pass
    started = time.monotonic()
    inactive_seen = False
    for attempt in range(1, 4):
        if attempt > 1:
            reset = getattr(getattr(proxy, "client", None), "reset_read_session", None)
            if callable(reset):
                reset()
        emit("waiting", screen="RENAME_DIALOG", stage="输入前完整字段核验",
             reason="确认当前输入框原名；MCP 空树只做有限只读重测，尚未清空或输入。",
             attempt=attempt, total=3, elapsed_seconds=int(time.monotonic() - started),
             next_action="完整原名一致后才清空；失败则取消并保留原名。",
             user_action="无需操作；不要手动点 OK。")
        result = proxy.call_tool("get_ui_elements", {
            "visible_only": False, "clickable_only": False, "limit": 160, "debug": True,
        })
        inactive_seen = inactive_seen or _ax_runtime_is_inactive(result)
        try:
            actual = exact_name_field(Snapshot(text_from_content(result), None))
        except PolicyViolation:
            actual = None
        if actual == current_name and not result.get("isError"):
            # Inactive diagnostics can coexist with a successful complete tree.
            # Exact current field evidence takes precedence over that heuristic.
            proxy._prefer_complete_field_tree = True
            return
        # A different actual field is identity disagreement, not a transient
        # empty tree. Stop without attempting to erase or overwrite it.
        if actual is not None and actual != current_name:
            raise AccessibilityRuntimeUnavailable("完整字段与已核验原名不一致；未输入或提交")
        if attempt < 3:
            time.sleep(_FIELD_READ_RETRY_DELAY_SECONDS)
    if inactive_seen:
        raise AccessibilityRuntimeUnavailable(
            "MCP 连续三次完整字段读取仍失败（axRuntimeMode: inactive）；"
            "已保留原名，需要恢复 MCP 字段读取服务，不是 iPad 锁屏。"
        )
    raise AccessibilityRuntimeUnavailable("清空前完整辅助功能树未读到一致的原名；未输入或提交")


def _focus_ocr_default_name_field(proxy: SafeProxy, current_name: str) -> None:
    """Focus the exact field from the current dialog pixels before clearing.

    A visible iPad rename dialog does not guarantee the keyboard target is
    active.  In that state MCP can acknowledge ``input_text`` while the field
    remains unchanged.  The already-proven default name is the only OCR text
    we use as a focus target; no fixed dialog point or inferred string is used.
    """

    dialog = base._next_snapshot(proxy, 0.2)
    if not dialog.image or not rename_dialog_visible(
        ocr_mcp_screenshot(dialog.image, base.ORIENTATION)
    ):
        raise PolicyViolation("改名弹窗未在聚焦名称字段前得到像素验证；未输入文字")
    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("聚焦名称字段时缺少安全观察")
    base._remember_stage_geometry(proxy, dialog)
    located = locate_exact_text_from_mcp(
        dialog.image,
        base.ORIENTATION,
        current_name,
        minimum_confidence=0.70,
        search_region=(0.10, 0.30, 0.70, 0.55),
    )
    x_ratio = (located.box.left + located.box.right) / (2.0 * located.image_width)
    y_ratio = located.box.center_y / located.image_height
    if not (0.10 <= x_ratio <= 0.70 and 0.30 <= y_ratio <= 0.55):
        raise PolicyViolation("OCR 默认名称不在改名字段安全区域；未输入文字")
    x, y = base.upright_ratio_to_touch(
        observation.width,
        observation.height,
        x_ratio,
        y_ratio,
        geometry=base.current_stage_geometry(proxy),
    )
    proxy.call_tool(
        "tap_screen",
        {
            "x": x,
            "y": y,
            "_observation_token": observation.token,
            "_intent": "navigate OCR-verified default name field before clearing and rename input",
            "_expected_after": "rename text field focused",
        },
    )
    _require_name_field_runtime(proxy, current_name)


def _dialog_contains_exact_text(proxy: SafeProxy, text: str) -> bool:
    """Read a live dialog and preserve visual proof for a possible text write."""

    dialog = base._next_snapshot(proxy, 0.3)
    if not dialog.image or not rename_dialog_visible(
        ocr_mcp_screenshot(dialog.image, base.ORIENTATION)
    ):
        return False
    visible = any(
        line.text == text and line.confidence >= 0.65
        for line in ocr_mcp_screenshot(dialog.image, base.ORIENTATION)
    )
    if visible and proxy.observation is not None:
        proxy.observation.text += "\n重新命名（离线 OCR 已验证当前改名弹窗）"
    return visible


def _persistent_task_switcher_wait_enabled() -> bool:
    return os.getenv("POGO_PERSIST_CAPTURE_WAIT", "false").strip().casefold() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _task_switcher_overlay_active(proxy: SafeProxy) -> bool:
    observation = proxy.observation
    if observation is None:
        return False
    text = str(observation.text).casefold()
    return "程序坞" in text or "dock" in text


def _wait_for_task_switcher_to_clear(proxy: SafeProxy) -> bool:
    """Read only until an overview clears, without trusting stale AX alone.

    Stage Manager can leave a previous Dock node in the accessibility tree
    after the real pixels have returned to Pokémon GO.  Treating that stale
    node as a system overlay indefinitely freezes a fully visible, already
    verified rename dialog.  A fresh local screenshot is authoritative here:
    if it proves the rename dialog, return to the normal field-verification
    path without tapping anything.
    """

    if not (
        _persistent_task_switcher_wait_enabled()
        and _task_switcher_overlay_active(proxy)
    ):
        return False
    emit(
        "status",
        message=(
            "iPad 多任务切换层覆盖改名界面；后台保持运行并只读等待，"
            "不会点击 OK、取消或其他 App。"
        ),
    )
    while _task_switcher_overlay_active(proxy):
        snapshot = base._next_snapshot(proxy, 3.0)
        try:
            if base.local_page_state(snapshot) == "RENAME_DIALOG":
                emit(
                    "status",
                    message=(
                        "改名弹窗已由当前游戏像素确认；忽略残留的 Dock 辅助功能节点，"
                        "继续只读核验输入字段。"
                    ),
                )
                return True
        except (PolicyViolation, ValueError, OSError):
            # A transient unreadable frame never authorizes a touch.  Keep the
            # original read-only wait and let the next screenshot decide.
            pass
    return True


def _wait_for_detail_after_cancel(proxy: SafeProxy, snapshot: Snapshot) -> Snapshot:
    """Read only until a cancelled edit has returned to the same DETAIL page.

    After a verified Cancel, a stale Stage Manager accessibility tree can keep
    reporting Dock/card text long after the game pixels have changed.  The old
    branch treated the moment that stale text disappeared as evidence that the
    direct-detail route had failed, then let a legacy validation message abort
    the batch.  A cancel never authorizes inventory navigation, so the only
    safe recovery is to keep reading until the existing detail page itself is
    proven again.  In particular, this helper never taps a card, launches the
    game, or retries Cancel/OK.
    """

    wait_started = time.monotonic()
    last_reported = wait_started
    while True:
        # Fresh local pixels are stronger than a retained Stage Manager AX
        # tree.  In particular, a cancelled dialog can already be back on its
        # original detail page while accessibility still describes the former
        # keyboard/Dock surface; waiting for that stale text to change caused
        # multi-hour no-progress loops.
        try:
            local_state = base.local_page_state(snapshot)
            if local_state == "DETAIL":
                return snapshot
            if local_state == "RENAME_DIALOG":
                # The bounded Cancel recovery already gave the game several
                # read-only frames to close.  Continuing to wait for DETAIL
                # here cannot make a visibly unchanged dialog disappear.  Let
                # the batch layer decide whether its durable journal can
                # safely resume the exact pending rename instead.
                raise RenameCancelDidNotDismiss(snapshot)
        except RenameCancelDidNotDismiss:
            raise
        except (PolicyViolation, ValueError, OSError):
            pass
        try:
            base._validate_expected("DETAIL", snapshot)
        except (PolicyViolation, ValueError):
            snapshot = base._next_snapshot(proxy, 3.0)
            now = time.monotonic()
            if now - last_reported >= 15.0:
                elapsed = max(0, int(now - wait_started))
                emit(
                    "status",
                    message=(
                        f"取消未提交编辑后仍在只读等待同一只详情页（已 {elapsed} 秒）；"
                        "不会点击盒子卡片、OK、取消或重开游戏。"
                    ),
                )
                last_reported = now
            continue
        return snapshot


def _verified_entered_value_with_read_only_retry(
    proxy: SafeProxy, nickname: str
) -> str:
    last = ""
    wait_started = time.monotonic()
    for attempt in range(1, _FIELD_READ_RETRY_LIMIT + 1):
        emit(
            "waiting",
            screen="RENAME_DIALOG",
            stage="昵称逐字核验",
            reason="正在读取输入框的完整字符；未与目标逐字一致前不会点击 OK。",
            attempt=attempt,
            total=_FIELD_READ_RETRY_LIMIT,
            elapsed_seconds=max(0, int(time.monotonic() - wait_started)),
            next_action="只读核验完整昵称；重试耗尽则取消未提交编辑。",
            user_action="无需操作；不要手动点 OK，以免跳过完整字符核验。",
        )
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
        if _dialog_contains_exact_text(proxy, nickname):
            if attempt > 1:
                emit(
                    "status",
                    message=f"输入字段在第 {attempt} 次视觉复核时恢复并逐字一致。",
                )
            return nickname
        if attempt < _FIELD_READ_RETRY_LIMIT:
            emit(
                "status",
                message=(
                    f"输入字段第 {attempt} 次未返回完整值；"
                    "只读取等待 accessibility 稳定，不重复输入、不点击 OK。"
                ),
            )
            base._next_snapshot(proxy, _FIELD_READ_RETRY_DELAY_SECONDS)
    return last


def _cancel_unverified_input(proxy: SafeProxy, actual: str) -> None:
    """Cancel an unsubmitted edit and read-only wait for DETAIL.

    Stage Manager can expose one transient composition immediately after the
    cancel tap.  Treating that single frame as an inventory page used to make
    the outer batch state machine click the first card again.  After a cancel
    there is no valid reason to touch the screen until DETAIL is proven, so
    retry only the observation and never re-enter navigation here.
    """

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
    try:
        tap_cancel(proxy)
    except PolicyViolation as cancel_error:
        # The game sometimes keeps the dialog in accessibility while the
        # locally-captured frame has already dropped its Cancel text.  The
        # exact, unique accessibility control is a safer recovery than
        # guessing a second OCR coordinate: it is fresh, clickable, and must
        # still be on this proven rename dialog.  It only cancels the pending
        # edit; it cannot submit a nickname.
        if _tap_accessibility_cancel(proxy):
            cancel_error = None
        else:
            # The rename field can disappear between the first dialog proof
            # and the OCR-controlled Cancel read.  This has no successful-
            # commit evidence (we never pressed OK), so do not try another
            # coordinate or restart the batch.  A fresh, proven DETAIL frame
            # is enough to leave this one unrecorded and continue; the next
            # pass will still preserve it if the game happened to show a
            # custom name.
            detail = _wait_for_detail_after_cancel(
                proxy, base._next_snapshot(proxy, 0.5)
            )
            proxy.pending_name = None
            emit("navigation", state="DETAIL")
            emit(
                "status",
                message=(
                    "取消控件在最终截图中已消失；已只读确认详情页，"
                    "未记录本只改名并安全继续。"
                ),
            )
            raise RenameFieldVerificationUnavailable(detail, actual) from cancel_error
    for attempt in range(1, _CANCEL_DETAIL_READ_LIMIT + 1):
        detail = base._next_snapshot(proxy, 1.5 if attempt == 1 else 1.0)
        try:
            base._validate_expected("DETAIL", detail)
        except (PolicyViolation, ValueError) as exc:
            if attempt < _CANCEL_DETAIL_READ_LIMIT:
                emit(
                    "status",
                    message=(
                        f"取消后详情页第 {attempt} 帧尚未稳定；"
                        "只读等待，不会重新点击盒子卡片。"
                    ),
                )
                continue
            break
        proxy.pending_name = None
        emit("navigation", state="DETAIL")
        raise RenameFieldVerificationUnavailable(detail, actual)

    # Do not use stale Dock/card AX labels as a timeout condition.  Once the
    # verified Cancel has been tapped, detail recovery is intentionally
    # read-only and unbounded: the worker resumes only when the same Pokémon
    # detail is visibly proven, never by trying a generic box/card route.
    try:
        detail = _wait_for_detail_after_cancel(proxy, detail)
    except RenameCancelDidNotDismiss as still_open:
        proxy.pending_name = None
        raise RenameFieldVerificationUnavailable(still_open.snapshot, actual) from still_open
    proxy.pending_name = None
    emit("navigation", state="DETAIL")
    raise RenameFieldVerificationUnavailable(detail, actual)


def _submit_with_one_verified_retry(
    proxy: SafeProxy,
    *,
    nickname: str,
    prefer_accessibility_first: bool = False,
    initial_dialog: Snapshot | None = None,
):
    """Submit an exactly verified field with bounded, evidence-gated retries.

    Pokémon GO can consume the first tap while the iOS input layer is still
    settling, or keep the dialog visible for several seconds while the rename
    request is processed.  The old two-tap flow treated either case as a hard
    failure.  Every retry here is authorized only after a fresh read proves
    that the same exact nickname is still in the live field.
    """

    if initial_dialog is not None:
        _wait_for_submit_environment(proxy, initial_dialog)

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
            try:
                tap_ok(proxy)
            except PolicyViolation:
                # A retry can arrive exactly as the previous OK begins to
                # dismiss the dialog. In that transition frame OCR quite
                # correctly cannot locate another OK control. Treating that
                # absence as a rename failure used to stop a healthy batch.
                # Do one fresh read only: a proven DETAIL is conclusive
                # success, while every other page still fails safely without
                # introducing an unproven extra tap.
                if attempt == 0:
                    raise
                transition = base._next_snapshot(proxy, 1.25)
                if transition.image and v14.robust_page_state(transition) == "DETAIL":
                    emit(
                        "status",
                        message=(
                            "重试时 OK 已随页面过渡消失；只读确认返回详情页，"
                            "不会发送额外点击。"
                        ),
                    )
                    return transition
                raise

        candidate = None
        dialog_visible = False
        for read_attempt in range(1, _SUBMIT_OUTCOME_READ_LIMIT + 1):
            candidate = base._next_snapshot(
                proxy,
                (
                    _FIRST_SUBMIT_OUTCOME_DELAY_SECONDS
                    if attempt == 0 and read_attempt == 1
                    else 1.25
                ),
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


def _tap_accessibility_cancel(proxy: SafeProxy) -> bool:
    """Cancel when local OCR is stale, preserving Stage Manager calibration."""

    observation = proxy.observation
    if observation is None:
        raise PolicyViolation("accessibility 取消缺少安全观察")

    # Accessibility exposes this iPad's text field on a 1024-point portrait
    # surface, while tap_screen uses the 1366×1024 landscape desktop.  Those
    # coordinates are not interchangeable in Stage Manager.  The dialog was
    # already jointly proven by its exact field and controls above, so use the
    # calibrated in-window Cancel anchor rather than reusing the portrait AX
    # point.  It is mapped through the freshly measured game-window bounds.
    if (
        base.ORIENTATION in {
            "STAGE_MANAGER_MAXIMIZED",
            "STAGE_MANAGER_PORTRAIT_WINDOW",
        }
        and observation.width is not None
        and observation.height is not None
        and observation.width > observation.height
    ):
        base._tap(proxy, "RENAME_CANCEL")
        emit("status", message="本次取消使用已校准的 Stage Manager 取消锚点。")
        return True

    point = exact_accessibility_tap_point(proxy, "取消")
    if point is None:
        return False
    proxy.call_tool(
        "tap_screen",
        {
            "x": point[0],
            "y": point[1],
            "_observation_token": observation.token,
            "_intent": "navigate exact accessibility cancel rename dialog without submitting",
            "_expected_after": "DETAIL",
        },
    )
    emit("status", message="本次取消使用 accessibility 返回的精确取消触点。")
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
) -> Snapshot:
    verified_before = proxy.verified_renames
    try:
        _focus_ocr_default_name_field(proxy, current_name)
    except AccessibilityRuntimeUnavailable as error:
        emit("status", message=str(error) + " 尚未清空或输入；正在关闭未修改的名称窗口。")
        try:
            _cancel_unverified_input(proxy, current_name)
        except RenameFieldVerificationUnavailable:
            raise error
        raise error
    count = _backspace_current_name(proxy, current_name)
    if _dialog_contains_exact_text(proxy, current_name):
        emit(
            "status",
            message="名称字段在清空后仍完整显示原名；不会输入或提交，正在取消本次编辑。",
        )
        _cancel_unverified_input(proxy, current_name)
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
        # ``input_text`` may report transport success while a Stage Manager
        # dialog kept the default value unchanged.  The MCP contract permits
        # exactly one ``type_text`` fallback in that case, never after a
        # partial/unknown field value.
        if _dialog_contains_exact_text(proxy, current_name):
            assert proxy.observation is not None
            proxy.call_tool(
                "type_text",
                {
                    "text": nickname,
                    "_observation_token": proxy.observation.token,
                    "_intent": "rename exact default species using deterministic pixel IV nickname fallback",
                    "_expected_after": "rename field contains exact deterministic nickname",
                    "_current_name": current_name,
                    "_species": species,
                    "_default_name_verified": True,
                    "_fallback_default_field_verified": True,
                },
            )
            entered_value = _verified_entered_value_with_read_only_retry(proxy, nickname)
        # A task-switcher frame can expose another app's accessibility text
        # (for example “账号安全”) while the unsubmitted Pokémon GO field is
        # still underneath.  Wait for the overlay to clear before deciding
        # whether cancellation is necessary; never tap either dialog button
        # while that system layer is visible.
        if entered_value != nickname and _wait_for_task_switcher_to_clear(proxy):
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
    dialog = None
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
        initial_dialog=dialog,
    )

    _finalize_verified_commit(
        proxy,
        verified_before=verified_before,
        current_name=current_name,
        species=species,
        nickname=nickname,
    )
    # _submit_with_one_verified_retry already proved that this is DETAIL and
    # that the rename dialog disappeared.  Returning the same fresh snapshot
    # avoids a redundant screenshot immediately afterward in batch mode.
    return detail


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
