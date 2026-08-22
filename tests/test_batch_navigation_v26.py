from __future__ import annotations

import unittest
from unittest.mock import patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.batch_navigation_v26 import (
    DetailFingerprint,
    NoNextPokemon,
    VerifiedEndOfStorage,
    fingerprints_differ,
    swipe_to_verified_next,
)


class DetailFingerprintTests(unittest.TestCase):
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
            "pogo_iphone_renamer.batch_navigation_v26._swipe_next_once"
        ) as swipe, patch(
            "pogo_iphone_renamer.batch_navigation_v26._observe_after_swipe",
            return_value=None,
        ):
            with self.assertRaises(NoNextPokemon):
                swipe_to_verified_next(object(), detail, before=before)

        self.assertEqual(swipe.call_count, 1)


if __name__ == "__main__":
    unittest.main()
