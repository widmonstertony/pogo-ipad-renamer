from __future__ import annotations

import base64
import io
import unittest
from unittest.mock import patch

from PIL import Image

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer import ipad_landscape_agent as base
from pogo_iphone_renamer.ipad_landscape_agent_v14 import (
    _inventory_text_evidence,
    _pokedex_detail_text_evidence,
    _pokedex_grid_text_evidence,
    _pokedex_index_text_evidence,
    _safe_same_page_retry,
    _target_reached,
    _transition,
    detail_record_overlay_visible,
    inventory_visible,
    navigate_to_appraisal_v14,
    perceptual_change,
    robust_page_state,
    snapshot_is_black,
    storage_capacity_visible,
)
from pogo_iphone_renamer.local_ocr import OCRLine


def _snapshot(color: tuple[int, int, int]) -> Snapshot:
    image = Image.new("RGB", (1024, 1366), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Snapshot(text="", image=base64.b64encode(buffer.getvalue()).decode("ascii"))


class ResilientLandscapeNavigationTests(unittest.TestCase):
    def test_rotated_hp_ocr_still_proves_plain_detail(self) -> None:
        snapshot = Snapshot(text="", image="current-detail")
        lines = tuple(
            OCRLine(text, 0.99)
            for text in ("CP846", "瑪瑙水母", "dH66/66", "38.96kg", "0.88m")
        )
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent.measure_ipad14_6_appraisal",
            side_effect=ValueError("plain detail has no appraisal bars"),
        ), patch(
            "pogo_iphone_renamer.local_ocr.ocr_mcp_screenshot",
            return_value=lines,
        ):
            self.assertEqual(base.local_page_state(snapshot), "DETAIL")

    def test_storage_capacity_accepts_thousands_separators(self) -> None:
        self.assertTrue(storage_capacity_visible("9,987 / 10,150"))
        self.assertTrue(storage_capacity_visible("9987 / 10150"))
        self.assertTrue(storage_capacity_visible("9 987 / 10 150"))

    def test_pokedex_detail_requires_seen_caught_and_entry_control(self) -> None:
        self.assertTrue(
            _pokedex_detail_text_evidence("#0152 菊草葉 有見過 3268 已捕捉 1110 關閉通知")
        )
        self.assertFalse(_pokedex_detail_text_evidence("有見過 3268 已捕捉 1110"))

    def test_storage_evidence_does_not_match_pokedex_counts(self) -> None:
        self.assertFalse(_inventory_text_evidence("有見過 3268 已捕捉 1110"))

    def test_pokedex_grid_does_not_match_inventory(self) -> None:
        text = "搜索 城都 100/100 異色 亮晶晶 XXL 0152 0153 0154"
        self.assertTrue(_pokedex_grid_text_evidence(text))
        self.assertFalse(_inventory_text_evidence(text))

    def test_pokedex_index_is_distinct_from_region_grid(self) -> None:
        text = "寶可夢圖鑑 已捕捉 847 關都 151/151 城都 100/100 豐緣 133/135"
        self.assertTrue(_pokedex_index_text_evidence(text))
        self.assertFalse(_pokedex_grid_text_evidence(text))

    def test_inventory_fallback_requires_all_storage_controls(self) -> None:
        self.assertTrue(_inventory_text_evidence("寶可夢 搜尋 標籤 蛋"))
        self.assertFalse(_inventory_text_evidence("寶可夢 搜尋"))

    def test_fresh_detail_pixels_override_stale_inventory_accessibility(self) -> None:
        snapshot = Snapshot(text="9987 / 10150", image="current-detail")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14.snapshot_is_black",
            return_value=False,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14._fresh_screenshot_text",
            return_value=("CP 100 20 / 20 HP 1.0 kg", True),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14.base.local_page_state",
            return_value="DETAIL",
        ):
            self.assertFalse(inventory_visible(snapshot))
            self.assertEqual(robust_page_state(snapshot), "DETAIL")

    def test_entry_navigation_closes_all_pokedex_layers_before_continuing(self) -> None:
        pokedex = Snapshot("有見過 已捕捉 關閉通知", "entry")
        grid = Snapshot("寶可夢圖鑑 搜尋", "grid")
        index = Snapshot("寶可夢圖鑑 關都 城都 豐緣", "index")
        detail = Snapshot("CP 100 20 / 20 HP 1 kg", "detail")
        measurement = object()
        proxy = object()
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14._wait_until_visible",
            return_value=pokedex,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14._close_detail_record_overlay_if_needed",
            return_value=(pokedex, False),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14.robust_page_state",
            side_effect=[
                "POKEDEX_DETAIL",
                "POKEDEX_REGION_GRID",
                "POKEDEX_INDEX",
                "DETAIL",
            ],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14.base._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14.base._ensure_stage_geometry_for_state",
            side_effect=lambda _proxy, snapshot, _state, **_kwargs: snapshot,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14.base._next_snapshot",
            side_effect=[grid, index, detail],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14._ORIGINAL_NAVIGATE",
            return_value=(detail, measurement),
        ):
            returned = navigate_to_appraisal_v14(proxy, pokedex)

        self.assertEqual(returned, (detail, measurement))
        self.assertEqual(tap.call_count, 3)
        tap.assert_has_calls(
            [
                unittest.mock.call(proxy, "POKEDEX_CLOSE"),
                unittest.mock.call(proxy, "POKEDEX_GRID_CLOSE"),
                unittest.mock.call(proxy, "POKEDEX_GRID_CLOSE"),
            ]
        )

    def test_hp_fraction_is_not_storage_capacity(self) -> None:
        self.assertFalse(storage_capacity_visible("95 / 95 HP"))

    def test_black_frame_is_not_a_map(self) -> None:
        self.assertTrue(snapshot_is_black(_snapshot((0, 0, 0))))
        self.assertFalse(snapshot_is_black(_snapshot((30, 60, 90))))

    def test_perceptual_change_distinguishes_same_and_changed_pages(self) -> None:
        dark = _snapshot((20, 20, 20))
        self.assertLess(perceptual_change(dark, dark), 0.001)
        self.assertGreater(perceptual_change(dark, _snapshot((230, 230, 230))), 0.5)

    def test_map_can_retry_once_despite_ambient_pixel_change(self) -> None:
        self.assertTrue(_safe_same_page_retry("MAP", "MAP", 0.25))

    def test_inventory_can_retry_once_despite_animated_cards(self) -> None:
        self.assertTrue(_safe_same_page_retry("INVENTORY", "INVENTORY", 0.25))

    def test_other_pages_keep_strict_unchanged_frame_retry(self) -> None:
        self.assertFalse(_safe_same_page_retry("MAIN_MENU", "MAIN_MENU", 0.25))
        self.assertTrue(_safe_same_page_retry("MAIN_MENU", "MAIN_MENU", 0.001))
        self.assertFalse(_safe_same_page_retry("MAP", "MAIN_MENU", 0.001))

    def test_inventory_is_accepted_as_immediate_downstream_of_main_menu(self) -> None:
        snapshot = Snapshot(text="", image="x")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14.robust_page_state",
            return_value="INVENTORY",
        ):
            self.assertEqual(_target_reached("MAIN_MENU", snapshot), (True, "INVENTORY"))

    def test_map_transition_returns_when_inventory_is_already_reached(self) -> None:
        before = Snapshot(text="map", image="before")
        inventory = Snapshot(text="100 / 1000", image="inventory")
        proxy = object()
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14.base._tap"
        ) as tap, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14.base._ensure_stage_geometry_for_state",
            side_effect=lambda _proxy, snapshot, _state, **_kwargs: snapshot,
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v14._wait_for_target",
            return_value=(inventory, "INVENTORY", 0.5),
        ):
            returned = _transition(proxy, before, "MAP")

        self.assertEqual(returned, (inventory, "INVENTORY"))
        tap.assert_called_once_with(proxy, "MAP")

    @patch("pogo_iphone_renamer.ipad_landscape_agent_v14.ocr_mcp_screenshot")
    def test_detail_height_record_overlay_requires_three_signals(self, ocr) -> None:
        ocr.return_value = (
            OCRLine("CP 201", 0.99),
            OCRLine("身高新纪录", 0.99),
            OCRLine("0.25 m", 0.99),
        )
        self.assertTrue(detail_record_overlay_visible(Snapshot(text="", image="x")))

        ocr.return_value = (OCRLine("身高新纪录", 0.99),)
        self.assertFalse(detail_record_overlay_visible(Snapshot(text="", image="x")))


if __name__ == "__main__":
    unittest.main()
