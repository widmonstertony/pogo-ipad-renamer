import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pogo_iphone_renamer import batch_agent as batch
from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.name_recognition import NameRegionResult


class CustomSeedSpeedTests(unittest.TestCase):
    def check(self, results, capture_ids=(1, 2, 3), history=()):
        frames = tuple(Snapshot("", "image-" + str(i), capture_ids[i]) for i in range(3))
        proxy = SimpleNamespace(_pogo_verified_frame_history=list(history))
        with patch.object(batch.base, "_validate_expected"), patch.object(batch, "_snapshot_digest", side_effect=lambda s: s.image), patch.object(batch, "analyze_name_region", side_effect=[*results, results[-1]]), patch.object(batch.base, "_next_snapshot") as capture, patch.object(batch, "_DETAIL_IDENTITY_READ_ONLY_RETRIES", 1), patch.object(batch, "emit"):
            result = batch._confirm_fresh_detail_identity(proxy, frames[-1], seed_samples=frames)
            capture.assert_not_called()
            return result

    def test_three_new_consistent_custom_frames_avoid_a_second_capture_round(self):
        name = NameRegionResult("鐵蟻", False, .99, ("鐵蟻", "15", "14", "96"))
        self.assertIsNotNone(self.check([name] * 3))

    def test_inconsistent_annotations_never_use_the_fast_path(self):
        one = NameRegionResult("鐵蟻", False, .99, ("鐵蟻", "15", "14", "96"))
        two = NameRegionResult("鐵蟻", False, .99, ("鐵蟻", "15", "13", "96"))
        self.assertIsNone(self.check([one, two, one]))

    def test_replayed_capture_ids_do_not_become_three_independent_reads(self):
        name = NameRegionResult("鐵蟻", False, .99, ("Durant15",))
        self.assertIsNone(self.check([name] * 3, capture_ids=(1, 1, 1)))

    def test_previously_processed_frames_do_not_authorize_a_fast_skip(self):
        name = NameRegionResult("鐵蟻", False, .99, ("Durant15",))
        self.assertIsNone(self.check([name] * 3, history=("image-0", "image-1", "image-2")))
