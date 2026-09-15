import unittest
from unittest.mock import patch
from PIL import Image
from pogo_iphone_renamer.text_localization import OCRTextBox, locate_exact_text_from_mcp
from pogo_iphone_renamer.policy import PolicyViolation


class RenameFieldRegionTests(unittest.TestCase):
    def locate(self, boxes):
        with patch('pogo_iphone_renamer.text_localization.rotate_mcp_image_upright',
                   return_value=Image.new('RGB', (1000, 1000))), patch(
                   'pogo_iphone_renamer.text_localization.ocr_text_boxes',
                   side_effect=[boxes, (), ()]):
            return locate_exact_text_from_mcp('image', 'profile', '噴嚏熊',
                                             search_region=(.1, .3, .7, .55))

    def test_same_name_in_keyboard_does_not_hide_the_input_field(self):
        field = OCRTextBox('噴嚏熊', .99, 170, 390, 310, 425)
        candidate = OCRTextBox('噴嚏熊', .99, 150, 695, 230, 715)
        self.assertEqual(self.locate((field, candidate)).box, field)

    def test_keyboard_only_is_not_an_input_field(self):
        with self.assertRaises(PolicyViolation):
            self.locate((OCRTextBox('噴嚏熊', .99, 150, 695, 230, 715),))

    def test_two_matches_inside_field_region_remain_ambiguous(self):
        with self.assertRaises(PolicyViolation):
            self.locate((OCRTextBox('噴嚏熊', .99, 170, 390, 310, 425),
                         OCRTextBox('噴嚏熊', .99, 400, 390, 510, 425)))

    def test_exact_region_retry_recovers_full_frame_traditional_ocr_error(self):
        simplified = OCRTextBox('保母虫', .99, 170, 390, 310, 425)
        enlarged_exact = OCRTextBox('保母蟲', .99, 140, 180, 420, 250)
        enlarged_exact_again = OCRTextBox('保母蟲', .98, 210, 270, 630, 375)
        with patch(
            'pogo_iphone_renamer.text_localization.rotate_mcp_image_upright',
            return_value=Image.new('RGB', (1000, 1000)),
        ), patch(
            'pogo_iphone_renamer.text_localization.ocr_text_boxes',
            side_effect=[(simplified,), (enlarged_exact,), (enlarged_exact_again,)],
        ):
            located = locate_exact_text_from_mcp(
                'image', 'profile', '保母蟲', minimum_confidence=.70,
                search_region=(.1, .3, .7, .55),
            )
        self.assertEqual(located.box.text, '保母蟲')
        self.assertAlmostEqual(located.box.left, 170)
        self.assertAlmostEqual(located.box.top, 390)
