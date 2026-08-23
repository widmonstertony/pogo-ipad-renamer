from __future__ import annotations

import argparse

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v13 as v13
from . import ipad_landscape_agent_v14 as v14
from . import ipad_landscape_agent_v16 as v16
from .config import Settings
from .device_run_lock import DeviceRunLock
from .ipad_landscape_agent_v20 import _navigate_with_stale_dialog_recovery
from .ipad_landscape_agent_v15 import wait_for_capture_channel
from .ipad_landscape_agent_v22 import _commit_after_dismissing_keyboard
from .native_agent import emit
from .policy import PolicyViolation


_BASE_ORIGINAL_NAVIGATE = v14._ORIGINAL_NAVIGATE
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
        if v14.snapshot_is_black(last_snapshot):
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
            retry = base._next_snapshot(
                proxy,
                _FIRST_DIALOG_READ_DELAY_SECONDS if attempt == 1 else 1.5,
            )
            if v14.snapshot_is_black(retry):
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
        previous_commit = v13._commit_with_transition_verification
        previous_navigate = v14.navigate_to_appraisal_v14
        previous_original = v14._ORIGINAL_NAVIGATE
        v13._commit_with_transition_verification = _commit_after_dismissing_keyboard
        v14.navigate_to_appraisal_v14 = _navigate_with_stale_dialog_recovery
        v14._ORIGINAL_NAVIGATE = _navigate_with_read_only_measurement_retry
        try:
            return v16.run(mode, settings)
        finally:
            v13._commit_with_transition_verification = previous_commit
            v14.navigate_to_appraisal_v14 = previous_navigate
            v14._ORIGINAL_NAVIGATE = previous_original


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Pure-Python locked iPad renamer with stable-frame retry v24"
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
