from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .landscape_cv import rotate_mcp_image_upright
from .local_ocr import _engine
from .policy import PolicyViolation


@dataclass(frozen=True)
class OCRTextBox:
    text: str
    confidence: float
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2.0


@dataclass(frozen=True)
class LocatedText:
    box: OCRTextBox
    image_width: int
    image_height: int


def ocr_text_boxes(image: Image.Image) -> tuple[OCRTextBox, ...]:
    import numpy as np

    result = _engine()(np.asarray(image.convert("RGB")))
    texts = tuple(result.txts or ())
    scores = tuple(result.scores or ())
    boxes = tuple(result.boxes) if result.boxes is not None else ()
    found: list[OCRTextBox] = []
    for text, score, corners in zip(texts, scores, boxes):
        clean = unicodedata.normalize("NFC", str(text)).strip()
        if not clean:
            continue
        points = np.asarray(corners, dtype=float).reshape(-1, 2)
        found.append(
            OCRTextBox(
                text=clean,
                confidence=float(score),
                left=float(points[:, 0].min()),
                top=float(points[:, 1].min()),
                right=float(points[:, 0].max()),
                bottom=float(points[:, 1].max()),
            )
        )
    return tuple(found)


def locate_exact_text_from_mcp(
    image_base64: str,
    orientation: str,
    exact_text: str,
    *,
    minimum_confidence: float = 0.85,
) -> LocatedText:
    normalized = unicodedata.normalize("NFC", exact_text).strip()
    upright = rotate_mcp_image_upright(image_base64, orientation)
    matches = [
        box
        for box in ocr_text_boxes(upright)
        if box.text == normalized and box.confidence >= minimum_confidence
    ]
    if not matches:
        raise PolicyViolation(f"详情页未定位到精确名称文字框：{normalized}")
    if len(matches) > 1:
        matches = [
            box
            for box in matches
            if 0.42 <= box.center_y / upright.height <= 0.60
        ]
    if len(matches) != 1:
        raise PolicyViolation(f"详情页名称文字框不唯一：{normalized}")
    return LocatedText(matches[0], upright.width, upright.height)


def _remap_crop_box(
    box: OCRTextBox,
    *,
    crop_left: int,
    crop_top: int,
    scale: float,
) -> OCRTextBox:
    return OCRTextBox(
        text=box.text,
        confidence=box.confidence,
        left=crop_left + box.left / scale,
        top=crop_top + box.top / scale,
        right=crop_left + box.right / scale,
        bottom=crop_top + box.bottom / scale,
    )


def locate_exact_name_from_mcp(
    image_base64: str,
    orientation: str,
    exact_text: str,
    *,
    minimum_confidence: float = 0.70,
) -> LocatedText:
    """Locate one already-known species name using targeted OCR passes.

    Running text detection over the whole Stage Manager desktop is much less
    reliable than reading the fixed Pokémon name row.  The expected text is
    already constrained by the local Traditional-Chinese species database, so
    exact matching in this narrow safe row remains deterministic.
    """

    normalized = unicodedata.normalize("NFC", exact_text).strip()
    upright = rotate_mcp_image_upright(image_base64, orientation)
    width, height = upright.size
    # Keep the original full-frame pass because it is fastest on clear frames.
    full_matches = [
        box
        for box in ocr_text_boxes(upright)
        if box.text == normalized
        and box.confidence >= minimum_confidence
        and 0.42 <= box.center_y / height <= 0.60
    ]
    if full_matches:
        return LocatedText(
            max(full_matches, key=lambda box: box.confidence), width, height
        )
    candidates: list[OCRTextBox] = []

    left = round(width * 0.28)
    top = round(height * 0.42)
    right = round(width * 0.72)
    bottom = round(height * 0.60)
    region = upright.crop((left, top, right, bottom))
    variants: tuple[tuple[Image.Image, float], ...] = (
        (region.resize((region.width * 2, region.height * 2)), 2.0),
        (
            ImageEnhance.Contrast(
                region.resize((region.width * 3, region.height * 3))
            ).enhance(1.8),
            3.0,
        ),
        (
            ImageOps.autocontrast(region.convert("L"))
            .filter(ImageFilter.UnsharpMask(radius=1.2, percent=170, threshold=2))
            .convert("RGB")
            .resize((region.width * 3, region.height * 3)),
            3.0,
        ),
    )
    for variant, scale in variants:
        candidates.extend(
            _remap_crop_box(
                box,
                crop_left=left,
                crop_top=top,
                scale=scale,
            )
            for box in ocr_text_boxes(variant)
        )

    matches = [
        box
        for box in candidates
        if box.text == normalized
        and box.confidence >= minimum_confidence
        and 0.42 <= box.center_y / height <= 0.60
    ]
    if not matches:
        raise PolicyViolation(f"详情页多尺度 OCR 未定位到精确名称：{normalized}")
    # The same physical row can be returned by several preprocessing passes.
    # Choosing its highest-confidence exact match is deterministic and avoids
    # mistaking those duplicate observations for multiple on-screen names.
    return LocatedText(max(matches, key=lambda box: box.confidence), width, height)


def calibrated_name_location(
    exact_text: str,
    *,
    image_width: int,
    image_height: int,
) -> LocatedText:
    """Derive the centered name box from the verified iPad14,6 font metrics.

    This is used only after targeted OCR fails and only on a page already
    proven to be DETAIL.  Three- and four-CJK-character captures calibrate to
    70 upright pixels per full-width glyph at 1366 px width.
    """

    normalized = unicodedata.normalize("NFC", exact_text).strip()
    if not normalized or len(normalized) > 12:
        raise PolicyViolation("物种名长度不适合真机字体标定后备")
    units = 0.0
    for char in normalized:
        if unicodedata.combining(char):
            continue
        units += 1.0 if unicodedata.east_asian_width(char) in {"W", "F"} else 0.55
    if not (1.5 <= units <= 8.0):
        raise PolicyViolation("物种名显示宽度超出真机字体标定范围")

    glyph_width = image_width * (70.0 / 1366.0)
    text_width = units * glyph_width
    # The measured text baseline is one pixel left of the mathematical center
    # on the canonical 1366-wide game surface.
    center_x = image_width * (682.0 / 1366.0)
    center_y = image_height * (528.5 / 1024.0)
    text_height = image_height * (47.0 / 1024.0)
    return LocatedText(
        OCRTextBox(
            normalized,
            0.0,
            center_x - text_width / 2.0,
            center_y - text_height / 2.0,
            center_x + text_width / 2.0,
            center_y + text_height / 2.0,
        ),
        image_width,
        image_height,
    )
