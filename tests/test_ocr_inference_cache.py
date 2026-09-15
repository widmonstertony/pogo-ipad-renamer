import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from PIL import Image
from pogo_iphone_renamer import local_ocr as ocr


class OCRInferenceCacheTests(unittest.TestCase):
    def setUp(self):
        ocr._OCR_CACHE.clear()

    def tearDown(self):
        ocr._OCR_CACHE.clear()

    def test_same_pixels_reuse_inference_but_changes_and_engine_changes_do_not(self):
        engine = Mock(return_value=SimpleNamespace(txts=["禿鷹丫頭"], scores=[.99], boxes=[[[0, 0], [2, 2]]]))
        original = Image.new("RGB", (5, 5), "white")
        with patch.object(ocr, "_engine", return_value=engine):
            first = ocr.ocr_image(original)
            self.assertEqual(ocr.ocr_image(original.copy()), first)
            self.assertEqual(engine.call_count, 1)
            changed = original.copy()
            changed.putpixel((1, 1), (0, 0, 0))
            ocr.ocr_image(changed)
            ocr.ocr_image(Image.new("RGB", (1, 25), "white"))
            self.assertEqual(engine.call_count, 3)
        replacement = Mock(return_value=SimpleNamespace(txts=[], scores=[], boxes=[]))
        with patch.object(ocr, "_engine", return_value=replacement):
            self.assertEqual(ocr.ocr_image(original), ())
            replacement.assert_called_once()

    def test_cache_has_a_fixed_memory_bound(self):
        engine = Mock(return_value=SimpleNamespace(txts=[], scores=[], boxes=[]))
        with patch.object(ocr, "_engine", return_value=engine), patch.object(ocr, "_OCR_CACHE_LIMIT", 2):
            for color in ("red", "green", "blue"):
                ocr.ocr_image(Image.new("RGB", (3, 3), color))
            self.assertEqual(len(ocr._OCR_CACHE), 2)
            ocr.ocr_image(Image.new("RGB", (3, 3), "red"))
            self.assertEqual(engine.call_count, 4)
