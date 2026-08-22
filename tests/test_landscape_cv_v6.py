from __future__ import annotations

import unittest

from PIL import Image, ImageDraw

from pogo_iphone_renamer.landscape_cv_v6 import measure_upright_appraisal_v6


def synthetic_appraisal(
    values: tuple[int, int, int],
    *,
    bar_half_height: int = 5,
) -> Image.Image:
    width, height = 1024, 1366
    image = Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(image)
    start = 105
    full_endpoint = 366
    # Real iPad captures place the rounded gray track edge one pixel beyond
    # the divider-derived 15-IV fill endpoint.
    track_end = 367
    span = full_endpoint - start + 1
    rows = (1046, 1113, 1182)
    for row, value in zip(rows, values):
        draw.rounded_rectangle(
            (
                start,
                row - bar_half_height,
                track_end,
                row + bar_half_height,
            ),
            radius=bar_half_height,
            fill=(228, 228, 228),
        )
        if value:
            endpoint = start - 1 + round(span * value / 15)
            draw.rounded_rectangle(
                (
                    start,
                    row - bar_half_height,
                    endpoint,
                    row + bar_half_height,
                ),
                radius=bar_half_height,
                fill=(210, 130, 133) if value == 15 else (230, 169, 89),
            )
        for fraction in (1 / 3, 2 / 3):
            tick = round(start + span * fraction)
            draw.rectangle((tick - 2, row - 7, tick + 2, row + 7), fill="white")
    return image


class LandscapeCVV6Tests(unittest.TestCase):
    def test_endpoint_and_cell_decoders_agree_for_every_iv(self) -> None:
        for value in range(16):
            with self.subTest(value=value):
                result = measure_upright_appraisal_v6(
                    synthetic_appraisal((value, value, value))
                )
                self.assertEqual(
                    (result.attack, result.defense, result.stamina),
                    (value, value, value),
                )
                self.assertGreaterEqual(result.confidence, 0.80)

    def test_regression_does_not_shift_phone_sample_up_one(self) -> None:
        result = measure_upright_appraisal_v6(
            synthetic_appraisal((15, 14, 12))
        )
        self.assertEqual(
            (result.attack, result.defense, result.stamina),
            (15, 14, 12),
        )
        self.assertNotEqual(
            (result.attack, result.defense, result.stamina),
            (15, 15, 13),
        )

    def test_sparse_far_colour_cannot_fool_endpoint_decoder(self) -> None:
        image = synthetic_appraisal((5, 5, 5))
        draw = ImageDraw.Draw(image)
        start = 105
        unit = (366 - start + 1) / 15
        rogue_x = round(start + 9.5 * unit)
        for row in (1046, 1113, 1182):
            draw.line((rogue_x, row - 3, rogue_x, row + 3), fill=(230, 169, 89))
        with self.assertRaises(ValueError):
            measure_upright_appraisal_v6(image)

    def test_thin_realistic_tracks_do_not_dilute_filled_cells(self) -> None:
        result = measure_upright_appraisal_v6(
            synthetic_appraisal((10, 13, 14), bar_half_height=2)
        )
        self.assertEqual(
            (result.attack, result.defense, result.stamina),
            (10, 13, 14),
        )

    def test_one_noisy_horizontal_slice_cannot_fill_an_empty_cell(self) -> None:
        image = synthetic_appraisal((5, 5, 5))
        draw = ImageDraw.Draw(image)
        start = 105
        unit = (366 - start + 1) / 15
        rogue_left = round(start + 9.35 * unit)
        rogue_right = round(start + 9.65 * unit)
        for row in (1046, 1113, 1182):
            draw.line(
                (rogue_left, row, rogue_right, row),
                fill=(230, 169, 89),
            )
        result = measure_upright_appraisal_v6(image)
        self.assertEqual(
            (result.attack, result.defense, result.stamina),
            (5, 5, 5),
        )

    def test_zero_iv_track_may_merge_with_neutral_background_on_left(self) -> None:
        image = synthetic_appraisal((9, 12, 0))
        draw = ImageDraw.Draw(image)
        # Reproduce the real card gradient joining the empty gray stamina
        # track on its left.  Divider seams and the physical right edge remain
        # intact, so both value decoders must still return zero.
        draw.rectangle((65, 1179, 106, 1185), fill=(228, 228, 228))
        result = measure_upright_appraisal_v6(image)
        self.assertEqual(
            (result.attack, result.defense, result.stamina),
            (9, 12, 0),
        )


if __name__ == "__main__":
    unittest.main()
