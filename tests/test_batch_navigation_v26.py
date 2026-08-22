from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.batch_navigation_v26 import (
    DetailFingerprint,
    NoNextPokemon,
    VerifiedEndOfStorage,
    _observe_after_swipe,
    _swipe_next_once,
    fingerprints_differ,
    swipe_to_verified_next,
)


class DetailFingerprintTests(unittest.TestCase):
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
            return_value=(detail, fingerprint, False),
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
            side_effect=[(first, before, False), (second, after, True)],
        ):
            snapshot, fingerprint = swipe_to_verified_next(
                object(), first, before=before
            )

        self.assertIs(snapshot, second)
        self.assertEqual(fingerprint, after)
        self.assertEqual(swipe.call_count, 2)

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
                (same, before, False),
                (same, before, False),
                (changed, after, True),
            ],
        ):
            returned, fingerprint = swipe_to_verified_next(
                proxy, same, before=before
            )

        self.assertIs(returned, changed)
        self.assertEqual(fingerprint, after)
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
        ):
            observed = _observe_after_swipe(object(), before)

        self.assertIsNotNone(observed)
        _snapshot, fingerprint, changed = observed
        self.assertFalse(changed)
        self.assertEqual(fingerprint, before)

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
        ):
            observed = _observe_after_swipe(object(), before)

        self.assertIsNotNone(observed)
        snapshot, fingerprint, changed = observed
        self.assertTrue(changed)
        self.assertIs(snapshot, snapshots[-1])
        self.assertEqual(fingerprint, after)

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
