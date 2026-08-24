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
# When a neighboring Stage Manager card covers part of the active Pokémon GO
# window, the still-visible detail page is vertically compressed after it is
# normalized to the canonical frame.  Its title row moves upward by roughly
# 8%.  This is a fallback only after the ordinary title-row crop has failed;
# a regular unoccluded page keeps the narrower primary crop above.
OCCLUDED_NAME_ROW_TOP = 0.38
OCCLUDED_NAME_ROW_BOTTOM = 0.47


@dataclass(frozen=True)
class NameRegionResult:
    species: str | None
    is_default: bool
    confidence: float
    evidence: tuple[str, ...]


def analyze_name_region(image_base64: str, orientation: str) -> NameRegionResult:
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, height = image.size
    known = traditional_chinese_species()

    def read_row(top: float, bottom: float) -> tuple[OCRLine, ...]:
        region = image.crop(
            (
                round(width * 0.30),
                round(height * top),
                round(width * 0.70),
                round(height * bottom),
            )
        )
        region = region.resize((region.width * 2, region.height * 2))
        region = ImageEnhance.Contrast(region).enhance(1.8)
        return tuple(line for line in ocr_image(region) if line.confidence >= 0.85)

    lines = read_row(NAME_ROW_TOP, NAME_ROW_BOTTOM)
    matches = {line.text: line.confidence for line in lines if line.text in known}
    if not matches:
        lines = read_row(OCCLUDED_NAME_ROW_TOP, OCCLUDED_NAME_ROW_BOTTOM)
        matches = {line.text: line.confidence for line in lines if line.text in known}
    evidence = tuple(line.text for line in lines)
    if len(matches) > 1:
        raise PolicyViolation("名称区域同时匹配多个繁中物种；已停止")
    species = next(iter(matches), None)
    if species is None:
        # In a narrow/moved Stage Manager card RapidOCR can join the title and
        # all circled IV glyphs into one line (for example ``炭小侍151513``).
        # That is enough to prove this is *not* the untouched default name,
        # without reconstructing or inventing the Unicode nickname.  Only a
        # known exact species prefix followed by a digit is accepted; labels
        # such as ``炭小侍的糖果`` remain unreadable rather than custom.
        full_lines = tuple(
            line for line in ocr_image(image) if line.confidence >= 0.85
        )
        for line in full_lines:
            for known_species in sorted(known, key=len, reverse=True):
                if not line.text.startswith(known_species):
                    continue
                suffix = line.text.removeprefix(known_species)
                if suffix and any(char.isdigit() for char in suffix):
                    return NameRegionResult(
                        known_species,
                        False,
                        line.confidence,
                        (line.text,),
                    )
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
