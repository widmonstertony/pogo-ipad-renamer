from __future__ import annotations

import re
from dataclasses import dataclass

from PIL import ImageEnhance

from .landscape_cv import rotate_mcp_image_upright
from .local_ocr import OCRLine, ocr_image
from .policy import PolicyViolation
from .species_db import traditional_chinese_species


# The right edge of the narrow name crop can clip the final ``P`` from HP.
# Treat ``108/108H`` as the same read-only detail stat as ``108/108HP``;
# otherwise a perfectly plain species name is falsely classified as a custom
# nickname.  Keep the leading H mandatory so IV number tokens remain strong
# annotation evidence.
HP_LINE = re.compile(
    r"^(?:"
    r"\d+\s*/\s*\d+\s*H(?:P)?"
    r"|[DPH]{1,2}\s*\d+\s*/\s*\d+"
    r")$",
    re.IGNORECASE,
)
NUMBER_TOKEN = re.compile(r"^\d{1,3}$")

# On the calibrated 1366×1024 iPad14,6 detail page, the visible Pokémon name
# is centered at y=528.5 with a 47 px glyph height.  The former 43%–61% crop
# also covered the "Lucky Pokémon" label and HP row below it, so a perfectly
# default name could be falsely treated as a custom nickname.  Keep a modest
# margin around the actual name row but deliberately exclude those metadata
# rows.  A real IV/custom name remains on this same name row and is still
# rejected by the strict species match below.
NAME_ROW_TOP = 0.47
NAME_ROW_BOTTOM = 0.55


@dataclass(frozen=True)
class NameRegionResult:
    species: str | None
    is_default: bool
    confidence: float
    evidence: tuple[str, ...]


def analyze_name_region(image_base64: str, orientation: str) -> NameRegionResult:
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, height = image.size
    region = image.crop(
        (
            round(width * 0.30),
            round(height * NAME_ROW_TOP),
            round(width * 0.70),
            round(height * NAME_ROW_BOTTOM),
        )
    )
    region = region.resize((region.width * 2, region.height * 2))
    region = ImageEnhance.Contrast(region).enhance(1.8)
    lines = tuple(line for line in ocr_image(region) if line.confidence >= 0.85)
    known = traditional_chinese_species()
    matches = {line.text: line.confidence for line in lines if line.text in known}
    evidence = tuple(line.text for line in lines)
    if len(matches) > 1:
        raise PolicyViolation("名称区域同时匹配多个繁中物种；已停止")
    species = next(iter(matches), None)
    if species is None:
        return NameRegionResult(None, False, 0.0, evidence)

    # Circled IV and superscript percentage glyphs are recognized by PP-OCR as
    # separate plain-number tokens. A default name crop contains only species
    # plus the normal "95/95 HP" line; two or more numeric tokens prove an IV
    # annotation/custom nickname and must never be renamed again.
    numeric_tokens = [line.text for line in lines if NUMBER_TOKEN.fullmatch(line.text)]
    unexpected = [
        line.text
        for line in lines
        if line.text != species
        and not HP_LINE.fullmatch(line.text)
        and not NUMBER_TOKEN.fullmatch(line.text)
    ]
    is_default = len(numeric_tokens) < 2 and not unexpected
    return NameRegionResult(species, is_default, matches[species], evidence)
