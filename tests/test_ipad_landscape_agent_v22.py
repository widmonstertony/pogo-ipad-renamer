from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.ipad_landscape_agent_v22 import (
    RenameCancelDidNotDismiss,
    RenameFieldVerificationUnavailable,
    _cancel_unverified_input,
    _commit_after_dismissing_keyboard,
    _dialog_evidence_after_keyboard_dismiss,
    _finalize_verified_commit,
    _location_error_banner_visible,
    _location_error_banner_visible,
    _submit_with_one_verified_retry,
    _tap_accessibility_cancel,
    _tap_accessibility_ok,
    _wait_for_detail_after_cancel,
    _wait_for_task_switcher_to_clear,
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

    def test_location_error_12_banner_is_detected_despite_ocr_variant(self) -> None:
        avoidance = [
            SimpleNamespace(text="無法偵側目前位置 (12)"),
            SimpleNamespace(text="設定暱稱"),
        ]
        self.assertTrue(_location_error_banner_visible(avoidance))
        self.assertFalse(
            _location_error_banner_visible(
                [SimpleNamespace(text="設定暱稱"), SimpleNamespace(text="OK")]
            )
        )

    def test_location_error_12_banner_survives_common_ocr_typo(self) -> None:
        self.assertTrue(
            _location_error_banner_visible(
                [SimpleNamespace(text="無法偵側目前位置 (12)")]
            )
        )
        self.assertFalse(
            _location_error_banner_visible(
                [SimpleNamespace(text="設定暱稱"), SimpleNamespace(text="OK")]
            )
        )

    def test_cancel_wait_accepts_current_pixel_detail_despite_stale_ax(self) -> None:
        detail = Snapshot("Dock stale", "detail")
        proxy = SimpleNamespace()
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base.local_page_state",
            return_value="DETAIL",
        ) as local_state, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._validate_expected"
        ) as validate:
            returned = _wait_for_detail_after_cancel(proxy, detail)

        self.assertIs(returned, detail)
        local_state.assert_called_once_with(detail)
        validate.assert_not_called()

    def test_cancel_wait_escalates_a_visibly_unchanged_rename_dialog(self) -> None:
        dialog = Snapshot("rename", "dialog")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base.local_page_state",
            return_value="RENAME_DIALOG",
        ):
            with self.assertRaises(RenameCancelDidNotDismiss) as raised:
                _wait_for_detail_after_cancel(SimpleNamespace(), dialog)

        self.assertIs(raised.exception.snapshot, dialog)

    def test_task_switcher_waits_without_touching_rename_dialog(self) -> None:
        proxy = SimpleNamespace(
            observation=SimpleNamespace(text="程序坞\n账号安全")
        )

        def clear_overlay(_proxy, _delay):
            proxy.observation.text = "重新命名"
            return Snapshot("rename dialog", "dialog")

        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            side_effect=clear_overlay,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ) as emit:
            _wait_for_task_switcher_to_clear(proxy)

        next_snapshot.assert_called_once_with(proxy, 3.0)
        emit.assert_called_once()

    def test_task_switcher_stale_ax_yields_to_visible_rename_dialog(self) -> None:
        proxy = SimpleNamespace(
            observation=SimpleNamespace(text="程序坞\n账号安全")
        )
        snapshot = Snapshot("rename dialog", "dialog")

        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            return_value=snapshot,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base.local_page_state",
            return_value="RENAME_DIALOG",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit",
        ) as emit:
            self.assertTrue(_wait_for_task_switcher_to_clear(proxy))

        next_snapshot.assert_called_once_with(proxy, 3.0)
        self.assertEqual(emit.call_count, 2)

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
        ) as read, patch(
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
        read.assert_called_once_with(unittest.mock.ANY, 1.0)

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
            "pogo_iphone_renamer.ipad_landscape_agent_v22._focus_ocr_default_name_field"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._dialog_contains_exact_text",
            return_value=False,
        ), patch(
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
            committed_detail = _commit_after_dismissing_keyboard(
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
        self.assertIs(committed_detail, detail)

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

    def test_retry_ok_disappearance_accepts_proven_detail_without_another_tap(self) -> None:
        nickname = "淚眼蜥⓯⓫⓬⁸⁴"
        dialog = Snapshot("", "dialog")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        proxy = SimpleNamespace(pending_name=nickname)

        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            side_effect=[dialog, dialog, detail],
        ) as read, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.ocr_mcp_screenshot",
            return_value=(),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.rename_dialog_visible",
            side_effect=[True, True],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.v14.robust_page_state",
            return_value="DETAIL",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._verified_entered_value_with_read_only_retry",
            return_value=nickname,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._tap_accessibility_ok",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.tap_ok",
            side_effect=[None, PolicyViolation("OCR OK disappeared during transition")],
        ) as ok, patch("pogo_iphone_renamer.ipad_landscape_agent_v22.emit"):
            returned = _submit_with_one_verified_retry(proxy, nickname=nickname)

        self.assertIs(returned, detail)
        self.assertEqual(ok.call_count, 2)
        self.assertEqual(read.call_count, 3)

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
            "pogo_iphone_renamer.ipad_landscape_agent_v22._focus_ocr_default_name_field"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._dialog_contains_exact_text",
            return_value=False,
        ), patch(
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

    def test_unchanged_default_after_input_uses_one_type_text_fallback(self) -> None:
        nickname = "滑滑小子❻❷⓬⁴⁴"
        proxy = SimpleNamespace(
            observation=SimpleNamespace(token="token", text="重新命名"),
            verified_renames=0,
            pending_name=None,
            journal=_Journal(),
            call_tool=Mock(),
        )
        detail = Snapshot("detail", "detail")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._focus_ocr_default_name_field"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._backspace_current_name",
            return_value=4,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._dialog_contains_exact_text",
            side_effect=[False, True],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._mark_rename_observation"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._verified_entered_value_with_read_only_retry",
            side_effect=["", nickname],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.dismiss_active_keyboard",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._submit_with_one_verified_retry",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._finalize_verified_commit"
        ), patch("pogo_iphone_renamer.ipad_landscape_agent_v22.emit"):
            returned = _commit_after_dismissing_keyboard(
                proxy,
                current_name="滑滑小子",
                species="滑滑小子",
                nickname=nickname,
            )

        self.assertIs(returned, detail)
        self.assertEqual(
            [call.args[0] for call in proxy.call_tool.call_args_list],
            ["input_text", "type_text"],
        )

    def test_default_still_visible_after_clear_never_sends_text(self) -> None:
        proxy = SimpleNamespace(
            observation=SimpleNamespace(token="token", text="重新命名"),
            verified_renames=0,
            pending_name=None,
            journal=_Journal(),
            call_tool=Mock(),
        )
        cancelled = RenameFieldVerificationUnavailable(Snapshot("detail", "detail"), "滑滑小子")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._focus_ocr_default_name_field"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._backspace_current_name",
            return_value=4,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._dialog_contains_exact_text",
            return_value=True,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._cancel_unverified_input",
            side_effect=cancelled,
        ) as cancel, patch("pogo_iphone_renamer.ipad_landscape_agent_v22.emit"):
            with self.assertRaises(RenameFieldVerificationUnavailable):
                _commit_after_dismissing_keyboard(
                    proxy,
                    current_name="滑滑小子",
                    species="滑滑小子",
                    nickname="滑滑小子❻❷⓬⁴⁴",
                )

        cancel.assert_called_once_with(proxy, "滑滑小子")
        proxy.call_tool.assert_not_called()

    def test_cancel_waits_through_transient_non_detail_frame(self) -> None:
        dialog = Snapshot("rename dialog", "dialog")
        transient = Snapshot("transition", "transient")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        proxy = SimpleNamespace(
            observation=SimpleNamespace(token="token", text="重新命名"),
            pending_name="滑滑小子❻❷⓬⁴⁴",
        )
        with patch(
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
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            side_effect=[dialog, transient, detail],
        ) as read, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._validate_expected",
            side_effect=[PolicyViolation("temporary frame"), None],
        ) as validate, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ):
            with self.assertRaises(RenameFieldVerificationUnavailable) as raised:
                _cancel_unverified_input(proxy, "")

        cancel.assert_called_once_with(proxy)
        self.assertIs(raised.exception.snapshot, detail)
        self.assertEqual(read.call_count, 3)
        self.assertEqual(validate.call_count, 2)
        self.assertIsNone(proxy.pending_name)

    def test_cancel_never_falls_back_to_card_navigation_after_stale_overlay(self) -> None:
        """A post-cancel stale Dock tree must keep the recovery read-only."""

        dialog = Snapshot("rename dialog", "dialog")
        transition = Snapshot("Stage Manager Dock", "transition")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        proxy = SimpleNamespace(
            observation=SimpleNamespace(token="token", text="程序坞\nPokémon GO"),
            pending_name="木木梟❼⓬⓭⁷¹",
        )
        with patch(
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
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            side_effect=[dialog, transition, transition, transition, transition, transition, detail],
        ) as read, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._validate_expected",
            side_effect=[PolicyViolation("transition")] * 6 + [None],
        ) as validate, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ):
            with self.assertRaises(RenameFieldVerificationUnavailable) as raised:
                _cancel_unverified_input(proxy, "木木梟")

        cancel.assert_called_once_with(proxy)
        self.assertIs(raised.exception.snapshot, detail)
        self.assertEqual(read.call_count, 7)
        # The final fresh local DETAIL frame is sufficient even while AX is
        # still stale, so it need not call the legacy AX validator again.
        self.assertEqual(validate.call_count, 6)
        self.assertIsNone(proxy.pending_name)

    def test_missing_cancel_control_recovers_from_a_verified_detail_without_retrying_a_tap(self) -> None:
        dialog = Snapshot("rename dialog", "dialog")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        proxy = SimpleNamespace(
            observation=SimpleNamespace(token="token", text="重新命名"),
            pending_name="火球鼠❼❼⓭⁶⁰",
        )
        cancel_error = PolicyViolation("详情页未定位到精确名称文字框：取消")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.dismiss_active_keyboard",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.rename_dialog_visible",
            return_value=True,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.ocr_mcp_screenshot",
            return_value=(),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.tap_cancel",
            side_effect=cancel_error,
        ) as ocr_cancel, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22._tap_accessibility_cancel",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._next_snapshot",
            side_effect=[dialog, detail],
        ) as read, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._validate_expected"
        ) as validate, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ) as emit:
            with self.assertRaises(RenameFieldVerificationUnavailable) as raised:
                _cancel_unverified_input(proxy, "unverified field")

        ocr_cancel.assert_called_once_with(proxy)
        self.assertIs(raised.exception.snapshot, detail)
        self.assertEqual(read.call_count, 2)
        # Pixel-local DETAIL proof intentionally bypasses the stale AX
        # validator on this recovery path.
        validate.assert_not_called()
        self.assertIsNone(proxy.pending_name)
        self.assertIn("取消控件在最终截图中已消失", emit.call_args.kwargs["message"])

    def test_accessibility_cancel_uses_only_the_exact_current_control(self) -> None:
        proxy = SimpleNamespace(
            observation=SimpleNamespace(
                token="fresh-token", text="重新命名", width=1024, height=1366
            ),
            call_tool=Mock(return_value={}),
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.exact_accessibility_tap_point",
            return_value=(992.0, 910.0),
        ) as point, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ) as emit:
            self.assertTrue(_tap_accessibility_cancel(proxy))

        point.assert_called_once_with(proxy, "取消")
        proxy.call_tool.assert_called_once_with(
            "tap_screen",
            {
                "x": 992.0,
                "y": 910.0,
                "_observation_token": "fresh-token",
                "_intent": "navigate exact accessibility cancel rename dialog without submitting",
                "_expected_after": "DETAIL",
            },
        )
        self.assertIn("精确取消触点", emit.call_args.kwargs["message"])

    def test_stage_manager_cancel_uses_calibrated_anchor_not_portrait_ax_point(
        self,
    ) -> None:
        proxy = SimpleNamespace(
            observation=SimpleNamespace(
                token="fresh-token", text="重新命名", width=1366, height=1024
            )
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.base._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.exact_accessibility_tap_point"
        ) as point, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v22.emit"
        ) as emit:
            self.assertTrue(_tap_accessibility_cancel(proxy))

        tap.assert_called_once_with(proxy, "RENAME_CANCEL")
        point.assert_not_called()
        self.assertIn("Stage Manager 取消锚点", emit.call_args.kwargs["message"])

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
