from __future__ import annotations

import unittest

from pogo_iphone_renamer.species_db import exact_default_species_name, traditional_chinese_species


class SpeciesDatabaseTests(unittest.TestCase):
    def test_database_is_complete_enough_and_traditional(self) -> None:
        names = traditional_chinese_species()
        self.assertGreaterEqual(len(names), 1000)
        self.assertIn("輕飄飄", names)
        self.assertIn("偷兒狐", names)
        self.assertNotIn("轻飘飘", names)

    def test_exact_match_only(self) -> None:
        self.assertEqual(exact_default_species_name("輕飄飄"), "輕飄飄")
        self.assertIsNone(exact_default_species_name("輕飄飄⓯⓮⓮⁹⁶"))


if __name__ == "__main__":
    unittest.main()
