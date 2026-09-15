from __future__ import annotations

import argparse

from . import device_controller as base
from . import rename_transition as transition
from . import game_navigation as navigation
from . import rename_dialog as rename_dialog
from .config import Settings
from .device_run_lock import DeviceRunLock
from .field_verification import _navigate_with_stale_dialog_recovery
from .device_recovery import wait_for_capture_channel
from .rename_submission import _commit_after_dismissing_keyboard
from .native_agent import emit
from .policy import PolicyViolation


_BASE_ORIGINAL_NAVIGATE = navigation._ORIGINAL_NAVIGATE
_READ_ONLY_RETRY_LIMIT = 6
# The normal iPad path reaches the leader dialogue before the bars.  Start
# observing it sooner, then keep the historical slower retry cadence if it is
# still settling.  No additional dialogue tap or measurement acceptance path
# is introduced by this tuning.
_FIRST_APPRAISAL_READ_DELAY_SECONDS = 1.0
_FIRST_DIALOG_READ_DELAY_SECONDS = 1.75


class AppraisalMeasurementUnavailable(PolicyViolation):
    """Stable appraisal bars were not available after bounded read-only waits."""

    def __init__(self, snapshot, cause: ValueError) -> None:
        super().__init__(
            "鉴定条在有限只读重测后仍不可读；当前宝可梦未改名"
        )
        self.snapshot = snapshot
        self.cause = cause


def _navigate_with_read_only_measurement_retry(proxy, snapshot):
    try:
        return _BASE_ORIGINAL_NAVIGATE(proxy, snapshot)
    except ValueError as first_error:
        last_error: ValueError = first_error
        last_snapshot = base._next_snapshot(
            proxy, _FIRST_APPRAISAL_READ_DELAY_SECONDS
        )
        if navigation.snapshot_is_black(last_snapshot):
            last_snapshot = wait_for_capture_channel(
                proxy, last_snapshot, allow_game_restart=False
            )
        emit(
            "status",
            message="首帧未显示鉴定条；先只读等待一个稳定帧。",
        )
        if last_snapshot.image:
            try:
                measurement = base.measure_ipad14_6_appraisal(
                    last_snapshot.image, base.ORIENTATION
                )
            except ValueError as exc:
                last_error = exc
            else:
                emit("status", message="鉴定条在只读等待后已稳定。")
                return last_snapshot, measurement

        # The current game build presents a stable team-leader dialogue before
        # the bars.  Advance that dialogue exactly once.  This is deliberately
        # separate from the later read-only retries: no second dialogue tap is
        # ever issued when bar detection remains uncertain.
        emit("status", message="验证到鉴定对白未显示 IV 条；只推进一次对白。")
        base._tap(proxy, "APPRAISAL_DIALOG")
        for attempt in range(1, _READ_ONLY_RETRY_LIMIT + 1):
            emit(
                "waiting",
                stage="鉴定条读取",
                reason="鉴定对白已推进，正在等待可稳定测量的 A/D/S 条。",
                attempt=attempt,
                total=_READ_ONLY_RETRY_LIMIT,
                elapsed_seconds=attempt,
                next_action="只读获取下一张鉴定页截图；连续不可读会保留原名并回到详情。",
                user_action="无需操作；后台不会重复点击对白或改名。",
            )
            retry = base._next_snapshot(
                proxy,
                _FIRST_DIALOG_READ_DELAY_SECONDS if attempt == 1 else 1.5,
            )
            if navigation.snapshot_is_black(retry):
                retry = wait_for_capture_channel(
                    proxy, retry, allow_game_restart=False
                )
            last_snapshot = retry
            if not retry.image:
                continue
            try:
                measurement = base.measure_ipad14_6_appraisal(
                    retry.image, base.ORIENTATION
                )
            except ValueError as exc:
                last_error = exc
                continue
            emit(
                "status",
                message=f"对白推进后，鉴定条在第 {attempt} 次只读重测时稳定。",
            )
            emit(
                "navigation",
                state="APPRAISAL_BARS",
                orientation=base.ORIENTATION,
                step=attempt,
            )
            return retry, measurement
        raise AppraisalMeasurementUnavailable(last_snapshot, last_error) from last_error


def run(mode: str, settings: Settings) -> int:
    with DeviceRunLock(settings.journal_path.parent / "iphone-mcp.lock"):
        previous_commit = transition._commit_with_transition_verification
        previous_navigate = navigation.navigate_to_appraisal
        previous_original = navigation._ORIGINAL_NAVIGATE
        transition._commit_with_transition_verification = _commit_after_dismissing_keyboard
        navigation.navigate_to_appraisal = _navigate_with_stale_dialog_recovery
        navigation._ORIGINAL_NAVIGATE = _navigate_with_read_only_measurement_retry
        try:
            return rename_dialog.run(mode, settings)
        finally:
            transition._commit_with_transition_verification = previous_commit
            navigation.navigate_to_appraisal = previous_navigate
            navigation._ORIGINAL_NAVIGATE = previous_original


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pure-Python locked iPad renamer with stable-frame retry"
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
