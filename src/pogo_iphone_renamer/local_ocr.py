from __future__ import annotations

import logging
import hashlib
import unicodedata
from collections import OrderedDict
from dataclasses import dataclass
from functools import lru_cache
from typing import Iterable

from PIL import Image

from .landscape_cv import rotate_mcp_image_upright
from .policy import PolicyViolation
from .species_db import traditional_chinese_species


@dataclass(frozen=True)
class OCRLine:
    text: str
    confidence: float
    # Coordinates are in the supplied image's pixel space.  Existing readers
    # use only text/confidence; the optional bounds let navigation safely
    # target a specific visible inventory card without guessing a grid cell.
    bounds: tuple[float, float, float, float] | None = None


@lru_cache(maxsize=1)
def _engine():
    # RapidOCR bundles the ONNX models in its wheel.  The engine is completely
    # local after installation and is intentionally not given a URL.
    logging.getLogger("RapidOCR").setLevel(logging.ERROR)
    from rapidocr import RapidOCR

    return RapidOCR()


# Cache inference, never observations. New screenshots still receive unique
# capture IDs and must pass the normal independent-frame checks. Keeping only
# a pixel hash and immutable OCR lines bounds memory without retaining frames.
_OCR_CACHE_LIMIT = 64
_OCR_CACHE: OrderedDict[tuple, tuple[OCRLine, ...]] = OrderedDict()


def ocr_image(image: Image.Image) -> tuple[OCRLine, ...]:
    import numpy as np

    rgb = image.convert("RGB")
    engine = _engine()
    key = (engine, rgb.size, hashlib.sha256(rgb.tobytes()).digest())
    if key in _OCR_CACHE:
        _OCR_CACHE.move_to_end(key)
        return _OCR_CACHE[key]
    result = engine(np.asarray(rgb))
    texts = tuple(result.txts or ())
    scores = tuple(result.scores or ())
    boxes = tuple(result.boxes) if result.boxes is not None else ()

    def bounds_for(box: object) -> tuple[float, float, float, float] | None:
        try:
            points = np.asarray(box, dtype=float).reshape(-1, 2)
            if len(points) < 2:
                return None
            return (
                float(points[:, 0].min()),
                float(points[:, 1].min()),
                float(points[:, 0].max()),
                float(points[:, 1].max()),
            )
        except (TypeError, ValueError):
            return None

    lines = tuple(
        OCRLine(
            unicodedata.normalize("NFC", str(text)).strip(),
            float(score),
            bounds_for(box),
        )
        for text, score, box in zip(texts, scores, boxes)
        if str(text).strip()
    )
    _OCR_CACHE[key] = lines
    while len(_OCR_CACHE) > _OCR_CACHE_LIMIT:
        _OCR_CACHE.popitem(last=False)
    return lines


def ocr_mcp_screenshot(image_base64: str, orientation: str) -> tuple[OCRLine, ...]:
    return ocr_image(rotate_mcp_image_upright(image_base64, orientation))


def exact_species_from_lines(
    lines: Iterable[OCRLine], *, minimum_confidence: float = 0.70
) -> tuple[str, float]:
    known = traditional_chinese_species()
    candidates: dict[str, float] = {}
    for line in lines:
        if line.confidence < minimum_confidence or line.text not in known:
            continue
        candidates[line.text] = max(candidates.get(line.text, 0.0), line.confidence)
    if not candidates:
        raise PolicyViolation("离线 OCR 未能精确匹配本地繁中物种名；已停止")
    if len(candidates) > 1:
        names = "、".join(sorted(candidates))
        raise PolicyViolation(f"离线 OCR 同时匹配到多个物种名：{names}；已停止")
    return next(iter(candidates.items()))


def exact_species_from_mcp_screenshot(
    image_base64: str, orientation: str, *, minimum_confidence: float = 0.70
) -> tuple[str, float]:
    return exact_species_from_lines(
        ocr_mcp_screenshot(image_base64, orientation),
        minimum_confidence=minimum_confidence,
    )


def rename_dialog_visible(lines: Iterable[OCRLine]) -> bool:
    visible = {line.text.casefold() for line in lines if line.confidence >= 0.65}
    has_title = any(
        text in visible
        for text in ("設定暱稱", "设定昵称", "設定暱稱。", "nickname", "set nickname")
    )
    # iOS Chinese keyboard can crop or suppress the right-side “取消” label
    # entirely.  “設定暱稱” plus the large, independent dialog OK control is
    # already unique to Pokémon GO's rename modal; requiring Cancel as well
    # misclassifies a visibly complete keyboard-open dialog as the main menu.
    # When present, Cancel remains useful corroboration but is no longer a
    # hard prerequisite for *read-only* dialog recognition.
    return has_title and "ok" in visible
