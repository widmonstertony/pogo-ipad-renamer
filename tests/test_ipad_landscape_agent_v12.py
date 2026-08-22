from __future__ import annotations

import unittest

from pogo_iphone_renamer.ipad_landscape_agent_v12 import clear_key_count
from pogo_iphone_renamer.policy import PolicyViolation


class BackspaceClearTests(unittest.TestCase):
    def test_uses_exact_nfc_character_count(self) -> None:
        self.assertEqual(clear_key_count("輕飄飄"), 3)
        self.assertEqual(clear_key_count("  輕飄飄  "), 3)

    def test_empty_name_is_rejected(self) -> None:
        with self.assertRaises(PolicyViolation):
            clear_key_count("   ")


if __name__ == "__main__":
    unittest.main()
