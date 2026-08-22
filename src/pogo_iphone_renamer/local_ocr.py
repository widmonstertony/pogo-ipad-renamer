from __future__ import annotations

import logging
import unicodedata
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


@lru_cache(maxsize=1)
def _engine():
    # RapidOCR bundles the ONNX models in its wheel.  The engine is completely
    # local after installation and is intentionally not given a URL.
    logging.getLogger("RapidOCR").setLevel(logging.ERROR)
    from rapidocr import RapidOCR

    return RapidOCR()


def ocr_image(image: Image.Image) -> tuple[OCRLine, ...]:
    import numpy as np

    result = _engine()(np.asarray(image.convert("RGB")))
    texts = tuple(result.txts or ())
    scores = tuple(result.scores or ())
    return tuple(
        OCRLine(unicodedata.normalize("NFC", str(text)).strip(), float(score))
        for text, score in zip(texts, scores)
        if str(text).strip()
    )


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
    # iOS Chinese keyboard OCR often merges the adjacent input-assistant
    # labels into one token (for example ``完成取消``).  The title and exact OK
    # button remain independent, so accepting a token that *contains* Cancel
    # still proves the complete rename dialog without guessing a coordinate.
    has_cancel = any("取消" in text or "cancel" in text for text in visible)
    return has_title and "ok" in visible and has_cancel
