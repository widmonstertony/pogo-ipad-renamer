from __future__ import annotations

import unittest
import base64
import io
from types import SimpleNamespace
from unittest.mock import patch

from PIL import Image, ImageDraw

from pogo_iphone_renamer import ipad_landscape_agent as base
from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.landscape_cv import (
    StageManagerGeometry,
    set_preferred_stage_manager_geometry,
    stage_manager_geometry,
)


class StageManagerMappingTests(unittest.TestCase):
    def tearDown(self) -> None:
        set_preferred_stage_manager_geometry(None)

    def test_map_anchor_matches_verified_maximized_window(self) -> None:
        x_ratio, y_ratio, _label, _expected = base.ANCHORS["MAP"]
        x, y = base.upright_ratio_to_touch(
            1366, 1024, x_ratio, y_ratio
        )
        self.assertAlmostEqual(x, 157, delta=4)
        self.assertAlmostEqual(y, 513, delta=4)

    def test_canonical_center_maps_to_game_window_center(self) -> None:
        x, y = base.upright_ratio_to_touch(1366, 1024, 0.5, 0.5)
        self.assertAlmostEqual(x, 684, delta=2)
        self.assertAlmostEqual(y, 512, delta=2)

    def test_dynamic_window_maps_current_map_anchor(self) -> None:
        geometry = StageManagerGeometry(
            raw_width=1024,
            raw_height=1366,
            left=272,
            top=198,
            right=750,
            bottom=1334,
        )
        x_ratio, y_ratio, _label, _expected = base.ANCHORS["MAP"]
        x, y = base.upright_ratio_to_touch(
            1366,
            1024,
            x_ratio,
            y_ratio,
            geometry=geometry,
        )
        self.assertAlmostEqual(x, 306, delta=3)
        self.assertAlmostEqual(y, 511, delta=3)

    def test_detects_moved_stage_manager_window_below_dock(self) -> None:
        image = Image.new("RGB", (1024, 1366), (32, 78, 112))
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 28, 1004, 155), fill=(225, 225, 225))
        draw.rectangle((272, 198, 750, 1334), fill=(85, 180, 112))
        draw.line((272, 198, 272, 1334), fill=(250, 250, 250), width=3)
        draw.line((750, 198, 750, 1334), fill=(5, 5, 5), width=3)
        geometry = stage_manager_geometry(image, use_preferred=False)
        self.assertAlmostEqual(geometry.left, 272, delta=4)
        self.assertAlmostEqual(geometry.top, 198, delta=5)
        self.assertAlmostEqual(geometry.right, 750, delta=4)
        self.assertAlmostEqual(geometry.bottom, 1334, delta=5)

        output = io.BytesIO()
        image.save(output, format="PNG")
        snapshot = Snapshot(
            text="",
            image=base64.b64encode(output.getvalue()).decode("ascii"),
        )
        proxy = SimpleNamespace()
        base._remember_stage_geometry(proxy, snapshot)
        self.assertIsInstance(
            getattr(proxy, "_stage_manager_geometry", None), StageManagerGeometry
        )

    def test_wide_window_does_not_mistake_dock_for_top_edge(self) -> None:
        image = Image.new("RGB", (1024, 1366), (32, 78, 112))
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 28, 1004, 155), fill=(225, 225, 225))
        # Matches the geometry of the real map frame that previously produced
        # top=140 instead of the actual top=198.
        draw.rectangle((106, 198, 592, 1334), fill=(85, 180, 112))
        draw.line((106, 198, 106, 1334), fill=(250, 250, 250), width=3)
        draw.line((592, 198, 592, 1334), fill=(5, 5, 5), width=3)
        geometry = stage_manager_geometry(image, use_preferred=False)
        self.assertAlmostEqual(geometry.left, 106, delta=4)
        self.assertAlmostEqual(geometry.top, 198, delta=5)
        self.assertAlmostEqual(geometry.right, 592, delta=4)
        self.assertAlmostEqual(geometry.bottom, 1334, delta=5)

    def test_detail_inner_card_does_not_replace_the_real_window_frame(self) -> None:
        image = Image.new("RGB", (1024, 1366), (32, 78, 112))
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 28, 1004, 155), fill=(225, 225, 225))
        draw.rectangle((106, 190, 594, 1334), fill=(75, 105, 135))
        draw.line((106, 190, 106, 1334), fill=(235, 235, 235), width=2)
        draw.line((594, 190, 594, 1334), fill=(15, 15, 15), width=2)
        # Reproduce the stronger detail-card edges that previously won the
        # peak ranking and produced (116, 248)-(578, 1334).
        draw.rectangle((116, 248, 578, 1334), fill=(246, 246, 246))
        draw.line((116, 248, 116, 1334), fill=(255, 255, 255), width=3)
        draw.line((578, 248, 578, 1334), fill=(0, 0, 0), width=3)
        geometry = stage_manager_geometry(image, use_preferred=False)
        self.assertAlmostEqual(geometry.left, 106, delta=5)
        self.assertAlmostEqual(geometry.top, 190, delta=6)
        self.assertAlmostEqual(geometry.right, 594, delta=5)
        self.assertAlmostEqual(geometry.bottom, 1334, delta=5)

    def test_current_pale_green_menu_is_not_mislabeled_as_map(self) -> None:
        image = Image.new("RGB", (1024, 1366), (20, 45, 70))
        draw = ImageDraw.Draw(image)
        draw.rectangle((20, 28, 1004, 155), fill=(215, 215, 215))
        draw.rectangle((106, 198, 592, 1334), fill=(230, 248, 225))
        draw.line((106, 198, 106, 1334), fill=(250, 250, 250), width=3)
        draw.line((592, 198, 592, 1334), fill=(5, 5, 5), width=3)
        output = io.BytesIO()
        image.save(output, format="PNG")
        snapshot = Snapshot(
            text="",
            image=base64.b64encode(output.getvalue()).decode("ascii"),
        )
        self.assertEqual(base.local_page_state(snapshot), "MAIN_MENU")

    def test_locked_geometry_does_not_jump_to_adjacent_stage_window(self) -> None:
        locked = StageManagerGeometry(1024, 1366, 272, 198, 750, 1334)
        set_preferred_stage_manager_geometry(locked)
        distractor = Image.new("RGB", (1024, 1366), (20, 20, 20))
        draw = ImageDraw.Draw(distractor)
        draw.rectangle((370, 28, 914, 1334), fill=(230, 230, 230))
        self.assertEqual(stage_manager_geometry(distractor), locked)

    def test_detail_validation_uses_canonical_game_ocr_not_desktop_text(self) -> None:
        snapshot = Snapshot(text="Stage Manager desktop", image="game-screenshot")
        lines = [
            SimpleNamespace(text="61/61HP", confidence=1.0),
            SimpleNamespace(text="4.76kg", confidence=1.0),
        ]
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent.measure_ipad14_6_appraisal",
            side_effect=ValueError("no bars"),
        ), patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=lines,
        ):
            base._validate_expected("DETAIL", snapshot)


if __name__ == "__main__":
    unittest.main()
