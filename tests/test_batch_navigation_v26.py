from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.policy import PolicyViolation
from pogo_iphone_renamer.batch_navigation_v26 import (
    DetailFingerprint,
    NoNextPokemon,
    VerifiedEndOfStorage,
    _observe_after_swipe,
    _stable_baseline,
    _swipe_next_once,
    _wait_for_post_swipe_identity,
    detail_fingerprint,
    fingerprints_differ,
    swipe_to_verified_next,
    wait_for_stable_detail_fingerprint,
)


class DetailFingerprintTests(unittest.TestCase):
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
        ):
            _swipe_next_once(proxy)

        proxy.call_tool.assert_called_once_with(
            "swipe_screen",
            {
                "fromX": 1065,
                "fromY": 512,
                "toX": 301,
                "toY": 512,
                "_observation_token": "fresh-observation",
                "_intent": "navigate left to next Pokemon detail",
                "_expected_after": "DETAIL for a different Pokemon",
            },
        )

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
        emit.assert_called_once()

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
        emit.assert_called_once()

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
        emit.assert_called_once()

    def test_verified_rename_uses_pre_rename_immutable_fallback(self) -> None:
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
                verified_rename_fallback=fallback,
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
        emit.assert_called_once()

    def test_unknown_sort_order_probes_opposite_direction_and_remembers_it(self) -> None:
        before = DetailFingerprint(
            ("皮卡丘",), "cp500", "60/60hp", "6.0kg", "0.4m"
        )
        after = DetailFingerprint(
            ("伊布",), "cp501", "61/61hp", "6.5kg", "0.5m"
        )
        same = Snapshot("same", "same")
        changed = Snapshot("changed", "changed")
        proxy = SimpleNamespace()
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
            ["left", "left", "right"],
        )
        self.assertEqual(proxy._batch_swipe_direction, "right")

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
        ), patch(
            "pogo_iphone_renamer.batch_navigation_v26._snapshot_digest",
            side_effect=["hash-1", "hash-2", "hash-3"],
        ):
            observed = _observe_after_swipe(object(), before)

        self.assertIsNotNone(observed)
        snapshot, fingerprint, changed, samples = observed
        self.assertTrue(changed)
        self.assertIs(snapshot, snapshots[-1])
        self.assertEqual(fingerprint, after)
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


if __name__ == "__main__":
    unittest.main()
