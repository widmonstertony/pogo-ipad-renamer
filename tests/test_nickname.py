from __future__ import annotations

import unittest

from pogo_iphone_renamer.nickname import generate_iv_nickname, iv_percent


class NicknameGeneratorTests(unittest.TestCase):
    def test_regression_examples(self) -> None:
        cases = [
            ((15, 3, 4), "偷兒狐⓯❸❹⁴⁹"),
            ((1, 12, 5), "偷兒狐❶⓬❺⁴⁰"),
            ((13, 12, 9), "偷兒狐⓭⓬❾⁷⁶"),
            ((12, 12, 15), "偷兒狐⓬⓬⓯⁸⁷"),
            ((10, 2, 13), "偷兒狐❿❷⓭⁵⁶"),
        ]
        for values, expected in cases:
            with self.subTest(values=values):
                self.assertEqual(generate_iv_nickname("偷兒狐", *values), expected)

    def test_all_4096_iv_combinations(self) -> None:
        for attack in range(16):
            for defense in range(16):
                for stamina in range(16):
                    nickname = generate_iv_nickname("測試", attack, defense, stamina)
                    self.assertTrue(nickname.startswith("測試"))
                    self.assertGreaterEqual(iv_percent(attack, defense, stamina), 0)
                    self.assertLessEqual(iv_percent(attack, defense, stamina), 100)

    def test_legacy_suffix_is_explicit_only(self) -> None:
        plain = generate_iv_nickname("火恐龍", 15, 13, 9)
        legacy = generate_iv_nickname("火恐龍", 15, 13, 9, legacy_move=True)
        self.assertEqual(plain, "火恐龍⓯⓭❾⁸²")
        self.assertEqual(legacy, "火恐⓯⓭❾⁸²(+)")
        self.assertFalse(plain.endswith("(+)"))
        self.assertTrue(legacy.endswith("(+)"))

    def test_four_cjk_characters_are_trimmed_to_the_24_byte_limit(self) -> None:
        nickname = generate_iv_nickname("瑪瑙水母", 14, 3, 10)
        self.assertEqual(nickname, "瑪瑙水⓮❸❿⁶⁰")
        self.assertLessEqual(len(nickname.encode("utf-8")), 24)
        self.assertLessEqual(len(nickname), 12)

    def test_iv_100_reserves_three_superscript_digits(self) -> None:
        nickname = generate_iv_nickname("妙蛙種子", 15, 15, 15)
        self.assertEqual(nickname, "妙蛙⓯⓯⓯¹⁰⁰")
        self.assertLessEqual(len(nickname.encode("utf-8")), 24)

    def test_every_iv_combination_stays_within_both_game_limits(self) -> None:
        for attack in range(16):
            for defense in range(16):
                for stamina in range(16):
                    nickname = generate_iv_nickname(
                        "伽勒爾火紅不倒翁", attack, defense, stamina
                    )
                    self.assertLessEqual(len(nickname.encode("utf-8")), 24)
                    self.assertLessEqual(len(nickname), 12)

    def test_two_through_five_character_species_use_dynamic_prefixes(self) -> None:
        cases = [
            ("皮丘", "皮丘⓮❸❿⁶⁰"),
            ("鯉魚王", "鯉魚王⓮❸❿⁶⁰"),
            ("瑪瑙水母", "瑪瑙水⓮❸❿⁶⁰"),
            ("帕底亞烏波", "帕底亞⓮❸❿⁶⁰"),
        ]
        for species, expected in cases:
            with self.subTest(species=species):
                nickname = generate_iv_nickname(species, 14, 3, 10)
                self.assertEqual(nickname, expected)
                self.assertLessEqual(len(nickname.encode("utf-8")), 24)
                self.assertLessEqual(len(nickname), 12)


if __name__ == "__main__":
    unittest.main()
