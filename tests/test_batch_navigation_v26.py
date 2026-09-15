from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.policy import PolicyViolation
from pogo_iphone_renamer.batch_navigation_v26 import (
    DetailFingerprint,
    DetailExitedToOverview,
    NoNextPokemon,
    VerifiedEndOfStorage,
    _observe_after_swipe,
    _overview_state,
    _stable_baseline,
    _swipe_next_once,
    _wait_for_post_swipe_identity,
    detail_fingerprint,
    fingerprints_differ,
    navigation_confirmation_key,
    swipe_to_verified_next,
    wait_for_stable_detail_fingerprint,
)


class DetailFingerprintTests(unittest.TestCase):
    def test_name_free_fingerprint_keeps_numeric_identity_for_confirmed_nickname(self) -> None:
        """Only an explicit navigation fallback may omit an unreadable title."""

        snapshot = Snapshot("CP 500 60/60 HP 6.0 kg 0.4 m", "detail")
        unreadable_name = SimpleNamespace(evidence=())
        full_lines = (
            SimpleNamespace(text="CP500", confidence=0.99),
            SimpleNamespace(text="60/60HP", confidence=0.99),
            SimpleNamespace(text="6.0kg", confidence=0.99),
            SimpleNamespace(text="0.4m", confidence=0.99),
        )
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.measure_ipad14_6_appraisal",
            side_effect=ValueError("plain detail"),
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.analyze_name_region",
            return_value=unreadable_name,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.ocr_mcp_screenshot",
            return_value=full_lines,
        ):
            with self.assertRaises(PolicyViolation):
                detail_fingerprint(snapshot)
            fingerprint = detail_fingerprint(snapshot, require_name=False)

        self.assertEqual(fingerprint.name_tokens, ())
        self.assertEqual(fingerprint.cp, "cp500")
        self.assertEqual(fingerprint.hp, "60/60hp")

    def test_full_frame_species_recovers_identity_when_name_crop_only_reads_hp(self) -> None:
        snapshot = Snapshot("CP 500 60/60 HP 6.0 kg", "detail")
        cropped_name = SimpleNamespace(evidence=("60/60 HP",))
        full_lines = (
            SimpleNamespace(text="皮卡丘", confidence=0.99),
            SimpleNamespace(text="CP500", confidence=0.99),
            SimpleNamespace(text="60/60HP", confidence=0.99),
            SimpleNamespace(text="6.0kg", confidence=0.99),
        )
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.measure_ipad14_6_appraisal",
            side_effect=ValueError("plain detail"),
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.analyze_name_region",
            return_value=cropped_name,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.ocr_mcp_screenshot",
            return_value=full_lines,
        ):
            fingerprint = detail_fingerprint(snapshot)

        self.assertEqual(fingerprint.name_tokens, ("皮卡丘",))
        self.assertEqual(fingerprint.cp, "cp500")

    def test_swipe_helper_always_issues_the_write_call(self) -> None:
        proxy = SimpleNamespace(
            observation=SimpleNamespace(
                width=1366,
                height=1024,
                token="fresh-observation",
            ),
            call_tool=Mock(),
        )
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.base.current_stage_geometry",
            return_value="geometry",
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.base.upright_ratio_to_touch",
            side_effect=[(1065, 512), (301, 512)],
        ) as mapper:
            _swipe_next_once(proxy)

        proxy.call_tool.assert_called_once_with(
            "swipe_screen",
            {
                "fromX": 1065,
                "fromY": 512,
                "toX": 301,
                "toY": 512,
                "duration": 320,
                "steps": 20,
                "_observation_token": "fresh-observation",
                "_intent": "navigate left to next Pokemon detail",
                "_expected_after": "DETAIL for a different Pokemon",
            },
        )
        self.assertEqual(
            [call.args[2:] for call in mapper.call_args_list],
            [(0.78, 0.28), (0.22, 0.28)],
        )

    def test_portrait_window_keeps_visual_horizontal_swipe_in_window_coordinates(self) -> None:
        proxy = SimpleNamespace(
            observation=SimpleNamespace(width=1366, height=1024, token="fresh"),
            call_tool=Mock(),
        )
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.base.ORIENTATION",
            "STAGE_MANAGER_PORTRAIT_WINDOW",
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.base.current_stage_geometry",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.base.upright_ratio_to_touch",
            side_effect=[(889.0, 431.0), (477.0, 431.0)],
        ):
            _swipe_next_once(proxy)

        proxy.call_tool.assert_called_once_with(
            "swipe_screen",
            {
                "fromX": 889.0,
                "fromY": 431.0,
                "toX": 477.0,
                "toY": 431.0,
                "duration": 320,
                "steps": 20,
                "_observation_token": "fresh",
                "_intent": "navigate left to next Pokemon detail",
                "_expected_after": "DETAIL for a different Pokemon",
            },
        )

    def test_right_swipe_uses_calibrated_left_to_right_digitizer_mapping(self) -> None:
        from pogo_iphone_renamer import ipad_landscape_agent as base

        proxy = SimpleNamespace(
            observation=SimpleNamespace(width=1366, height=1024, token="fresh"),
            call_tool=Mock(),
        )
        with (
            patch.object(base, "ORIENTATION", "STAGE_MANAGER_PORTRAIT_WINDOW"),
            patch.object(base, "_PORTRAIT_WINDOW_INPUT_MAPPING", "digitizer_normalized"),
        ):
            _swipe_next_once(proxy, direction="right")
        tool, arguments = proxy.call_tool.call_args.args
        self.assertEqual(tool, "swipe_screen")
        self.assertAlmostEqual(arguments["fromX"], 943.3596)
        self.assertAlmostEqual(arguments["toX"], arguments["fromX"])
        self.assertAlmostEqual(arguments["fromY"], 358.31808)
        self.assertAlmostEqual(arguments["toY"], 665.68192)
        self.assertEqual(arguments["_intent"], "navigate right to next Pokemon detail")

    def test_custom_name_change_proves_next_identity(self) -> None:
        before = DetailFingerprint(("鯉魚王", "3", "7"), "cp111", "54/54hp", "12.73kg", "1.07m")
        after = DetailFingerprint(("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m")
        self.assertTrue(fingerprints_differ(before, after))

    def test_same_species_can_be_proved_by_cp_change(self) -> None:
        before = DetailFingerprint(("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m")
        after = DetailFingerprint(("皮卡丘",), "cp501", "60/60hp", "6.0kg", "0.4m")
        self.assertTrue(fingerprints_differ(before, after))

    def test_identical_stable_fields_do_not_prove_change(self) -> None:
        value = DetailFingerprint(("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m")
        self.assertFalse(fingerprints_differ(value, value))

    def test_navigation_key_ignores_title_ocr_variants(self) -> None:
        full_title = DetailFingerprint(
            ("種子鐵球",), "cp11", "12/12hp", "22.07kg", "0.6m"
        )
        truncated_title = DetailFingerprint(
            ("種子鐵",), "cp11", "12/12hp", "22.07kg", "0.6m"
        )

        self.assertEqual(
            navigation_confirmation_key(full_title),
            navigation_confirmation_key(truncated_title),
        )

    def test_four_verified_unchanged_swipes_prove_storage_end(self) -> None:
        fingerprint = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        detail = Snapshot("CP500 60/60HP 6.0kg 0.4m", "detail")
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26._stable_baseline",
            return_value=(detail, fingerprint),
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._swipe_next_once"
        ) as swipe, patch(
            "pogo_iphone_renamer.batch_navigation_v26._observe_after_swipe",
            return_value=(detail, fingerprint, False, ()),
        ):
            with self.assertRaises(VerifiedEndOfStorage):
                swipe_to_verified_next(
                    object(), detail, before=fingerprint
                )

        self.assertEqual(swipe.call_count, 4)

    def test_swallowed_first_swipe_retries_from_verified_same_detail(self) -> None:
        before = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        after = DetailFingerprint(
            ("伊布",), "cp501", "61/61hp", "6.5kg", "0.5m"
        )
        first = Snapshot("same detail", "same")
        second = Snapshot("next detail", "next")
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26._stable_baseline",
            return_value=(first, before),
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._swipe_next_once"
        ) as swipe, patch(
            "pogo_iphone_renamer.batch_navigation_v26._observe_after_swipe",
            side_effect=[
                (first, before, False, ()),
                (second, after, True, (second, second, second)),
            ],
        ):
            next_detail = swipe_to_verified_next(
                object(), first, before=before
            )

        self.assertIs(next_detail.snapshot, second)
        self.assertEqual(next_detail.fingerprint, after)
        self.assertEqual(next_detail.samples, (second, second, second))
        self.assertEqual(swipe.call_count, 2)

    def test_verified_short_nickname_skips_name_dependent_baseline(self) -> None:
        before = DetailFingerprint(
            (), "cp415", "96/96hp", "11.02kg", "0.57m"
        )
        after = DetailFingerprint(
            ("迷你芙",), "cp416", "97/97hp", "11.03kg", "0.57m"
        )
        detail = Snapshot("verified short nickname", "detail")
        next_detail = Snapshot("next detail", "next")
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26._stable_baseline",
            side_effect=AssertionError("short nickname must not enter baseline OCR wait"),
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._swipe_next_once"
        ) as swipe, patch(
            "pogo_iphone_renamer.batch_navigation_v26._observe_after_swipe",
            return_value=(next_detail, after, True, (next_detail,) * 3),
        ), patch("pogo_iphone_renamer.batch_navigation_v26.base.emit") as emit:
            returned = swipe_to_verified_next(object(), detail, before=before)

        self.assertIs(returned.snapshot, next_detail)
        swipe.assert_called_once()
        self.assertGreaterEqual(emit.call_count, 2)
        self.assertIn("当前详情", emit.call_args_list[0].kwargs["message"])

    def test_preverified_named_detail_skips_redundant_baseline_ocr_wait(self) -> None:
        before = DetailFingerprint(
            ("齒輪兒", "2", "0", "6"), "cp24", "20/20hp", "18.24kg", "0.25m"
        )
        after = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        detail = Snapshot("already three-frame verified detail", "detail")
        next_detail = Snapshot("next detail", "next")
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26._stable_baseline",
            side_effect=AssertionError("preverified detail must not wait for duplicate OCR"),
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._swipe_next_once"
        ) as swipe, patch(
            "pogo_iphone_renamer.batch_navigation_v26._observe_after_swipe",
            return_value=(next_detail, after, True, (next_detail,) * 3),
        ), patch("pogo_iphone_renamer.batch_navigation_v26.base.emit"):
            returned = swipe_to_verified_next(object(), detail, before=before)

        self.assertIs(returned.snapshot, next_detail)
        swipe.assert_called_once()

    def test_never_blindly_retries_when_no_detail_can_be_verified(self) -> None:
        before = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        detail = Snapshot("detail", "image")
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26._stable_baseline",
            return_value=(detail, before),
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._swipe_next_once"
        ) as swipe, patch(
            "pogo_iphone_renamer.batch_navigation_v26._observe_after_swipe",
            return_value=None,
        ):
            with self.assertRaises(NoNextPokemon):
                swipe_to_verified_next(object(), detail, before=before)

        self.assertEqual(swipe.call_count, 1)

    def test_persistent_mode_waits_read_only_for_post_swipe_identity(self) -> None:
        before = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        after = DetailFingerprint(
            ("伊布",), "cp501", "61/61hp", "6.5kg", "0.5m"
        )
        detail = Snapshot("detail", "detail")
        next_detail = Snapshot("next", "next")
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26._stable_baseline",
            return_value=(detail, before),
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._swipe_next_once"
        ) as swipe, patch(
            "pogo_iphone_renamer.batch_navigation_v26._observe_after_swipe",
            return_value=None,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._wait_for_post_swipe_identity",
            return_value=(next_detail, after, True, (next_detail,) * 3),
        ) as wait:
            returned = swipe_to_verified_next(object(), detail, before=before)

        self.assertIs(returned.snapshot, next_detail)
        self.assertEqual(returned.fingerprint, after)
        swipe.assert_called_once()
        wait.assert_called_once()

    def test_persistent_post_swipe_wait_recovers_after_ocr_gap(self) -> None:
        before = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        unreadable = Snapshot("", "unreadable")
        recovered = Snapshot("same detail", "recovered")
        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.base._next_snapshot",
            side_effect=[unreadable, recovered],
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.batch_navigation_v26.detail_fingerprint",
            side_effect=[PolicyViolation("OCR 暂不可读"), before],
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._snapshot_digest",
            side_effect=["first", "second"],
        ), patch("pogo_iphone_renamer.batch_navigation_v26.base.emit") as emit:
            observed = _wait_for_post_swipe_identity(object(), before)

        self.assertIsNotNone(observed)
        snapshot, fingerprint, changed, samples = observed
        self.assertIs(snapshot, recovered)
        self.assertEqual(fingerprint, before)
        self.assertFalse(changed)
        self.assertEqual(samples, ())
        self.assertEqual(next_snapshot.call_count, 2)
        self.assertGreaterEqual(emit.call_count, 2)
        self.assertTrue(
            any(call.args and call.args[0] == "waiting" for call in emit.call_args_list)
        )

    def test_persistent_detail_fingerprint_wait_recovers_after_ocr_gap(self) -> None:
        fingerprint = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        incomplete = Snapshot("name only", "incomplete")
        recovered = Snapshot("complete detail", "recovered")
        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.detail_fingerprint",
            side_effect=[
                PolicyViolation("详情页稳定身份字段不足；不会自动翻页"),
                fingerprint,
            ],
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.base._next_snapshot",
            return_value=recovered,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.batch_navigation_v26.base.emit"
        ) as emit:
            snapshot, returned = wait_for_stable_detail_fingerprint(
                object(), incomplete
            )

        self.assertIs(snapshot, recovered)
        self.assertEqual(returned, fingerprint)
        next_snapshot.assert_called_once_with(unittest.mock.ANY, 3.0)
        self.assertGreaterEqual(emit.call_count, 2)
        self.assertTrue(
            any(call.args and call.args[0] == "waiting" for call in emit.call_args_list)
        )

    def test_verified_detail_uses_name_free_navigation_fallback(self) -> None:
        fallback = DetailFingerprint(
            (), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        short_nickname_detail = Snapshot("", "short-nickname-detail")
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.detail_fingerprint",
            side_effect=PolicyViolation("详情页稳定身份字段不足；不会自动翻页"),
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.base._next_snapshot"
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.batch_navigation_v26.base.emit"
        ) as emit:
            snapshot, returned = wait_for_stable_detail_fingerprint(
                object(),
                short_nickname_detail,
                verified_navigation_fallback=fallback,
            )

        self.assertIs(snapshot, short_nickname_detail)
        self.assertEqual(returned, fallback)
        next_snapshot.assert_not_called()
        emit.assert_called_once()

    def test_persistent_baseline_waits_for_a_second_matching_identity(self) -> None:
        initial = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        intermittent = [
            DetailFingerprint(("皮卡丘",), f"cp{value}", "60/60hp", "", "0.4m")
            for value in range(501, 505)
        ]
        detail = Snapshot("initial", "initial")
        samples = [Snapshot("", f"frame-{index}") for index in range(5)]
        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.base._next_snapshot",
            side_effect=samples,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.batch_navigation_v26.detail_fingerprint",
            side_effect=[*intermittent, initial],
        ), patch("pogo_iphone_renamer.batch_navigation_v26.base.emit") as emit:
            returned, fingerprint = _stable_baseline(
                object(), detail, initial
            )

        self.assertIs(returned, samples[-1])
        self.assertEqual(fingerprint, initial)
        self.assertEqual(next_snapshot.call_count, 5)
        self.assertGreaterEqual(emit.call_count, 2)
        self.assertTrue(
            any(call.args and call.args[0] == "waiting" for call in emit.call_args_list)
        )

    def test_next_never_probes_opposite_direction_from_identity_change(self) -> None:
        before = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        after = DetailFingerprint(
            ("伊布",), "cp501", "61/61hp", "6.5kg", "0.5m"
        )
        same = Snapshot("same", "same")
        changed = Snapshot("changed", "changed")
        # A stale pre-fix value must not reverse navigation. Identity changes
        # on both sides, so it is not directional evidence.
        proxy = SimpleNamespace(_batch_swipe_direction="right")
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26._stable_baseline",
            return_value=(same, before),
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._swipe_next_once"
        ) as swipe, patch(
            "pogo_iphone_renamer.batch_navigation_v26._observe_after_swipe",
            side_effect=[
                (same, before, False, ()),
                (same, before, False, ()),
                (changed, after, True, (changed, changed, changed)),
            ],
        ):
            next_detail = swipe_to_verified_next(
                proxy, same, before=before
            )

        self.assertIs(next_detail.snapshot, changed)
        self.assertEqual(next_detail.fingerprint, after)
        self.assertEqual(
            [call.kwargs["direction"] for call in swipe.call_args_list],
            ["left", "left", "left"],
        )
        self.assertEqual(proxy._batch_swipe_direction, "left")

    def test_one_transition_ocr_difference_does_not_prove_next_identity(self) -> None:
        before = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        false_change = DetailFingerprint(
            ("皮卡丘",), "cpS00", "60/60hp", "6.0kg", "0.4m"
        )
        snapshots = [Snapshot("", f"frame-{index}") for index in range(8)]
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.base._next_snapshot",
            side_effect=snapshots,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.detail_fingerprint",
            side_effect=[false_change] + [before] * 7,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._snapshot_digest",
            side_effect=[f"hash-{index}" for index in range(8)],
        ):
            observed = _observe_after_swipe(object(), before)

        self.assertIsNotNone(observed)
        _snapshot, fingerprint, changed, samples = observed
        self.assertFalse(changed)
        self.assertEqual(fingerprint, before)
        self.assertEqual(samples, ())

    def test_three_matching_changed_fingerprints_prove_next_identity(self) -> None:
        before = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        after = DetailFingerprint(
            ("伊布",), "cp501", "61/61hp", "6.5kg", "0.5m"
        )
        snapshots = [Snapshot("", f"frame-{index}") for index in range(3)]
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.base._next_snapshot",
            side_effect=snapshots,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.detail_fingerprint",
            side_effect=[after, after, after],
        ) as fingerprint_reader, patch(
            "pogo_iphone_renamer.batch_navigation_v26._snapshot_digest",
            side_effect=["hash-1", "hash-2", "hash-3"],
        ):
            observed = _observe_after_swipe(object(), before)

        self.assertIsNotNone(observed)
        snapshot, observed_fingerprint, changed, samples = observed
        self.assertTrue(changed)
        self.assertIs(snapshot, snapshots[-1])
        self.assertEqual(observed_fingerprint, after)
        self.assertEqual(samples, tuple(snapshots))
        self.assertEqual(
            [call.kwargs for call in fingerprint_reader.call_args_list],
            [{"require_name": False}] * 3,
        )

    def test_three_title_variants_with_same_numeric_detail_prove_one_next_identity(self) -> None:
        before = DetailFingerprint(
            ("種子鐵球",), "cp262", "21/21hp", "18.8kg", "0.6m"
        )
        variants = [
            DetailFingerprint(("種子鐵球",), "cp11", "12/12hp", "22.07kg", "0.6m"),
            DetailFingerprint(("種子鐵",), "cp11", "12/12hp", "22.07kg", "0.6m"),
            DetailFingerprint((), "cp11", "12/12hp", "22.07kg", "0.6m"),
        ]
        snapshots = [Snapshot("", f"frame-{index}") for index in range(3)]
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.base._next_snapshot",
            side_effect=snapshots,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.detail_fingerprint",
            side_effect=variants,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._snapshot_digest",
            side_effect=["hash-1", "hash-2", "hash-3"],
        ):
            observed = _observe_after_swipe(object(), before)

        self.assertIsNotNone(observed)
        _snapshot, fingerprint, changed, samples = observed
        self.assertTrue(changed)
        self.assertEqual(fingerprint, variants[-1])
        self.assertEqual(samples, tuple(snapshots))

    def test_replayed_post_swipe_frame_cannot_count_as_three_new_identities(self) -> None:
        before = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        after = DetailFingerprint(
            ("伊布",), "cp501", "61/61hp", "6.5kg", "0.5m"
        )
        cached = Snapshot("", "cached")
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.base._next_snapshot",
            return_value=cached,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.detail_fingerprint",
            return_value=after,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._snapshot_digest",
            return_value="same-post-swipe-hash",
        ):
            observed = _observe_after_swipe(object(), before)

        self.assertIsNone(observed)

    def test_cached_pre_swipe_pixels_cannot_authorize_another_swipe(self) -> None:
        before = DetailFingerprint(
            ("黏黏寶",), "cp846", "99/99hp", "39.96kg", "0.88m"
        )
        cached = Snapshot("", "cached-frame")
        proxy = SimpleNamespace(_pogo_verified_frame_history=["old-hash"])
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.base._next_snapshot",
            return_value=cached,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._snapshot_digest",
            return_value="old-hash",
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.detail_fingerprint"
        ) as fingerprint:
            observed = _observe_after_swipe(proxy, before)

        self.assertIsNone(observed)
        fingerprint.assert_not_called()

    def test_post_swipe_inventory_is_reported_for_automatic_recovery(self) -> None:
        before = DetailFingerprint(
            ("種子鐵球",), "cp11", "12/12hp", "22.07kg", "0.6m"
        )
        inventory = Snapshot("9751 / 10225", "inventory")
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.base._next_snapshot",
            return_value=inventory,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._snapshot_digest",
            return_value="fresh-inventory-frame",
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.base.local_page_state",
            return_value="INVENTORY",
        ):
            with self.assertRaises(DetailExitedToOverview) as raised:
                _observe_after_swipe(object(), before)

        self.assertIs(raised.exception.snapshot, inventory)
        self.assertEqual(raised.exception.state, "INVENTORY")

    def test_stale_inventory_accessibility_cannot_override_detail_pixels(self) -> None:
        detail = Snapshot("stale inventory labels", "fresh-detail-pixels")
        with patch(
            "pogo_iphone_renamer.batch_navigation_v26.base.local_page_state",
            return_value="INVENTORY",
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._looks_like_detail_visual",
            return_value=True,
        ):
            self.assertIsNone(_overview_state(detail))

    def test_visual_detail_fallback_never_authorizes_name_but_unblocks_navigation(self) -> None:
        before = DetailFingerprint(
            ("種子鐵球",), "cp11", "12/12hp", "22.07kg", "0.6m"
        )
        samples = [Snapshot("", f"detail-{index}") for index in range(3)]
        with patch.dict(
            "os.environ", {"POGO_PERSIST_CAPTURE_WAIT": "true"}, clear=False
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.base._next_snapshot",
            side_effect=samples,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._snapshot_digest",
            side_effect=["fresh-1", "fresh-2", "fresh-3"],
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.base.local_page_state",
            return_value="DETAIL",
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._looks_like_detail_visual",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26.detail_fingerprint",
            side_effect=PolicyViolation("详情页稳定身份字段不足"),
        ):
            observed = _wait_for_post_swipe_identity(object(), before)

        self.assertIsNotNone(observed)
        snapshot, fingerprint, changed, accepted = observed
        self.assertIs(snapshot, samples[-1])
        self.assertEqual(fingerprint, DetailFingerprint((), "", "", "", ""))
        self.assertTrue(changed)
        self.assertEqual(accepted, tuple(samples))


if __name__ == "__main__":
    unittest.main()
