from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.ipad_landscape_agent_v16 import (
    RenamePencilLocalizationUnavailable,
    _locate_dynamic_pencil_with_read_only_retry,
    _require_visual_detail,
    _wait_for_dialog_or_detail_after_pencil,
    dynamic_pencil_point,
)
from pogo_iphone_renamer.local_ocr_v4 import (
    LocatedText,
    OCRTextBox,
    calibrated_name_location,
)
from pogo_iphone_renamer.policy import PolicyViolation


class DynamicPencilTests(unittest.TestCase):
    def test_three_character_name_reproduces_calibrated_point(self) -> None:
        located = LocatedText(
            OCRTextBox("鯉魚王", 0.99, 577, 505, 787, 552), 1366, 1024
        )
        x, y = dynamic_pencil_point(
            located, observation_width=1024, observation_height=1366
        )
        self.assertAlmostEqual(x, 614.7, places=1)
        self.assertAlmostEqual(y, 705.0, places=1)

    def test_longer_name_moves_pencil_right(self) -> None:
        short = LocatedText(
            OCRTextBox("鯉魚王", 0.99, 577, 505, 787, 552), 1366, 1024
        )
        long = LocatedText(
            OCRTextBox("瑪瑙水母", 0.99, 542, 505, 822, 552), 1366, 1024
        )
        short_x, _ = dynamic_pencil_point(
            short, observation_width=1024, observation_height=1366
        )
        long_x, _ = dynamic_pencil_point(
            long, observation_width=1024, observation_height=1366
        )
        self.assertGreater(long_x, short_x + 25)

    def test_calibrated_fallback_reproduces_three_and_four_character_boxes(self) -> None:
        short = calibrated_name_location(
            "鯉魚王", image_width=1366, image_height=1024
        )
        long = calibrated_name_location(
            "瑪瑙水母", image_width=1366, image_height=1024
        )

        self.assertAlmostEqual(short.box.left, 577.0, places=1)
        self.assertAlmostEqual(short.box.right, 787.0, places=1)
        self.assertAlmostEqual(long.box.left, 542.0, places=1)
        self.assertAlmostEqual(long.box.right, 822.0, places=1)
        self.assertAlmostEqual(short.box.center_y, 528.5, places=1)

    def test_rejects_text_outside_name_row(self) -> None:
        located = LocatedText(
            OCRTextBox("鯉魚王", 0.99, 577, 50, 787, 90), 1366, 1024
        )
        with self.assertRaises(PolicyViolation):
            dynamic_pencil_point(
                located, observation_width=1024, observation_height=1366
            )

    def test_transient_empty_ocr_is_retried_without_tapping(self) -> None:
        proxy = SimpleNamespace(
            observation=SimpleNamespace(width=1024, height=1366, token="fresh")
        )
        first = Snapshot("detail", "first")
        second = Snapshot("detail", "second")
        point = (615.0, 705.0)
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16._require_visual_detail"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16._dynamic_pencil_coordinates",
            side_effect=[PolicyViolation("empty OCR"), point],
        ) as locate, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16.base._next_snapshot",
            return_value=second,
        ) as refresh, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16.emit"
        ):
            returned, coordinates = _locate_dynamic_pencil_with_read_only_retry(
                proxy, first, "可達鴨", extra_gap=33.0
            )

        self.assertIs(returned, second)
        self.assertEqual(coordinates, point)
        self.assertEqual(locate.call_count, 2)
        refresh.assert_called_once_with(proxy, 0.6)
        self.assertFalse(hasattr(proxy, "call_tool"))

    def test_persistent_empty_ocr_raises_typed_pre_tap_failure(self) -> None:
        proxy = SimpleNamespace(
            observation=SimpleNamespace(width=1024, height=1366, token="fresh")
        )
        snapshots = [Snapshot("detail", "second"), Snapshot("detail", "third")]
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16._require_visual_detail"
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16._dynamic_pencil_coordinates",
            side_effect=PolicyViolation("empty OCR"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16.base._next_snapshot",
            side_effect=snapshots,
        ) as refresh, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16.emit"
        ):
            with self.assertRaises(RenamePencilLocalizationUnavailable) as raised:
                _locate_dynamic_pencil_with_read_only_retry(
                    proxy, Snapshot("detail", "first"), "可達鴨", extra_gap=33.0
                )

        self.assertIs(raised.exception.snapshot, snapshots[-1])
        self.assertEqual(refresh.call_count, 2)

    def test_post_pencil_proof_does_not_rerun_generic_inventory_validation(self) -> None:
        proxy = SimpleNamespace(
            observation=SimpleNamespace(width=1024, height=1366, token="fresh")
        )
        detail = Snapshot("same-proven-detail", "detail")
        point = (615.0, 705.0)
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16.base._validate_expected",
            side_effect=PolicyViolation("点击第一张卡片后没有验证到详情页"),
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16._require_visual_detail"
        ) as require_detail, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16._dynamic_pencil_coordinates",
            return_value=point,
        ):
            returned, coordinates = _locate_dynamic_pencil_with_read_only_retry(
                proxy,
                detail,
                "涼脊龍",
                extra_gap=45.0,
                detail_already_verified=True,
            )

        self.assertIs(returned, detail)
        self.assertEqual(coordinates, point)
        require_detail.assert_not_called()

    def test_post_pencil_map_frame_waits_for_same_detail_without_a_second_tap(self) -> None:
        first = Snapshot("map-looking", "first")
        restored = Snapshot("detail", "restored")
        with patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16._verified_dialog_snapshot",
            return_value=None,
        ) as dialog, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16.base.local_page_state",
            side_effect=["MAP", "DETAIL"],
        ), patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16.base._next_snapshot",
            return_value=restored,
        ) as next_snapshot, patch(
            "pogo_iphone_renamer.ipad_landscape_agent_v16.emit"
        ) as emit:
            returned = _wait_for_dialog_or_detail_after_pencil(
                object(), "電電蟲", first
            )

        self.assertIs(returned, restored)
        self.assertEqual(dialog.call_count, 2)
        next_snapshot.assert_called_once_with(unittest.mock.ANY, 0.8)
        self.assertIn("只读等待", emit.call_args.kwargs["message"])


if __name__ == "__main__":
    unittest.main()
