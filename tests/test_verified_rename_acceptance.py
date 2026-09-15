import unittest

from pogo_iphone_renamer.background_batch_runner import _with_cumulative_verification


class VerifiedRenameAcceptanceTests(unittest.TestCase):
    def state(self, renamed=0, skipped=0, unreadable=0):
        return _with_cumulative_verification(
            dict(renamed=renamed, skipped=skipped, unreadable=unreadable, scanned=0),
            completed=dict(renamed=0, skipped=0, unreadable=0, scanned=0), target=50,
        )["verification"]

    def test_fifty_skips_are_not_fifty_successes(self):
        self.assertFalse(self.state(skipped=50)["passed"])
        self.assertEqual(self.state(skipped=50)["completed"], 0)
        self.assertEqual(self.state(skipped=50)["processed"], 50)

    def test_cancellations_do_not_complete_acceptance(self):
        self.assertFalse(self.state(renamed=49, skipped=30, unreadable=1)["passed"])

    def test_only_fifty_verified_renames_pass(self):
        self.assertTrue(self.state(renamed=50)["passed"])
        self.assertEqual(self.state(renamed=50)["basis"], "verified_renames")
