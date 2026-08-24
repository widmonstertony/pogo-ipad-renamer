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
    _appraisal_identity_matches_current_detail,
    _close_appraisal,
    _confirm_fresh_detail_identity,
    _confirm_low_confidence_measurement,
    _ensure_game_foreground,
    _ensure_plain_detail,
    _bring_proven_direct_detail_to_foreground,
    _current_detail_only,
    _last_unsubmitted_journal_nickname,
    _restore_direct_detail_after_interrupted_appraisal,
    _is_recoverable_navigation_failure,
    _is_unsafe_stage_manager_geometry,
    _navigate_from_current_detail_only,
    _process_one,
    _proven_default_name_in_rename_dialog,
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
                "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._dialog_evidence_after_keyboard_dismiss"
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
            submit.assert_called_once_with(proxy, nickname="奈克洛❿❿⓫⁶⁹")
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
            side_effect=[PolicyViolation("not yet"), detail, detail],
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
        self.assertEqual(require.call_count, 3)
        next_snapshot.assert_called_once_with(ANY, 3.0)
        emit.assert_called_once()

    def test_proven_direct_detail_card_is_selected_once_to_clear_multiwindow(self) -> None:
        detail = Snapshot("程序坞", "detail")
        foreground = Snapshot("detail", "foreground")
        observation = SimpleNamespace(token="fresh", width=1366.0, height=1024.0)
        proxy = SimpleNamespace(observation=observation, calls=[])

        def call_tool(name, arguments):
            proxy.calls.append((name, arguments))
            return {}

        proxy.call_tool = call_tool
        geometry = SimpleNamespace()
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._require_current_detail",
            side_effect=[detail, foreground],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._remember_stage_geometry"
        ) as remember, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.current_stage_geometry",
            return_value=geometry,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.upright_ratio_to_touch",
            return_value=(673.0, 674.0),
        ) as touch, patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._next_snapshot",
            return_value=foreground,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.emit"
        ):
            returned = _bring_proven_direct_detail_to_foreground(proxy, detail)

        self.assertIs(returned, foreground)
        remember.assert_called_once_with(proxy, detail)
        touch.assert_called_once_with(
            1366.0, 1024.0, 0.5, 0.5, geometry=geometry
        )
        self.assertEqual(len(proxy.calls), 1)
        name, arguments = proxy.calls[0]
        self.assertEqual(name, "tap_screen")
        self.assertEqual(arguments["_observation_token"], "fresh")
        self.assertEqual((arguments["x"], arguments["y"]), (673.0, 674.0))

    def test_proven_direct_detail_does_not_tap_without_multiwindow_overlay(self) -> None:
        detail = Snapshot("detail", "detail")
        proxy = SimpleNamespace(observation=None)
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26._require_current_detail",
            return_value=detail,
        ):
            returned = _bring_proven_direct_detail_to_foreground(proxy, detail)

        self.assertIs(returned, detail)

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

    def test_appraisal_suffix_fragment_keeps_proven_default_species(self) -> None:
        detail = _default_name("蟲寶包")
        appraisal = NameRegionResult(
            species="蟲寶包",
            is_default=False,
            confidence=0.99,
            evidence=("蟲寶包", "包"),
        )

        self.assertTrue(_appraisal_identity_matches_current_detail(appraisal, detail))

    def test_appraisal_suffix_exception_rejects_numeric_or_other_text(self) -> None:
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
        self.assertFalse(_appraisal_identity_matches_current_detail(other_text, detail))

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

    def test_auto_mode_uses_current_detail_without_navigation(self) -> None:
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected"
        ):
            self.assertTrue(_current_detail_only(detail))

    def test_auto_mode_keeps_legacy_entry_for_a_game_map(self) -> None:
        game_map = Snapshot("map", "map")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected",
            side_effect=PolicyViolation("not detail"),
        ):
            self.assertFalse(_current_detail_only(game_map))

    def test_auto_mode_rejects_stage_manager_overview_without_navigation(self) -> None:
        overview = Snapshot("程序坞\nShijima\n设置", "overview")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base._validate_expected",
            side_effect=PolicyViolation("not detail"),
        ):
            with self.assertRaises(PolicyViolation):
                _current_detail_only(overview)

    def test_direct_resume_closes_only_a_proven_appraisal_overlay(self) -> None:
        appraisal = Snapshot("", "appraisal")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        proxy = object()
        with patch(
            "pogo_iphone_renamer.ipad_landscape_batch_agent_v26.base.local_page_state",
            return_value="APPRAISAL_BARS",
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


if __name__ == "__main__":
    unittest.main()
