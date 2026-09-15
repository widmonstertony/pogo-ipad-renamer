from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, Mock, patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.batch_navigation_v26 import DetailFingerprint
from pogo_iphone_renamer.batch_pause import BatchPauseFile
from pogo_iphone_renamer import ipad_landscape_agent as base
from pogo_iphone_renamer.ipad_landscape_agent_v24 import (
    AppraisalMeasurementUnavailable,
)
from pogo_iphone_renamer.ipad_landscape_agent_v22 import (
    RenameFieldVerificationUnavailable,
)
from pogo_iphone_renamer.ipad_landscape_agent_v16 import (
    RenamePencilLocalizationUnavailable,
)
from pogo_iphone_renamer.ipad_landscape_batch_agent_v26 import (
    FreshDetailIdentityUnavailable,
    _appraisal_identity_matches_current_detail,
    _close_appraisal,
    _confirm_fresh_detail_identity,
    _confirm_low_confidence_measurement,
    _ensure_game_foreground,
    _ensure_plain_detail,
    _empty_appraisal_title_is_safe_on_direct_route,
    _current_detail_only,
    _detail_name_key,
    _last_unsubmitted_journal_nickname,
    _last_interrupted_preinput_journal_species,
    _restore_direct_detail_after_interrupted_appraisal,
    _restore_direct_detail_from_existing_menu,
    _wait_for_direct_stage_geometry,
    _is_recoverable_navigation_failure,
    _is_calibrated_landscape_device,
    _matches_configured_device,
    _is_scrolled_pokemon_detail,
    _orientation_for_calibrated_device,
    _clear_pager_resume,
    _load_pager_resume,
    _save_pager_resume,
    _select_pokemon_tab_from_egg_overview,
    _swipe_to_verified_next_with_read_recovery,
    _is_unsafe_stage_manager_geometry,
    _navigate_from_current_detail_only,
    _process_one,
    _require_current_detail,
    _name_agnostic_navigation_fingerprint,
    _visual_navigation_fingerprint,
    _open_next_inventory_card_after_pager_exit,
    _probe_unreadable_name_via_untouched_dialog,
    _proven_default_name_in_rename_dialog,
    _recover_after_device_unlock,
    _recover_after_task_switcher_interrupt,
    _resume_verified_unsubmitted_rename,
    _wait_for_direct_detail_after_task_switcher,
    _wait_for_verified_next_detail,
    _wait_without_game_restart,
    _wait_at_safe_pause_boundary,
)
from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.landscape_cv import IVMeasurement
from pogo_iphone_renamer.local_ocr_v3 import NameRegionResult
from pogo_iphone_renamer.policy import Observation, PolicyViolation


def _default_name(species: str = "可達鴨") -> NameRegionResult:
    return NameRegionResult(
        species=species,
        is_default=True,
        confidence=0.99,
        evidence=(species,),
    )


class BatchUnreadableAppraisalTests(unittest.TestCase):
    def test_unreadable_title_uses_untouched_field_for_default_name(self) -> None:
        detail = Snapshot("detail", "detail")
        dialog = Snapshot("dialog", "dialog")
        restored = Snapshot("restored", "restored")
        proxy = SimpleNamespace(observation=SimpleNamespace())
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            side_effect=[
                "DETAIL",
                "RENAME_DIALOG",
                "RENAME_DIALOG",
                "RENAME_DIALOG",
                "RENAME_DIALOG",
                "DETAIL",
            ],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._static_pencil_coordinates",
            return_value=(1.0, 2.0),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._tap_dynamic_pencil_at"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._wait_for_dialog_or_detail_after_pencil",
            return_value=dialog,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=[dialog, dialog, dialog],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.exact_name_field",
            side_effect=["巨金怪", "巨金怪", "巨金怪"],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._cancel_unverified_input",
            side_effect=RenameFieldVerificationUnavailable(restored, "巨金怪"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.traditional_chinese_species",
            return_value=frozenset({"巨金怪"}),
        ), patch("pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"):
            result = _probe_unreadable_name_via_untouched_dialog(proxy, detail)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIs(result[0], restored)
        self.assertEqual(result[1].species, "巨金怪")
        self.assertTrue(result[1].is_default)

    def test_unreadable_title_preserves_custom_name_from_untouched_field(self) -> None:
        detail = Snapshot("detail", "detail")
        dialog = Snapshot("dialog", "dialog")
        restored = Snapshot("restored", "restored")
        proxy = SimpleNamespace(observation=SimpleNamespace())
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            side_effect=[
                "DETAIL",
                "RENAME_DIALOG",
                "RENAME_DIALOG",
                "RENAME_DIALOG",
                "RENAME_DIALOG",
                "DETAIL",
            ],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._static_pencil_coordinates",
            return_value=(1.0, 2.0),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._tap_dynamic_pencil_at"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._wait_for_dialog_or_detail_after_pencil",
            return_value=dialog,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=[dialog, dialog, dialog],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.exact_name_field",
            side_effect=["完美巨金怪", "完美巨金怪", "完美巨金怪"],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._cancel_unverified_input",
            side_effect=RenameFieldVerificationUnavailable(restored, "完美巨金怪"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.traditional_chinese_species",
            return_value=frozenset({"巨金怪"}),
        ), patch("pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"):
            result = _probe_unreadable_name_via_untouched_dialog(proxy, detail)

        self.assertIsNotNone(result)
        assert result is not None
        self.assertIs(result[0], restored)
        self.assertIsNone(result[1].species)
        self.assertFalse(result[1].is_default)
        self.assertIn("完美巨金怪", result[1].evidence)

    def test_egg_tab_requires_visual_change_then_adopts_verified_fallback_mapping(self) -> None:
        egg = Snapshot("egg", "egg")
        cards = Snapshot("cards", "cards")
        proxy = SimpleNamespace(
            observation=SimpleNamespace(width=1366, height=1024, token="fresh"),
            client=SimpleNamespace(reset_read_session=Mock()),
            call_tool=Mock(),
        )
        located = SimpleNamespace(
            box=SimpleNamespace(left=450, right=550, top=60, bottom=120),
            image_width=1000,
            image_height=1000,
        )
        previous_orientation = base.ORIENTATION
        previous_mapping = base._PORTRAIT_WINDOW_INPUT_MAPPING
        self.addCleanup(setattr, base, "ORIENTATION", previous_orientation)
        self.addCleanup(setattr, base, "_PORTRAIT_WINDOW_INPUT_MAPPING", previous_mapping)
        base.ORIENTATION = "STAGE_MANAGER_PORTRAIT_WINDOW"
        base._PORTRAIT_WINDOW_INPUT_MAPPING = "ax_rotated"
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.locate_exact_text_from_mcp",
            return_value=located,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.current_stage_geometry",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.upright_ratio_to_touch",
            return_value=(683.0, 133.0),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.portrait_window_ax_rotated_touch",
            return_value=(683.0, 133.0),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.portrait_window_visible_touch",
            return_value=(520.0, 133.0),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.portrait_window_legacy_rotated_touch",
            return_value=(978.0, 520.0),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=[egg, egg, egg, cards],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.robust_page_state",
            return_value="INVENTORY",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._egg_overview_visible",
            side_effect=[True, True, True, False],
        ), patch("pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"):
            returned = _select_pokemon_tab_from_egg_overview(proxy, egg)

        self.assertIs(returned, cards)
        self.assertEqual(proxy.client.reset_read_session.call_count, 2)
        self.assertEqual(proxy.call_tool.call_count, 2)
        self.assertEqual(base._PORTRAIT_WINDOW_INPUT_MAPPING, "direct")

    def test_missing_fresh_detail_identity_retries_same_card_without_counting_it(self) -> None:
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit",
        ) as emit:
            with self.assertRaises(FreshDetailIdentityUnavailable) as raised:
                _process_one(object(), detail, mode="rename", index=8)

        self.assertIs(raised.exception.snapshot, detail)
        self.assertEqual(emit.call_args.kwargs["stage"], "详情身份核验")
        self.assertIn("不会把未验证卡片计为已处理", emit.call_args.kwargs["reason"])

    def test_process_one_recovers_proven_appraisal_before_identity_reads(self) -> None:
        appraisal = Snapshot("20/20 HP 1 kg", "appraisal")
        detail = Snapshot("索羅亞 20/20 HP 1 kg", "detail")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="APPRAISAL_DIALOG",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._restore_direct_detail_after_interrupted_appraisal",
            return_value=detail,
        ) as restore, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._scroll_verified_detail_to_title",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=None,
        ):
            with self.assertRaises(FreshDetailIdentityUnavailable):
                _process_one(object(), appraisal, mode="rename", index=1)

        restore.assert_called_once_with(ANY, appraisal)

    def test_pager_resume_only_restores_a_structurally_valid_prior_cp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            settings = Settings(
                mcp_url="http://127.0.0.1:8090/mcp",
                health_url="http://127.0.0.1:8090/health",
                protocol_version="2025-11-25",
                pokemon_go_bundle_id="com.nianticlabs.pokemongo",
                write_enabled=True,
                batch_limit=0,
                observation_ttl_seconds=20,
                journal_path=data / "actions.jsonl",
            )
            fingerprint = DetailFingerprint(
                ("燭光靈",), "cp632", "95/95hp", "3.14kg", "0.29m"
            )

            _save_pager_resume(settings, fingerprint)

            self.assertEqual(_load_pager_resume(settings), fingerprint)
            _clear_pager_resume(settings)
            self.assertIsNone(_load_pager_resume(settings))

    def test_inventory_fallback_requires_two_fresh_target_cp_reads(self) -> None:
        inventory = Snapshot("inventory", "inventory")
        target = Snapshot("detail", "target")
        proxy = SimpleNamespace(
            observation=SimpleNamespace(width=1366, height=1024, token="fresh"),
            client=SimpleNamespace(reset_read_session=Mock()),
            call_tool=Mock(),
        )
        previous = DetailFingerprint(("燭光靈",), "cp632", "62/62hp", "2.0kg", "0.2m")
        target_fingerprint = DetailFingerprint(("燭光靈",), "cp570", "60/60hp", "2.0kg", "0.2m")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._visible_inventory_cards",
            return_value=[
                ("cp632", 500.0, 640.0, 1024, 1366),
                ("cp570", 820.0, 640.0, 1024, 1366),
            ],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.current_stage_geometry",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.upright_ratio_to_touch",
            return_value=(900.0, 500.0),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=[inventory, target, target],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.robust_page_state",
            side_effect=["INVENTORY", "DETAIL", "DETAIL"],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.detail_fingerprint",
            return_value=target_fingerprint,
        ), patch("pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"):
            returned = _open_next_inventory_card_after_pager_exit(
                proxy, inventory, previous
            )

        self.assertIs(returned, target)
        self.assertEqual(proxy.client.reset_read_session.call_count, 2)
        proxy.call_tool.assert_called_once_with(
            "tap_screen",
            {
                "x": 900.0,
                "y": 500.0,
                "_observation_token": "fresh",
                "_intent": "navigate verified adjacent Pokemon inventory card",
                "_expected_after": "DETAIL for neighboring visible Pokemon",
            },
        )

    def test_scrolled_detail_requires_multiple_detail_only_controls(self) -> None:
        snapshot = Snapshot("", "lower-detail")
        lines = (
            SimpleNamespace(text="強化", confidence=0.99),
            SimpleNamespace(text="進化", confidence=0.99),
            SimpleNamespace(text="新攻擊招式", confidence=0.99),
        )
        with patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=lines,
        ):
            self.assertTrue(_is_scrolled_pokemon_detail(snapshot))
        with patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=(SimpleNamespace(text="進化", confidence=0.99),),
        ):
            self.assertFalse(_is_scrolled_pokemon_detail(snapshot))

    def test_calibrated_device_requires_known_model_and_exact_touch_space(self) -> None:
        self.assertTrue(
            _is_calibrated_landscape_device(
                {"machine": "iPad7,2", "screenWidth": 1366, "screenHeight": 1024}
            )
        )
        self.assertTrue(
            _is_calibrated_landscape_device(
                {"machine": "iPad14,6", "screenWidth": 1366, "screenHeight": 1024}
            )
        )
        self.assertFalse(
            _is_calibrated_landscape_device(
                {"machine": "iPad7,2", "screenWidth": 1024, "screenHeight": 1366}
            )
        )

    def test_configured_device_machine_blocks_a_mismatched_endpoint(self) -> None:
        with patch.dict("os.environ", {"POGO_EXPECTED_DEVICE_MACHINE": "iPad7,2"}):
            self.assertTrue(_matches_configured_device({"machine": "iPad7,2"}))
            self.assertFalse(_matches_configured_device({"machine": "iPad14,6"}))
        self.assertEqual(
            "STAGE_MANAGER_PORTRAIT_WINDOW",
            _orientation_for_calibrated_device({"machine": "iPad7,2"}),
        )
        self.assertEqual(
            "STAGE_MANAGER_MAXIMIZED",
            _orientation_for_calibrated_device({"machine": "iPad14,6"}),
        )
        self.assertFalse(
            _is_calibrated_landscape_device(
                {"machine": "iPad99,9", "screenWidth": 1366, "screenHeight": 1024}
            )
        )

    def test_existing_shortened_iv_nickname_is_a_safe_custom_skip(self) -> None:
        nickname = NameRegionResult(
            species=None,
            is_default=False,
            confidence=0.99,
            evidence=("種子鐵1415", "96", "14", "72/72 HP"),
        )

        self.assertEqual(
            ("custom", "種子鐵1415|96|14"), _detail_name_key(nickname)
        )

    def test_candy_confirmed_shortened_nickname_is_a_safe_custom_skip(self) -> None:
        nickname = NameRegionResult(
            species="麻麻小魚",
            is_default=False,
            confidence=0.99,
            evidence=("麻麻小121215",),
        )

        self.assertEqual(("custom", "麻麻小魚"), _detail_name_key(nickname))

    def test_split_annotations_on_shortened_title_are_preserved(self) -> None:
        nickname = NameRegionResult(None, False, 0.0, ("泥偶小", "56", "9", "11", "100 / 100 HP"))
        self.assertEqual(("custom", "泥偶小|56|9|11"), _detail_name_key(nickname))

    def test_split_annotation_fallback_rejects_ambiguous_or_non_title_labels(self) -> None:
        for evidence in (
            ("泥偶小", "100 / 100 HP"),
            ("泥偶小", "9", "11", "100 / 100 HP"),
            ("泥偶小", "56", "9", "11"),
            ("泥偶小", "560", "9", "11", "100 / 100 HP"),
            ("體重值", "56", "9", "11", "100 / 100 HP"),
            ("皮卡丘", "56", "9", "11", "100 / 100 HP"),
            ("泥偶小", "56", "9", "11", "未知標籤", "100 / 100 HP"),
        ):
            with self.subTest(evidence=evidence):
                self.assertIsNone(_detail_name_key(NameRegionResult(None, False, 0.0, evidence)))

    def test_unlock_recovery_cancels_only_this_workers_preinput_dialog(self) -> None:
        detail = Snapshot("可達鴨 21/21 HP", "detail")
        cancelled = RenameFieldVerificationUnavailable(detail, "")
        proxy = SimpleNamespace(pending_name=None, verified_renames=0)
        setattr(proxy, "_pogo_lock_recovery_default_species", "可達鴨")
        settings = SimpleNamespace(journal_path=Path("/definitely/not/a/journal"))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="RENAME_DIALOG",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._cancel_unverified_input",
            side_effect=cancelled,
        ) as cancel, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ), patch("pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"):
            returned, action = _recover_after_device_unlock(
                proxy, Snapshot("rename", "dialog"), settings
            )

        self.assertIs(returned, detail)
        self.assertEqual(action, "retry")
        cancel.assert_called_once_with(proxy, "可達鴨")
        self.assertFalse(hasattr(proxy, "_pogo_lock_recovery_default_species"))

    def test_unlock_recovery_never_cancels_an_unmarked_dialog(self) -> None:
        proxy = SimpleNamespace(pending_name=None, verified_renames=0)
        settings = SimpleNamespace(journal_path=Path("/definitely/not/a/journal"))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="RENAME_DIALOG",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._cancel_unverified_input"
        ) as cancel:
            with self.assertRaisesRegex(PolicyViolation, "未留档"):
                _recover_after_device_unlock(
                    proxy, Snapshot("rename", "dialog"), settings
                )

        cancel.assert_not_called()

    def test_unlock_recovery_waits_for_task_switcher_without_selecting_a_card(self) -> None:
        overlay = Snapshot("程序坞 Shijima 设置", "overview")
        detail = Snapshot("可達鴨 21/21 HP", "detail")
        proxy = SimpleNamespace(pending_name=None, verified_renames=0)
        settings = SimpleNamespace(journal_path=Path("/definitely/not/a/journal"))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._wait_for_visible_game_surface_after_task_switcher",
            return_value=detail,
        ) as wait, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="DETAIL",
        ), patch("pogo_iphone_renamer.ipad_landscape_batch_agent_v26._require_current_detail",
            return_value=detail,
        ), patch("pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"):
            returned, action = _recover_after_device_unlock(proxy, overlay, settings)

        self.assertIs(returned, detail)
        self.assertEqual(action, "retry")
        wait.assert_called_once_with(proxy, overlay)

    def test_unlock_recovery_waits_for_redraw_then_restores_interrupted_appraisal(self) -> None:
        """A black/transitional unlock frame must not end a live direct batch."""

        transition = Snapshot("", "transition")
        appraisal = Snapshot("leader dialogue", "appraisal")
        detail = Snapshot("可達鴨 21/21 HP", "detail")
        proxy = SimpleNamespace(pending_name=None, verified_renames=0)
        settings = SimpleNamespace(journal_path=Path("/definitely/not/a/journal"))
        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            side_effect=["MAP", "APPRAISAL_DIALOG", "APPRAISAL_DIALOG"],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=appraisal,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._restore_direct_detail_after_interrupted_appraisal",
            return_value=detail,
        ) as restore, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned, action = _recover_after_device_unlock(proxy, transition, settings)

        self.assertIs(returned, detail)
        self.assertEqual(action, "retry")
        next_snapshot.assert_called_once_with(proxy, 3.0)
        restore.assert_called_once_with(proxy, appraisal)

    def test_task_switcher_interrupt_closes_only_the_interrupted_appraisal(self) -> None:
        overlay = Snapshot("程序坞 Shijima 设置", "overview")
        appraisal = Snapshot("leader dialogue", "appraisal")
        detail = Snapshot("可達鴨 21/21 HP", "detail")
        proxy = SimpleNamespace(pending_name=None, verified_renames=0)
        settings = SimpleNamespace(journal_path=Path("/definitely/not/a/journal"))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._wait_for_visible_game_surface_after_task_switcher",
            return_value=appraisal,
        ) as wait, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="APPRAISAL_BARS",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._restore_direct_detail_after_interrupted_appraisal",
            return_value=detail,
        ) as restore, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned, action = _recover_after_task_switcher_interrupt(
                proxy, overlay, settings
            )

        self.assertIs(returned, detail)
        self.assertEqual(action, "retry")
        wait.assert_called_once_with(proxy, overlay)
        restore.assert_called_once_with(proxy, appraisal)

    def test_stale_rename_recovery_waits_for_task_switcher_before_reading_field(self) -> None:
        overlay = Snapshot("程序坞 Shijima 设置", "overview")
        detail = Snapshot("可達鴨 21/21 HP", "detail")
        proxy = SimpleNamespace(pending_name=None, verified_renames=0)
        settings = SimpleNamespace(journal_path=Path("/definitely/not/a/journal"))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._wait_for_visible_game_surface_after_task_switcher",
            return_value=detail,
        ) as wait, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state"
        ) as state, patch("pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"):
            returned = _resume_verified_unsubmitted_rename(proxy, overlay, settings)

        self.assertIs(returned, detail)
        wait.assert_called_once_with(proxy, overlay)
        state.assert_called_once_with(detail)

    def test_direct_geometry_waits_read_only_after_stage_manager_redraw(self) -> None:
        appraisal = Snapshot("", "appraisal")
        recovered = Snapshot("", "recovered")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._ensure_stage_geometry_for_state",
            side_effect=[
                PolicyViolation("当前截图未能安全定位 Stage Manager 中的 Pokémon GO 窗口；未执行触控"),
                recovered,
            ],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=appraisal,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="APPRAISAL_BARS",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned = _wait_for_direct_stage_geometry(
                object(), appraisal, "APPRAISAL_BARS"
            )

        self.assertIs(returned, recovered)
        next_snapshot.assert_called_once_with(ANY, 3.0)
        self.assertIn("只读等待", emit.call_args.kwargs["message"])

    def test_name_agnostic_navigation_fingerprint_removes_nickname(self) -> None:
        before = DetailFingerprint(
            ("迷你芙",), "cp415", "96/96hp", "11.02kg", "0.57m"
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.detail_fingerprint",
            return_value=before,
        ):
            fallback = _name_agnostic_navigation_fingerprint(
                Snapshot("before", "before-image")
            )

        self.assertEqual(
            fallback,
            DetailFingerprint((), "cp415", "96/96hp", "11.02kg", "0.57m"),
        )

    def test_visual_navigation_fingerprint_uses_capture_id(self) -> None:
        snapshot = Snapshot("", "", 987654)
        fallback = _visual_navigation_fingerprint(snapshot)

        self.assertEqual(
            fallback,
            DetailFingerprint((), "987654", "987654", "", ""),
        )

    def test_visual_navigation_fingerprint_uses_image_digest_when_no_capture_id(
        self,
    ) -> None:
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._snapshot_digest",
            return_value="abcdef0123456789",
        ):
            fallback = _visual_navigation_fingerprint(Snapshot("", "image"))

        self.assertEqual(
            fallback,
            DetailFingerprint((), "abcdef0123456789", "abcdef0123456789", "", ""),
        )

    def test_journal_only_returns_latest_uncommitted_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.jsonl"
            path.write_text(
                "\n".join(
                    (
                        '{"event":"write_attempt","tool":"input_text","success":true,"arguments":{"text":"妙蛙種❽❿⓯⁷³"}}',
                        '{"event":"verified_rename_keyboard_dismissed_dynamic_ok","new_name":"妙蛙種❽❿⓯⁷³"}',
                        '{"event":"write_attempt","tool":"input_text","success":true,"arguments":{"text":"奈克洛❿❿⓫⁶⁹"}}',
                    )
                ),
                encoding="utf-8",
            )
            settings = Settings(
                mcp_url="http://127.0.0.1:8090/mcp",
                health_url="http://127.0.0.1:8090/health",
                protocol_version="2025-11-25",
                pokemon_go_bundle_id="com.nianticlabs.pokemongo",
                write_enabled=True,
                batch_limit=0,
                observation_ttl_seconds=20,
                journal_path=path,
            )
            self.assertEqual(_last_unsubmitted_journal_nickname(settings), "奈克洛❿❿⓫⁶⁹")

    def test_failed_deterministic_input_recovers_only_the_preinput_species(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.jsonl"
            path.write_text(
                "\n".join(
                    (
                        '{"event":"verified_rename_keyboard_dismissed_dynamic_ok","new_name":"伊布❿❿❿⁶⁷"}',
                        '{"event":"write_attempt","tool":"input_text","success":false,"arguments":{"text":"童偶熊❷❻❶²⁰"}}',
                    )
                ),
                encoding="utf-8",
            )
            settings = SimpleNamespace(journal_path=path)
            self.assertEqual(
                _last_interrupted_preinput_journal_species(settings), "童偶熊"
            )

    def test_resume_commits_only_a_live_field_matching_journal_target(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "actions.jsonl"
            path.write_text(
                '{"event":"write_attempt","tool":"input_text","success":true,"arguments":{"text":"奈克洛❿❿⓫⁶⁹"}}\n',
                encoding="utf-8",
            )
            settings = Settings(
                mcp_url="http://127.0.0.1:8090/mcp",
                health_url="http://127.0.0.1:8090/health",
                protocol_version="2025-11-25",
                pokemon_go_bundle_id="com.nianticlabs.pokemongo",
                write_enabled=True,
                batch_limit=0,
                observation_ttl_seconds=20,
                journal_path=path,
            )
            journal = Mock()
            proxy = SimpleNamespace(
                observation=SimpleNamespace(text="rename dialog"),
                pending_name=None,
                verified_renames=0,
                journal=journal,
            )
            detail = Snapshot("detail", "detail")
            with patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
                return_value="RENAME_DIALOG",
            ), patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._verified_entered_value",
                return_value="奈克洛❿❿⓫⁶⁹",
            ), patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._dialog_evidence_after_keyboard_dismiss",
                return_value=(Snapshot("rename", "dialog"), False),
            ), patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._submit_with_one_verified_retry",
                return_value=detail,
            ) as submit, patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
            ):
                returned = _resume_verified_unsubmitted_rename(
                    proxy, Snapshot("rename", "dialog"), settings
                )

            self.assertIs(returned, detail)
            submit.assert_called_once_with(
                proxy,
                nickname="奈克洛❿❿⓫⁶⁹",
                initial_dialog=Snapshot("rename", "dialog"),
            )
            self.assertEqual(proxy.verified_renames, 1)
            self.assertIsNone(proxy.pending_name)
            journal.append.assert_called_once()

    def test_default_unsubmitted_dialog_is_cancelled_and_returns_detail(self) -> None:
        settings = SimpleNamespace(journal_path=Path("/not-used"))
        proxy = object()
        dialog = Snapshot("rename", "dialog")
        detail = Snapshot("detail", "detail")
        cancelled = RenameFieldVerificationUnavailable(detail, "涼脊龍")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="RENAME_DIALOG",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._last_unsubmitted_journal_nickname",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._proven_default_name_in_rename_dialog",
            return_value="涼脊龍",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._cancel_unverified_input",
            side_effect=cancelled,
        ) as cancel, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned = _resume_verified_unsubmitted_rename(proxy, dialog, settings)

        self.assertIs(returned, detail)
        cancel.assert_called_once_with(proxy, "涼脊龍")

    def test_journalled_but_unapplied_input_cancels_proven_default_dialog(self) -> None:
        settings = SimpleNamespace(journal_path=Path("/not-used"))
        proxy = object()
        dialog = Snapshot("rename", "dialog")
        detail = Snapshot("detail", "detail")
        cancelled = RenameFieldVerificationUnavailable(detail, "涼脊龍")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="RENAME_DIALOG",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._last_unsubmitted_journal_nickname",
            return_value="涼脊龍⓮⓯⓮⁹⁶",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._verified_entered_value",
            return_value="涼脊龍",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._proven_default_name_in_rename_dialog",
            return_value="涼脊龍",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._cancel_unverified_input",
            side_effect=cancelled,
        ) as cancel, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned = _resume_verified_unsubmitted_rename(proxy, dialog, settings)

        self.assertIs(returned, detail)
        cancel.assert_called_once_with(proxy, "涼脊龍")

    def test_journalled_nondefault_dialog_recovers_when_keyboard_hides_ax_field(self) -> None:
        settings = SimpleNamespace(journal_path=Path("/not-used"))
        journal = Mock()
        proxy = SimpleNamespace(
            observation=SimpleNamespace(text="rename"),
            pending_name=None,
            verified_renames=0,
            journal=journal,
        )
        dialog = Snapshot("rename", "dialog")
        detail = Snapshot("detail", "detail")
        nickname = "涼脊龍⓮⓯⓮⁹⁶"
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="RENAME_DIALOG",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._last_unsubmitted_journal_nickname",
            return_value=nickname,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._verified_entered_value",
            side_effect=PolicyViolation("改名窗口已打开，但 accessibility 未返回完整名称字段"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._proven_default_name_in_rename_dialog",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._journalled_nondefault_rename_can_resume_without_ax",
            return_value=True,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._dialog_evidence_after_keyboard_dismiss",
            return_value=(Snapshot("rename", "dialog"), False),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._submit_with_one_verified_retry",
            return_value=detail,
        ) as submit, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned = _resume_verified_unsubmitted_rename(proxy, dialog, settings)

        self.assertIs(returned, detail)
        submit.assert_called_once_with(
            proxy, nickname=nickname, initial_dialog=Snapshot("rename", "dialog")
        )
        self.assertEqual(proxy.verified_renames, 1)
        self.assertIsNone(proxy.pending_name)


    def test_default_dialog_proof_requires_name_inside_input_field(self) -> None:
        snapshot = Snapshot("rename", "dialog")
        boxes = SimpleNamespace(
            box=SimpleNamespace(left=150.0, right=310.0, center_y=570.0),
            image_width=1024,
            image_height=1366,
        )
        line = SimpleNamespace(text="涼脊龍", confidence=0.99)
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.ocr_mcp_screenshot",
            return_value=(line,),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.rename_dialog_visible",
            return_value=True,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.exact_species_from_lines",
            return_value=("涼脊龍", 0.99),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.locate_exact_text_from_mcp",
            return_value=boxes,
        ):
            self.assertEqual(_proven_default_name_in_rename_dialog(snapshot), "涼脊龍")

    def test_task_switcher_waits_for_existing_direct_detail(self) -> None:
        overview = Snapshot("程序坞\n账号安全", "overview")
        detail = Snapshot("detail", "detail")
        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._require_current_detail",
            side_effect=[PolicyViolation("not yet"), detail],
        ) as require, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=detail,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned = _wait_for_direct_detail_after_task_switcher(
                object(), overview
            )

        self.assertIs(returned, detail)
        self.assertEqual(require.call_count, 2)
        next_snapshot.assert_called_once_with(ANY, 3.0)
        emit.assert_called_once()

    def test_task_switcher_wait_reports_safe_wait_heartbeat(self) -> None:
        overview = Snapshot("程序坞\n账号安全", "overview")
        detail = Snapshot("detail", "detail")
        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._require_current_detail",
            side_effect=[
                PolicyViolation("not yet"),
                PolicyViolation("still covered"),
                detail,
            ],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=[overview, detail],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.time.monotonic",
            side_effect=[100.0, 116.0],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned = _wait_for_direct_detail_after_task_switcher(object(), overview)

        self.assertIs(returned, detail)
        self.assertEqual(emit.call_count, 2)
        self.assertIn("已安全等待 16 秒", emit.call_args_list[-1].kwargs["message"])

    def test_task_switcher_tolerates_one_unreadable_overlay_frame(self) -> None:
        overview = Snapshot("程序坞\n账号安全", "overview")
        unreadable = Snapshot("", "transient")
        detail = Snapshot("detail", "detail")
        proxy = SimpleNamespace(aborted=False)
        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._require_current_detail",
            side_effect=[PolicyViolation("not yet"), PolicyViolation("temporary"), detail],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=[unreadable, detail],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._has_ipad_task_switcher_overlay",
            side_effect=[True, False],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned = _wait_for_direct_detail_after_task_switcher(proxy, overview)

        self.assertIs(returned, detail)

    def test_reconnect_first_frame_waits_without_task_switcher_labels(self) -> None:
        """A transport reconnect may lose the Dock AX text for one frame."""

        transient = Snapshot("", "transient")
        detail = Snapshot("detail", "detail")
        proxy = SimpleNamespace(aborted=False)
        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._require_current_detail",
            side_effect=[PolicyViolation("unclassified reconnect frame"), detail],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=detail,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned = _wait_for_direct_detail_after_task_switcher(proxy, transient)

        self.assertIs(returned, detail)
        next_snapshot.assert_called_once_with(proxy, 3.0)


    def test_verified_next_detail_waits_out_a_brief_classifier_miss(self) -> None:
        first = Snapshot("stale classifier", "first")
        recovered = Snapshot("detail", "recovered")
        seeds = (Snapshot("", "seed-1"), Snapshot("", "seed-2"), first)
        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._require_current_detail",
            side_effect=[PolicyViolation("not yet"), recovered],
        ) as require, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=recovered,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned = _wait_for_verified_next_detail(
                object(), first, seed_samples=seeds
            )

        self.assertIs(returned, recovered)
        self.assertEqual(require.call_count, 2)
        next_snapshot.assert_called_once_with(ANY, 3.0)
        emit.assert_called_once()

    def test_verified_next_detail_stops_when_the_box_is_visible(self) -> None:
        inventory = Snapshot("box", "inventory")
        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._require_current_detail",
            side_effect=PolicyViolation("not detail"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="INVENTORY",
        ):
            with self.assertRaisesRegex(PolicyViolation, "已明确离开宝可梦详情页"):
                _wait_for_verified_next_detail(object(), inventory, seed_samples=())

    def test_appraisal_suffix_fragment_keeps_proven_default_species(self) -> None:
        detail = _default_name("蟲寶包")
        appraisal = NameRegionResult(
            species="蟲寶包",
            is_default=False,
            confidence=0.99,
            evidence=("蟲寶包", "包"),
        )

        self.assertTrue(_appraisal_identity_matches_current_detail(appraisal, detail))

    def test_appraisal_suffix_exception_rejects_numeric_but_accepts_status_label(self) -> None:
        detail = _default_name("蟲寶包")
        numeric = NameRegionResult(
            species="蟲寶包",
            is_default=False,
            confidence=0.99,
            evidence=("蟲寶包", "9"),
        )
        other_text = NameRegionResult(
            species="蟲寶包",
            is_default=False,
            confidence=0.99,
            evidence=("蟲寶包", "亮晶晶"),
        )

        self.assertFalse(_appraisal_identity_matches_current_detail(numeric, detail))
        self.assertTrue(_appraisal_identity_matches_current_detail(other_text, detail))

    def test_empty_appraisal_title_is_allowed_only_on_direct_detail_route(self) -> None:
        empty_title = NameRegionResult(
            species="", is_default=False, confidence=0.0, evidence=()
        )
        recognized_text = NameRegionResult(
            species="", is_default=False, confidence=0.8, evidence=("妙蛙種子",)
        )

        self.assertTrue(
            _empty_appraisal_title_is_safe_on_direct_route(
                empty_title, current_detail_only=True
            )
        )
        self.assertFalse(
            _empty_appraisal_title_is_safe_on_direct_route(
                empty_title, current_detail_only=False
            )
        )
        self.assertFalse(
            _empty_appraisal_title_is_safe_on_direct_route(
                recognized_text, current_detail_only=True
            )
        )

    def test_partial_appraisal_title_does_not_skip_proven_default(self) -> None:
        before = Snapshot("", "detail")
        appraisal = Snapshot("", "appraisal")
        restored_detail = Snapshot("", "restored-detail")
        measurement = IVMeasurement(10, 2, 1, 0.943, (1, 2, 3), (4, 5, 6))
        detail_name = _default_name("蟲寶包")
        partial_appraisal_name = NameRegionResult(
            species="蟲寶包",
            is_default=False,
            confidence=0.99,
            evidence=("蟲寶包", "包"),
        )

        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=before,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=(before, detail_name),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=partial_appraisal_name,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=restored_detail,
        ) as close, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned, outcome = _process_one(
                object(), before, mode="scan", index=41
            )

        self.assertIs(returned, restored_detail)
        self.assertEqual(outcome, "scanned")
        close.assert_called_once()
        self.assertIn("末尾残片", emit.call_args_list[-2].kwargs["message"])

    def test_empty_appraisal_title_on_direct_route_keeps_proven_default(self) -> None:
        before = Snapshot("", "detail")
        appraisal = Snapshot("", "appraisal")
        restored_detail = Snapshot("", "restored-detail")
        measurement = IVMeasurement(14, 15, 15, 0.95, (1, 2, 3), (4, 5, 6))
        detail_name = _default_name("一對鼠")
        empty_appraisal_name = NameRegionResult(
            species="", is_default=False, confidence=0.0, evidence=()
        )

        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._wait_for_verified_next_detail",
            return_value=before,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=(before, detail_name),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_from_current_detail_only",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=empty_appraisal_name,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=restored_detail,
        ) as close, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned, outcome = _process_one(
                object(),
                before,
                mode="scan",
                index=42,
                current_detail_only=True,
            )

        self.assertIs(returned, restored_detail)
        self.assertEqual(outcome, "scanned")
        close.assert_called_once()
        self.assertIn("标题本帧未返回文字", emit.call_args_list[-2].kwargs["message"])

    def test_auto_mode_uses_current_detail_without_navigation(self) -> None:
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ):
            self.assertTrue(_current_detail_only(detail))

    def test_explicit_automatic_entry_keeps_recovery_available_from_a_detail(self) -> None:
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        with patch.dict("os.environ", {"POGO_START_FROM_CURRENT_DETAIL": "false"}), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ):
            self.assertFalse(_current_detail_only(detail))

    def test_auto_mode_keeps_legacy_entry_for_a_game_map(self) -> None:
        game_map = Snapshot("map", "map")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected",
            side_effect=PolicyViolation("not detail"),
        ):
            self.assertFalse(_current_detail_only(game_map))

    def test_automatic_processing_returns_a_verified_lower_detail_to_its_title(self) -> None:
        lower_detail = Snapshot("CP 100 20 / 20 HP 1 kg", "lower")
        title_detail = Snapshot("CP 100 20 / 20 HP 1 kg", "title")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=lower_detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._scroll_verified_detail_to_title",
            return_value=title_detail,
        ) as scroll, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=None,
        ):
            with self.assertRaises(FreshDetailIdentityUnavailable) as raised:
                _process_one(
                    object(), lower_detail, mode="scan", index=1, current_detail_only=False
                )

        scroll.assert_called_once_with(ANY, lower_detail)
        self.assertIs(raised.exception.snapshot, title_detail)

    def test_auto_mode_rejects_stage_manager_overview_without_navigation(self) -> None:
        overview = Snapshot("程序坞\nShijima\n设置", "overview")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected",
            side_effect=PolicyViolation("not detail"),
        ):
            with self.assertRaises(PolicyViolation):
                _current_detail_only(overview)

    def test_direct_route_accepts_an_existing_ocr_proven_detail_menu(self) -> None:
        menu = Snapshot("調查寶可夢", "menu")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="DETAIL_MENU",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ) as validate:
            self.assertIs(_require_current_detail(menu), menu)

        validate.assert_not_called()

    def test_direct_resume_closes_only_a_proven_appraisal_overlay(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        proxy = object()
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="APPRAISAL_BARS",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._wait_for_direct_stage_geometry",
            return_value=appraisal,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ) as close, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned = _restore_direct_detail_after_interrupted_appraisal(
                proxy, appraisal
            )

        self.assertIs(returned, detail)
        close.assert_called_once_with(proxy)
        self.assertIn("遗留的鉴定层", emit.call_args.kwargs["message"])

    def test_direct_resume_advances_a_proven_appraisal_dialogue_once(self) -> None:
        dialogue = Snapshot("", "dialogue")
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        proxy = object()
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="APPRAISAL_DIALOG",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._wait_for_direct_stage_geometry",
            return_value=dialogue,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_read_only_measurement_retry",
            return_value=(appraisal, object()),
        ) as advance, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ) as close, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned = _restore_direct_detail_after_interrupted_appraisal(
                proxy, dialogue
            )

        self.assertIs(returned, detail)
        advance.assert_called_once_with(proxy, dialogue)
        close.assert_called_once_with(proxy)

    def test_existing_detail_menu_returns_to_same_detail_before_identity_reads(self) -> None:
        menu = Snapshot("調查寶可夢", "menu")
        appraisal = Snapshot("bars", "appraisal")
        detail = Snapshot("可達鴨 21/21 HP", "detail")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_from_current_detail_only",
            return_value=(appraisal, object()),
        ) as appraise, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ) as close, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned = _restore_direct_detail_from_existing_menu(object(), menu)

        self.assertIs(returned, detail)
        appraise.assert_called_once()
        close.assert_called_once()
        self.assertIn("遗留的详情菜单", emit.call_args.kwargs["message"])

    def test_detail_capture_wait_never_permits_game_restart(self) -> None:
        proxy = object()
        snapshot = Snapshot("detail", "frame")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.wait_for_capture_channel",
            return_value=snapshot,
        ) as wait:
            self.assertIs(_wait_without_game_restart(proxy, snapshot), snapshot)

        wait.assert_called_once_with(proxy, snapshot, allow_game_restart=False)

    def test_current_detail_navigation_uses_direct_reader_not_legacy_adapter(self) -> None:
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        appraisal = Snapshot("", "appraisal")
        measurement = object()

        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_read_only_measurement_retry",
            return_value=(appraisal, measurement),
        ) as direct_reader, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery"
        ) as legacy_adapter, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.navigate_to_appraisal_v14"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._tap"
        ) as tap:
            returned = _navigate_from_current_detail_only(object(), detail)

        self.assertEqual(returned, (appraisal, measurement))
        direct_reader.assert_called_once_with(ANY, detail)
        legacy_adapter.assert_not_called()
        tap.assert_not_called()

    def test_current_detail_navigation_blocks_frozen_base_inventory_tap(self) -> None:
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")

        def invoke_frozen_base_navigator(proxy, snapshot):
            # v24 keeps an import-time reference to the original base
            # navigator.  Simulate that lower layer asking to tap a storage
            # card after a classifier disagreement.
            base._tap(proxy, "INVENTORY")

        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_read_only_measurement_retry",
            side_effect=invoke_frozen_base_navigator,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._tap"
        ) as original_tap:
            with self.assertRaisesRegex(PolicyViolation, "第一只可见宝可梦"):
                _navigate_from_current_detail_only(object(), detail)

        original_tap.assert_not_called()

    def test_current_detail_navigation_allows_only_appraisal_controls(self) -> None:
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")

        def direct_appraisal_controls(proxy, snapshot):
            for key in ("DETAIL", "DETAIL_MENU", "APPRAISAL_DIALOG", "APPRAISAL_CLOSE"):
                base._tap(proxy, key)
            return snapshot, object()

        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_read_only_measurement_retry",
            side_effect=direct_appraisal_controls,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._tap"
        ) as original_tap:
            result = _navigate_from_current_detail_only(object(), detail)

        self.assertEqual(result[0], detail)
        self.assertEqual(
            [call.args[1] for call in original_tap.call_args_list],
            ["DETAIL", "DETAIL_MENU", "APPRAISAL_DIALOG", "APPRAISAL_CLOSE"],
        )

    def test_current_detail_process_uses_guarded_appraisal_navigation(self) -> None:
        before = Snapshot("", "detail")
        appraisal = Snapshot("", "appraisal")
        restored_detail = Snapshot("", "restored-detail")
        measurement = IVMeasurement(12, 11, 10, 0.95, (1, 2, 3), (4, 5, 6))
        default = _default_name()

        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._require_current_detail",
            return_value=before,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=(before, default),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_from_current_detail_only",
            return_value=(appraisal, measurement),
        ) as direct_navigation, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery"
        ) as legacy_navigation, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=default,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=restored_detail,
        ):
            returned, outcome = _process_one(
                object(), before, mode="scan", index=1, current_detail_only=True
            )

        self.assertIs(returned, restored_detail)
        self.assertEqual(outcome, "scanned")
        direct_navigation.assert_called_once_with(ANY, before)
        legacy_navigation.assert_not_called()

    def test_successful_commit_reuses_its_verified_detail_snapshot(self) -> None:
        before = Snapshot("", "detail")
        appraisal = Snapshot("", "appraisal")
        ready_for_rename = Snapshot("", "ready-for-rename")
        committed_detail = Snapshot("CP1 1/1HP 1kg", "committed-detail")
        measurement = IVMeasurement(12, 11, 10, 0.95, (1, 2, 3), (4, 5, 6))
        default = _default_name("可達鴨")

        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=before,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=(before, default),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=default,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=ready_for_rename,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.open_dynamic_rename_from_detail"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._commit_after_dismissing_keyboard",
            return_value=committed_detail,
        ) as commit, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.screen_snapshot"
        ) as screenshot, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ) as validate, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned, outcome = _process_one(
                object(), before, mode="rename", index=1
            )

        self.assertIs(returned, committed_detail)
        self.assertEqual(outcome, "renamed")
        commit.assert_called_once()
        screenshot.assert_not_called()
        validate.assert_called_once_with("DETAIL", committed_detail)

    def test_unsafe_stage_manager_geometry_is_recoverable(self) -> None:
        self.assertTrue(
            _is_unsafe_stage_manager_geometry(
                ValueError("detected Stage Manager game-window geometry is unsafe")
            )
        )
        self.assertFalse(_is_unsafe_stage_manager_geometry(ValueError("bad image")))

    def test_stage_manager_identity_and_swipe_misses_are_recoverable(self) -> None:
        self.assertTrue(
            _is_recoverable_navigation_failure(
                PolicyViolation("详情页稳定身份字段不足；不会自动翻页")
            )
        )
        self.assertTrue(
            _is_recoverable_navigation_failure(
                PolicyViolation("横向翻页后连续只读采样仍无法确认安全详情页")
            )
        )
        self.assertTrue(
            _is_recoverable_navigation_failure(
                PolicyViolation("页面在等待 12 秒后仍为 MAP，未到达 MAIN_MENU")
            )
        )
        self.assertFalse(
            _is_recoverable_navigation_failure(PolicyViolation("鉴定条仍可见"))
        )

    def test_detail_entry_uses_resilient_navigation_until_detail(self) -> None:
        initial = Snapshot("map", "map")
        inventory = Snapshot("inventory", "inventory")
        detail = Snapshot("CP 100 20/20 HP 1 kg", "detail")

        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.robust_page_state",
            return_value="MAP",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14._transition",
            side_effect=[(inventory, "INVENTORY"), (detail, "DETAIL")],
        ) as transition, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ) as validate:
            returned = _ensure_plain_detail(object(), initial)

        self.assertIs(returned, detail)
        self.assertEqual(
            [call.args[2] for call in transition.call_args_list],
            ["MAP", "INVENTORY"],
        )
        validate.assert_called_once_with("DETAIL", detail)

    def test_manual_unlock_home_launches_only_configured_game(self) -> None:
        snapshot = Snapshot("SpringBoard", "home")

        class Proxy:
            settings = SimpleNamespace(
                pokemon_go_bundle_id="com.nianticlabs.pokemongo"
            )
            observation = Observation("home-token", 0.0, "SpringBoard", 1366, 1024)

            def __init__(self) -> None:
                self.calls: list[tuple[str, dict]] = []

            def call_tool(self, name: str, arguments: dict) -> dict:
                self.calls.append((name, arguments))
                if name == "get_frontmost_app":
                    return {
                        "content": [
                            {"type": "text", "text": "com.apple.springboard"}
                        ]
                    }
                if name == "launch_app":
                    self.observation = Observation(
                        "game-token",
                        0.0,
                        "com.nianticlabs.pokemongo",
                        1366,
                        1024,
                    )
                return {}

        proxy = Proxy()
        game = Snapshot("Pokemon GO HP kg", "game")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=game,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.wait_for_capture_channel",
            return_value=game,
        ):
            result = _ensure_game_foreground(proxy, snapshot)

        self.assertIs(result, game)
        self.assertEqual(
            [name for name, _ in proxy.calls],
            ["get_frontmost_app", "launch_app"],
        )
        self.assertEqual(
            proxy.calls[1][1]["bundle_id"], "com.nianticlabs.pokemongo"
        )
        self.assertEqual(proxy.calls[1][1]["_observation_token"], "home-token")

    def test_close_appraisal_recovers_black_capture_before_validation(self) -> None:
        black = Snapshot("", "black")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        proxy = object()
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=black,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.snapshot_is_black",
            return_value=True,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.wait_for_capture_channel",
            return_value=detail,
        ) as recover, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ) as validate:
            returned = _close_appraisal(proxy)

        self.assertIs(returned, detail)
        tap.assert_called_once_with(proxy, "APPRAISAL_CLOSE")
        recover.assert_called_once_with(proxy, black, allow_game_restart=False)
        validate.assert_called_once_with("DETAIL", detail)

    def test_close_appraisal_does_not_leak_raw_measurement_error(self) -> None:
        unknown = Snapshot("", "unknown")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._tap"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=unknown,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.snapshot_is_black",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected",
            side_effect=PolicyViolation("not detail"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.measure_ipad14_6_appraisal",
            side_effect=ValueError("no tracks"),
        ):
            with self.assertRaises(PolicyViolation) as raised:
                _close_appraisal(object())

        self.assertNotIsInstance(raised.exception, ValueError)
        self.assertIn("未重复点击", str(raised.exception))

    def test_close_appraisal_waits_through_transition_without_second_tap(self) -> None:
        transition = Snapshot("", "transition")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")

        def validate(_expected: str, snapshot: Snapshot) -> None:
            if snapshot is not detail:
                raise PolicyViolation("not detail")

        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=[transition, transition, detail],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.snapshot_is_black",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected",
            side_effect=validate,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.measure_ipad14_6_appraisal",
            side_effect=ValueError("transition has no stable tracks"),
        ):
            returned = _close_appraisal(object())

        self.assertIs(returned, detail)
        tap.assert_called_once()

    def test_close_appraisal_retries_only_after_two_final_proven_frames(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")

        def validate(_expected: str, snapshot: Snapshot) -> None:
            if snapshot is not detail:
                raise PolicyViolation("not detail")

        measurement = IVMeasurement(1, 2, 3, 0.95, (1, 2, 3), (4, 5, 6))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=[appraisal] * 5 + [detail],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.snapshot_is_black",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected",
            side_effect=validate,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.measure_ipad14_6_appraisal",
            return_value=measurement,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned = _close_appraisal(object())

        self.assertIs(returned, detail)
        self.assertEqual(tap.call_count, 2)

    def test_pause_waits_without_phone_io_then_refreshes_same_detail(self) -> None:
        counts = {"renamed": 2, "skipped": 3, "scanned": 0, "unreadable": 1}
        fingerprint = DetailFingerprint(("皮卡丘",), "cp1", "1/1hp", "1kg", "1m")
        detail = Snapshot("CP1 1/1HP 1kg 1m", "detail")
        with tempfile.TemporaryDirectory() as directory:
            pause = BatchPauseFile(Path(directory) / "batch.pause")
            pause.request()

            def resume_after_one_poll(_seconds: float) -> None:
                pause.resume()

            with patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.time.sleep",
                side_effect=resume_after_one_poll,
            ), patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.screen_snapshot",
                return_value=detail,
            ) as refresh, patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
            ), patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.detail_fingerprint",
                return_value=fingerprint,
            ), patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._emit_progress"
            ) as progress, patch(
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
            ):
                returned = _wait_at_safe_pause_boundary(
                    object(),
                    detail,
                    fingerprint=fingerprint,
                    index=6,
                    limit=0,
                    counts=counts,
                    pause=pause,
                )

        self.assertIs(returned, detail)
        refresh.assert_called_once()
        self.assertEqual(
            [item.kwargs["phase"] for item in progress.call_args_list],
            ["paused", "resumed"],
        )

    def test_unreadable_appraisal_is_preserved_and_skipped(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        before = Snapshot("", "before")
        error = AppraisalMeasurementUnavailable(
            appraisal, ValueError("no appraisal tracks")
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=before,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=(before, _default_name()),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            side_effect=error,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ) as close, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned, outcome = _process_one(
                object(), before, mode="rename", index=10
            )

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "unreadable")
        close.assert_called_once()
        self.assertIn("继续下一只", emit.call_args.kwargs["message"])

    def test_low_confidence_measurement_preserves_one_and_continues(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        measurement = IVMeasurement(
            8, 4, 8, 0.881, (1, 2, 3), (4, 5, 6)
        )
        before = Snapshot("", "before")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=before,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=(before, _default_name()),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ) as close, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region"
        ) as analyze_name, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned, outcome = _process_one(
                object(), before, mode="rename", index=1
            )

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "unreadable")
        close.assert_called_once()
        analyze_name.assert_not_called()
        self.assertIn("继续下一只", emit.call_args.kwargs["message"])

    def test_low_confidence_measurement_accepts_three_matching_frames(self) -> None:
        first_snapshot = Snapshot("", "first")
        second_snapshot = Snapshot("", "second")
        third_snapshot = Snapshot("", "third")
        first = IVMeasurement(12, 4, 3, 0.925, (1, 2, 3), (4, 5, 6))
        second = IVMeasurement(12, 4, 3, 0.934, (1, 2, 3), (4, 5, 6))
        third = IVMeasurement(12, 4, 3, 0.929, (1, 2, 3), (4, 5, 6))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=[second_snapshot, third_snapshot],
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.snapshot_is_black",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.measure_ipad14_6_appraisal",
            side_effect=[second, third],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._snapshot_digest",
            side_effect=["hash-1", "hash-2", "hash-3"],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned = _confirm_low_confidence_measurement(
                object(), first_snapshot, first
            )

        self.assertEqual(returned, (third_snapshot, third))
        self.assertIn("三张未复用像素帧", emit.call_args.kwargs["message"])
        self.assertEqual(
            [call.args[1] for call in next_snapshot.call_args_list],
            [0.9, 0.9],
        )

    def test_detail_identity_rejects_old_hash_and_requires_three_new_frames(self) -> None:
        old = Snapshot("", "old")
        new_frames = [Snapshot("", f"new-{index}") for index in range(3)]
        result = _default_name("黏黏寶")
        proxy = SimpleNamespace(_pogo_verified_frame_history=["old-hash"])
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=new_frames,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._snapshot_digest",
            side_effect=["old-hash", "new-1", "new-2", "new-3"],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=result,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            confirmed = _confirm_fresh_detail_identity(proxy, old)

        self.assertEqual(confirmed, (new_frames[-1], result))
        self.assertEqual(
            proxy._pogo_verified_frame_history,
            ["old-hash", "new-1", "new-2", "new-3"],
        )
        self.assertEqual(
            [call.args[1] for call in next_snapshot.call_args_list],
            [0.8, 0.8, 1.0],
        )

    def test_post_swipe_default_evidence_reuses_three_fresh_frames(self) -> None:
        seeds = tuple(Snapshot("", f"seed-{index}") for index in range(3))
        result = _default_name("蟲寶包")
        proxy = SimpleNamespace(_pogo_verified_frame_history=[])
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot"
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._snapshot_digest",
            side_effect=["seed-1", "seed-2", "seed-3"],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=result,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            confirmed = _confirm_fresh_detail_identity(
                proxy, seeds[-1], seed_samples=seeds
            )

        self.assertEqual(confirmed, (seeds[-1], result))
        self.assertEqual(proxy._pogo_verified_frame_history, ["seed-1", "seed-2", "seed-3"])
        next_snapshot.assert_not_called()
        self.assertIn("翻页后的三张新鲜身份帧", emit.call_args.kwargs["message"])

    def test_ambiguous_post_swipe_evidence_falls_back_to_fresh_identity_reads(self) -> None:
        seeds = tuple(Snapshot("", f"seed-{index}") for index in range(3))
        fresh = [Snapshot("", f"fresh-{index}") for index in range(2)]
        ambiguous = NameRegionResult(
            species="蟲寶包",
            is_default=False,
            confidence=0.99,
            evidence=("蟲寶包", "包"),
        )
        verified = _default_name("蟲寶包")
        proxy = SimpleNamespace(_pogo_verified_frame_history=[])
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=fresh,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._snapshot_digest",
            side_effect=["seed-1", "fallback-1", "fallback-2", "fallback-3"],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            side_effect=[ambiguous, verified, verified, verified],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            confirmed = _confirm_fresh_detail_identity(
                proxy, seeds[-1], seed_samples=seeds
            )

        self.assertEqual(confirmed, (fresh[-1], verified))
        self.assertEqual(next_snapshot.call_count, 2)
        self.assertEqual(
            proxy._pogo_verified_frame_history,
            ["fallback-1", "fallback-2", "fallback-3"],
        )

    def test_non_species_labels_never_confirm_a_custom_nickname(self) -> None:
        """Move labels are not a name, even when OCR reads them consistently."""

        labels = NameRegionResult(
            species=None,
            is_default=False,
            confidence=0.99,
            evidence=("道館對戰&團體戰", "訓練家對戰"),
        )
        self.assertIsNone(_detail_name_key(labels))

        initial = Snapshot("", "initial")
        later = [Snapshot("", f"later-{index}") for index in range(11)]
        proxy = SimpleNamespace(_pogo_verified_frame_history=[])
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=later,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._snapshot_digest",
            side_effect=[f"hash-{index}" for index in range(12)],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=labels,
        ), patch("pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"):
            confirmed = _confirm_fresh_detail_identity(proxy, initial)

        self.assertIsNone(confirmed)
        self.assertEqual(proxy._pogo_verified_frame_history, [])

    def test_appraisal_consensus_does_not_count_duplicate_pixels_twice(self) -> None:
        frames = [Snapshot("", name) for name in ("duplicate", "new-2", "new-3")]
        measurement = IVMeasurement(15, 14, 12, 0.95, (1, 2, 3), (4, 5, 6))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            side_effect=frames,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.v14.snapshot_is_black",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.measure_ipad14_6_appraisal",
            return_value=measurement,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._snapshot_digest",
            side_effect=["hash-1", "hash-1", "hash-2", "hash-3"],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            confirmed = _confirm_low_confidence_measurement(
                SimpleNamespace(), Snapshot("", "initial"), measurement
            )

        self.assertEqual(confirmed, (frames[-1], measurement))

    def test_existing_iv_nickname_never_opens_rename_dialog(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        measurement = IVMeasurement(15, 14, 14, 0.99, (1, 2, 3), (4, 5, 6))
        existing = NameRegionResult(
            species="輕飄飄",
            is_default=False,
            confidence=0.99,
            evidence=("輕飄飄", "15", "14", "14", "96"),
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=(detail, existing),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ) as navigate, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.open_dynamic_rename_from_detail"
        ) as open_dialog, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._commit_after_dismissing_keyboard"
        ) as commit, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned, outcome = _process_one(
                object(), Snapshot("", "before"), mode="rename", index=3
            )

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "skipped")
        navigate.assert_not_called()
        open_dialog.assert_not_called()
        commit.assert_not_called()

    def test_unreadable_name_boundary_preserves_one_and_continues(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        before = Snapshot("", "before")
        measurement = IVMeasurement(0, 1, 3, 0.97, (1, 2, 3), (4, 5, 6))
        default = NameRegionResult(
            species="可達鴨",
            is_default=True,
            confidence=0.99,
            evidence=("可達鴨",),
        )
        error = RenamePencilLocalizationUnavailable(
            detail, PolicyViolation("empty OCR")
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=before,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=(before, default),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=default,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.open_dynamic_rename_from_detail",
            side_effect=error,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._commit_after_dismissing_keyboard"
        ) as commit, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ) as validate, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned, outcome = _process_one(
                object(), before, mode="rename", index=7
            )

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "unreadable")
        validate.assert_called_once_with("DETAIL", detail)
        commit.assert_not_called()
        self.assertIn("继续下一只", emit.call_args.kwargs["message"])

    def test_cached_appraisal_species_mismatch_never_opens_rename(self) -> None:
        before = Snapshot("", "fresh-detail")
        appraisal = Snapshot("", "cached-appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        measurement = IVMeasurement(15, 14, 12, 0.95, (1, 2, 3), (4, 5, 6))
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=before,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=(before, _default_name("黏黏寶")),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=_default_name("可達鴨"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.open_dynamic_rename_from_detail"
        ) as open_dialog, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._commit_after_dismissing_keyboard"
        ) as commit, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned, outcome = _process_one(
                object(), before, mode="rename", index=2
            )

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "unreadable")
        open_dialog.assert_not_called()
        commit.assert_not_called()

    def test_unverifiable_typed_field_is_preserved_and_continues(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        before = Snapshot("", "before")
        measurement = IVMeasurement(6, 2, 12, 0.97, (1, 2, 3), (4, 5, 6))
        default = NameRegionResult(
            species="滑滑小子",
            is_default=True,
            confidence=0.99,
            evidence=("滑滑小子",),
        )
        error = RenameFieldVerificationUnavailable(detail, "")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=before,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=(before, default),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=default,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.open_dynamic_rename_from_detail"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._commit_after_dismissing_keyboard",
            side_effect=error,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ) as validate, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ) as emit:
            returned, outcome = _process_one(
                object(), before, mode="rename", index=1
            )

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "unreadable")
        validate.assert_called_once_with("DETAIL", detail)
        self.assertIn("继续下一只", emit.call_args.kwargs["message"])

    def test_unverified_visible_dialog_resumes_with_proxy_run_settings(self) -> None:
        """The visible-dialog recovery must not depend on run-local scope."""

        appraisal = Snapshot("", "appraisal")
        before = Snapshot("", "before")
        dialog = Snapshot("rename", "dialog")
        detail = Snapshot("CP1 1/1HP 1kg", "detail")
        measurement = IVMeasurement(6, 2, 12, 0.97, (1, 2, 3), (4, 5, 6))
        default = _default_name("滑滑小子")
        configured = SimpleNamespace(journal_path=Path("/not-used"))
        proxy = SimpleNamespace(settings=configured, verified_renames=0)

        def resume(active_proxy, snapshot, active_settings):
            self.assertIs(active_proxy, proxy)
            self.assertIs(active_settings, configured)
            active_proxy.verified_renames += 1
            return detail

        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._ensure_plain_detail",
            return_value=before,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_fresh_detail_identity",
            return_value=(before, default),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._navigate_with_complete_stale_recovery",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._confirm_low_confidence_measurement",
            return_value=(appraisal, measurement),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.analyze_name_region",
            return_value=default,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._close_appraisal",
            return_value=detail,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.open_dynamic_rename_from_detail"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._commit_after_dismissing_keyboard",
            side_effect=RenameFieldVerificationUnavailable(dialog, ""),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="RENAME_DIALOG",
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._resume_verified_unsubmitted_rename",
            side_effect=resume,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned, outcome = _process_one(proxy, before, mode="rename", index=1)

        self.assertIs(returned, detail)
        self.assertEqual(outcome, "renamed")


if __name__ == "__main__":
    unittest.main()
