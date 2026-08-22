from __future__ import annotations

import unittest

from pogo_iphone_renamer.policy import (
    Observation,
    PolicyViolation,
    arguments_are_dangerous,
    screen_is_dangerous,
    validate_bounds,
    validate_poke_genie_name,
)


class NamePolicyTests(unittest.TestCase):
    def test_regression_examples_are_accepted(self) -> None:
        cases = [
            ("火恐⓯⓭❾⁸²(+)", "火恐龍"),
            ("妙蛙種子⁷⁶", "妙蛙種子"),
            ("偷兒狐⓯❸❹⁴⁹", "偷兒狐"),
            ("偷兒狐❶⓬❺⁴⁰", "偷兒狐"),
            ("偷兒狐⓭⓬❾⁷⁶", "偷兒狐"),
            ("偷兒狐⓬⓬⓯⁸⁷", "偷兒狐"),
            ("偷兒狐❿❷⓭⁵⁶", "偷兒狐"),
        ]
        for nickname, species in cases:
            with self.subTest(nickname=nickname):
                validate_poke_genie_name(nickname, species)

    def test_plain_name_is_rejected(self) -> None:
        with self.assertRaises(PolicyViolation):
            validate_poke_genie_name("偷兒狐", "偷兒狐")

    def test_wrong_species_prefix_is_rejected(self) -> None:
        with self.assertRaises(PolicyViolation):
            validate_poke_genie_name("皮卡丘⓯⓯⓯¹⁰⁰", "偷兒狐")

    def test_malformed_legacy_marker_is_rejected(self) -> None:
        with self.assertRaises(PolicyViolation):
            validate_poke_genie_name("偷兒狐⓯⓯⓯¹⁰⁰(+x)", "偷兒狐")


class SafetyPolicyTests(unittest.TestCase):
    def test_transfer_confirmation_is_dangerous(self) -> None:
        self.assertTrue(screen_is_dangerous("確認傳送這隻寶可夢？"))

    def test_normal_detail_is_not_dangerous(self) -> None:
        self.assertFalse(screen_is_dangerous("寶可夢 詳細資訊 CP 123"))

    def test_dangerous_element_argument_is_rejected(self) -> None:
        self.assertTrue(arguments_are_dangerous({"text": "傳送"}))

    def test_coordinate_bounds(self) -> None:
        observation = Observation("t", 0, "", 390, 844)
        validate_bounds("tap_screen", {"x": 10, "y": 20}, observation)
        with self.assertRaises(PolicyViolation):
            validate_bounds("tap_screen", {"x": 390, "y": 20}, observation)


if __name__ == "__main__":
    unittest.main()

