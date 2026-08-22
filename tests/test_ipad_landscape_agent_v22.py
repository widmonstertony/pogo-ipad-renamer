from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.ipad_landscape_agent_v22 import (
    RenameFieldVerificationUnavailable,
    _commit_after_dismissing_keyboard,
    _dialog_evidence_after_keyboard_dismiss,
    _finalize_verified_commit,
    _submit_with_one_verified_retry,
    _tap_accessibility_ok,
)
from pogo_iphone_renamer.policy import PolicyViolation


class _Journal:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, str]]] = []

    def append(self, event: str, payload: dict[str, str]) -> None:
        self.events.append((event, payload))


class _Proxy:
    def __init__(self, *, verified: int, pending: str | None) -> None:
        self.verified_renames = verified
        self.pending_name = pending
        self.journal = _Journal()


class BatchVerifiedCommitTests(unittest.TestCase):
    def test_keyboard_dismiss_waits_through_one_empty_ocr_frame(self) -> None:
        blank = Snapshot("", "blank")
        dialog = Snapshot("", "dialog")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            side_effect=[blank, dialog],
        ) as read, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.ocr_mcp_screenshot",
            return_value=(),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.rename_dialog_visible",
            side_effect=[False, True],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.exact_accessibility_tap_point",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ):
            returned, prefer_accessibility = (
                _dialog_evidence_after_keyboard_dismiss(object())
            )

        self.assertIs(returned, dialog)
        self.assertFalse(prefer_accessibility)
        self.assertEqual(read.call_count, 2)

    def test_keyboard_dismiss_accepts_both_live_accessibility_controls(self) -> None:
        blank = Snapshot("", "blank")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            return_value=blank,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.ocr_mcp_screenshot",
            return_value=(),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.rename_dialog_visible",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.exact_accessibility_tap_point",
            side_effect=[(500.0, 760.0), (500.0, 850.0)],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ):
            returned, prefer_accessibility = (
                _dialog_evidence_after_keyboard_dismiss(object())
            )

        self.assertIs(returned, blank)
        self.assertTrue(prefer_accessibility)

    def test_accessibility_evidence_uses_accessibility_for_first_submit(self) -> None:
        detail = Snapshot("", "detail")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._tap_accessibility_ok",
            return_value=True,
        ) as accessibility_ok, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.tap_ok"
        ) as ocr_ok, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.ocr_mcp_screenshot",
            return_value=(),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.rename_dialog_visible",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.v14.robust_page_state",
            return_value="DETAIL",
        ):
            returned = _submit_with_one_verified_retry(
                object(),
                nickname="瓦斯彈⓯❺⓫⁶⁹",
                prefer_accessibility_first=True,
            )

        self.assertIs(returned, detail)
        accessibility_ok.assert_called_once()
        ocr_ok.assert_not_called()

    def test_accessibility_ok_retry_uses_exact_returned_point(self) -> None:
        calls: list[tuple[str, dict]] = []
        proxy = SimpleNamespace(
            observation=SimpleNamespace(token="fresh-token"),
            call_tool=lambda name, arguments: calls.append((name, arguments)) or {},
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.exact_accessibility_tap_point",
            return_value=(501.0, 768.0),
        ), patch("pogo_iphone_renamer.ipad_landscape_agent_v22.emit"):
            self.assertTrue(_tap_accessibility_ok(proxy))

        self.assertEqual(calls[0][0], "tap_screen")
        self.assertEqual(calls[0][1]["x"], 501.0)
        self.assertEqual(calls[0][1]["y"], 768.0)
        self.assertEqual(calls[0][1]["_observation_token"], "fresh-token")

    def test_slow_submit_waits_read_only_before_retrying_ok(self) -> None:
        nickname = "滑滑小子❻❷⓬⁴⁴"
        dialog = Snapshot("", "dialog")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")

        class SubmitProxy(_Proxy):
            observation = SimpleNamespace(token="token", text="重新命名")

            def call_tool(self, name: str, arguments: dict) -> dict:
                self.pending_name = nickname
                return {}

        proxy = SubmitProxy(verified=0, pending=None)
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._backspace_current_name",
            return_value=4,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._mark_rename_observation"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._verified_entered_value",
            return_value=nickname,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.dismiss_active_keyboard",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            side_effect=[dialog, detail],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.ocr_mcp_screenshot",
            return_value=(),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.rename_dialog_visible",
            side_effect=[True, False],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.v14.robust_page_state",
            return_value="DETAIL",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.tap_ok"
        ) as ok, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._tap_accessibility_ok",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ):
            _commit_after_dismissing_keyboard(
                proxy,
                current_name="滑滑小子",
                species="滑滑小子",
                nickname=nickname,
            )

        # The second frame reached DETAIL, so the new state machine proves a
        # slow transition without sending a redundant second OK tap.
        self.assertEqual(ok.call_count, 1)
        self.assertEqual(proxy.verified_renames, 1)
        self.assertIsNone(proxy.pending_name)

    def test_unchanged_dialog_reverifies_field_then_retries(self) -> None:
        nickname = "瑪瑙水母❼❾⓿³⁶"
        dialog = Snapshot("", "dialog")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")

        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            side_effect=[dialog, dialog, detail],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.ocr_mcp_screenshot",
            return_value=(),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.rename_dialog_visible",
            side_effect=[True, True, False],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.v14.robust_page_state",
            return_value="DETAIL",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._verified_entered_value_with_read_only_retry",
            return_value=nickname,
        ) as verify, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._tap_accessibility_ok",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.tap_ok"
        ) as ok, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ):
            returned = _submit_with_one_verified_retry(
                object(), nickname=nickname
            )

        self.assertIs(returned, detail)
        self.assertEqual(ok.call_count, 2)
        verify.assert_called_once_with(unittest.mock.ANY, nickname)

    def test_accessibility_value_loss_uses_verified_pending_name_for_retry(self) -> None:
        nickname = "瑪瑙水母❿⓿⓿²²"
        dialog = Snapshot("", "dialog")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        proxy = SimpleNamespace(pending_name=nickname)

        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            side_effect=[dialog, dialog, detail],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.ocr_mcp_screenshot",
            return_value=(),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.rename_dialog_visible",
            side_effect=[True, True, False],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.v14.robust_page_state",
            return_value="DETAIL",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._verified_entered_value_with_read_only_retry",
            return_value="",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._tap_accessibility_ok",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.tap_ok"
        ) as ok, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ):
            returned = _submit_with_one_verified_retry(proxy, nickname=nickname)

        self.assertIs(returned, detail)
        self.assertEqual(ok.call_count, 2)

    def test_empty_accessibility_field_is_cancelled_without_submit(self) -> None:
        nickname = "滑滑小子❻❷⓬⁴⁴"
        dialog = Snapshot("", "dialog")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")

        class RecoveryProxy(_Proxy):
            observation = SimpleNamespace(token="token", text="重新命名")

            def call_tool(self, name: str, arguments: dict) -> dict:
                self.pending_name = nickname
                return {}

        proxy = RecoveryProxy(verified=0, pending=None)
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._backspace_current_name",
            return_value=4,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._mark_rename_observation"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._verified_entered_value",
            return_value="",
        ) as verify, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            side_effect=[dialog, dialog, dialog, detail],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.dismiss_active_keyboard",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.rename_dialog_visible",
            return_value=True,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.ocr_mcp_screenshot",
            return_value=(),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.tap_cancel"
        ) as cancel, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.tap_ok"
        ) as ok, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._validate_expected"
        ) as validate, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ):
            with self.assertRaises(RenameFieldVerificationUnavailable) as raised:
                _commit_after_dismissing_keyboard(
                    proxy,
                    current_name="滑滑小子",
                    species="滑滑小子",
                    nickname=nickname,
                )

        self.assertIs(raised.exception.snapshot, detail)
        self.assertEqual(verify.call_count, 3)
        cancel.assert_called_once_with(proxy)
        ok.assert_not_called()
        validate.assert_called_once_with("DETAIL", detail)
        self.assertIsNone(proxy.pending_name)
        self.assertEqual(proxy.verified_renames, 0)

    def test_second_rename_manual_fallback_clears_pending_state(self) -> None:
        proxy = _Proxy(verified=1, pending="走路草❶⓫❽⁴⁴")

        _finalize_verified_commit(
            proxy,
            verified_before=1,
            current_name="走路草",
            species="走路草",
            nickname="走路草❶⓫❽⁴⁴",
        )

        self.assertEqual(proxy.verified_renames, 2)
        self.assertIsNone(proxy.pending_name)
        self.assertEqual(len(proxy.journal.events), 1)

    def test_upstream_auto_verification_must_increment_exactly_once(self) -> None:
        proxy = _Proxy(verified=2, pending=None)

        _finalize_verified_commit(
            proxy,
            verified_before=1,
            current_name="走路草",
            species="走路草",
            nickname="走路草❶⓫❽⁴⁴",
        )

        self.assertEqual(proxy.verified_renames, 2)
        self.assertEqual(proxy.journal.events, [])

    def test_mismatched_pending_name_is_not_cleared(self) -> None:
        proxy = _Proxy(verified=1, pending="其他昵称")

        with self.assertRaises(PolicyViolation):
            _finalize_verified_commit(
                proxy,
                verified_before=1,
                current_name="走路草",
                species="走路草",
                nickname="走路草❶⓫❽⁴⁴",
            )

        self.assertEqual(proxy.verified_renames, 1)
        self.assertEqual(proxy.pending_name, "其他昵称")

    def test_missing_pending_without_count_increment_is_rejected(self) -> None:
        proxy = _Proxy(verified=1, pending=None)

        with self.assertRaises(PolicyViolation):
            _finalize_verified_commit(
                proxy,
                verified_before=1,
                current_name="走路草",
                species="走路草",
                nickname="走路草❶⓫❽⁴⁴",
            )


if __name__ == "__main__":
    unittest.main()
