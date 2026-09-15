from __future__ import annotations

import io
import re
from dataclasses import dataclass

from PIL import Image, ImageEnhance

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

# These are status badges rendered by Pokémon GO around the title, not any
# part of the editable nickname.  Keeping the list exact prevents a real
# custom nickname from being silently accepted while allowing untouched shiny
# and lucky Pokémon to use the same default-name path as every other species.
DETAIL_STATUS_LABELS = frozenset(
    {
        "亮晶晶",
        "亮晶晶寶可夢",
        "Lucky",
        "Lucky Pokémon",
        "異色",
        "异色",
        "色違",
        "色违",
        "Shiny",
        "極巨化",
        "极巨化",
        "超極巨化",
        "超极巨化",
        "Dynamax",
        "Gigantamax",
        "最佳夥伴",
        "最佳伙伴",
        "夥伴緞帶",
        "伙伴绶带",
        "Best Buddy",
        "Best Buddy Ribbon",
    }
)


def is_detail_status_label(text: str) -> bool:
    return text.strip().casefold() in {
        label.casefold() for label in DETAIL_STATUS_LABELS
    }

# On the calibrated 1366×1024 iPad14,6 detail page, the visible Pokémon name
# is centered at y=528.5 with a 47 px glyph height.  The former 43%–61% crop
# also covered the "Lucky Pokémon" label and HP row below it, so a perfectly
# default name could be falsely treated as a custom nickname.  Keep a modest
# margin around the actual name row but deliberately exclude those metadata
# rows.  A real IV/custom name remains on this same name row and is still
# rejected by the strict species match below.
# New ios-mcp builds return an upright Stage Manager capture.  The title is
# still in the same visual row, but its glyphs reach farther left/right and
# slightly higher than they did in the old rotated capture.  The former narrow
# crop clipped English/custom names (for example ``Ferroseed❶❻❽³³``) into an
# OCR-empty fragment, which made an otherwise safe custom-name skip stall.
NAME_ROW_LEFT = 0.15
NAME_ROW_RIGHT = 0.85
NAME_ROW_TOP = 0.44
NAME_ROW_BOTTOM = 0.57
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


def _default_title_excluding_positioned_metadata(crop_lines, full_lines, width, height, species):
    """Separate HP/gender from the title, never discard a glyph by text alone.

    A wide crop can read a female icon as Q and split 46/46 HP into two
    tokens. Require an independently located whole title and whole HP row;
    every crop token must have bounds proving which row it belongs to.
    Low-confidence extras on the title row remain a veto.
    """
    titles = [line for line in full_lines if line.text == species
              and line.confidence >= .98 and line.bounds is not None
              and width * NAME_ROW_LEFT <= line.bounds[0] < line.bounds[2] <= width * NAME_ROW_RIGHT
              and height * NAME_ROW_TOP <= line.bounds[1] < line.bounds[3] <= height * .54]
    hp = [line for line in full_lines if HP_LINE.fullmatch(line.text)
          and line.confidence >= .85 and line.bounds is not None
          and width * .25 <= line.bounds[0] < line.bounds[2] <= width * .75
          and height * .54 <= line.bounds[1] < line.bounds[3] <= height * .62]
    if len(titles) != 1 or len(hp) != 1:
        return None
    title, health = titles[0], hp[0]
    name_bottom = title.bounds[3]
    if health.bounds[1] <= name_bottom + height * .006:
        return None

    def metadata(line, bounds):
        left, top, right, bottom = bounds
        # On iPad7,2 the male icon can overlap the bottom of the title's
        # bounding box after the Stage Manager crop is contrast-enhanced. It
        # is then read as Q around x=76.8%..81.3%, y=51.9%..55.3%. A genuine
        # Q nickname suffix stays on the title baseline and ends above the
        # title bottom. Require the far-right icon slot *and* a lower edge
        # clearly below the independently located title before ignoring it.
        if (line.text in {"Q", "♀", "♂"}
                and width * .75 <= left < right <= width * .87
                and height * .50 <= top < bottom <= height * .60
                and bottom > name_bottom + height * .01):
            return True
        if top <= name_bottom + height * .006:
            return False
        compact = re.sub(r"\s+", "", line.text).upper()
        hp_text = re.sub(r"\s+", "", health.text).upper()
        hl, ht, hr, hb = health.bounds
        if (compact and compact in hp_text
                and hl - width * .008 <= left < right <= hr + width * .008
                and ht - height * .006 <= top < bottom <= hb + height * .006):
            return True
        return False

    # Full-frame OCR must not contain a suffix that the crop lost.
    for line in full_lines:
        if line.bounds is None:
            return None
        left, top, right, bottom = line.bounds
        if (right > width * NAME_ROW_LEFT and left < width * NAME_ROW_RIGHT
                and bottom > height * NAME_ROW_TOP and top < height * NAME_ROW_BOTTOM
                and line is not title and not metadata(line, line.bounds)):
            return None

    seen_title = 0
    for line in crop_lines:
        if line.bounds is None:
            return None
        # read_row doubles the crop; restore canonical full-frame coordinates.
        left, top, right, bottom = line.bounds
        bounds = (left / 2 + round(width * NAME_ROW_LEFT),
                  top / 2 + round(height * NAME_ROW_TOP),
                  right / 2 + round(width * NAME_ROW_LEFT),
                  bottom / 2 + round(height * NAME_ROW_TOP))
        if line.text == species and line.confidence >= .98:
            if not (abs(bounds[1] - title.bounds[1]) <= height * .015
                    and abs(bounds[3] - title.bounds[3]) <= height * .015
                    and abs(bounds[0] - title.bounds[0]) <= width * .015
                    and abs(bounds[2] - title.bounds[2]) <= width * .015):
                return None
            seen_title += 1
        elif not metadata(line, bounds):
            return None
    if seen_title != 1:
        return None
    return NameRegionResult(species, True, title.confidence, (species, health.text))


def _complete_title_from_fragments(crop_lines, full_lines, width, height, known):
    """Verify a whole OCR title against overlapping crop fragments, never infer it."""
    row = []
    for line in full_lines:
        if line.bounds is None:
            continue
        left, top, right, bottom = line.bounds
        if (width * NAME_ROW_LEFT <= left < right <= width * NAME_ROW_RIGHT
                and height * NAME_ROW_TOP <= top < bottom <= height * NAME_ROW_BOTTOM):
            if not HP_LINE.fullmatch(line.text) and not is_detail_status_label(line.text):
                row.append(line)
    # Retain even low-confidence extra glyphs as a veto: an IV/custom suffix
    # must not disappear simply because its OCR confidence is below 85%.
    if len(row) != 1 or row[0].confidence < 0.98 or row[0].text not in known:
        return None
    name = row[0].text
    fragments = [line for line in crop_lines
                 if not HP_LINE.fullmatch(line.text) and not is_detail_status_label(line.text)]
    if len(fragments) < 2 or not any(HP_LINE.fullmatch(line.text) for line in crop_lines):
        return None
    covered = set()
    for line in fragments:
        if line.confidence < 0.85 or len(line.text) < 2 or line.text not in name:
            return None
        start = name.index(line.text)
        covered.update(range(start, start + len(line.text)))
    if covered != set(range(len(name))):
        return None
    return NameRegionResult(name, True, min(row[0].confidence, *(line.confidence for line in fragments)), (name,))


def _preserved_short_title_from_original_crop(image, full_lines, known):
    """Recover only an explicitly annotated short title, never rename permission.

    Contrast enhancement can merge white circled digits into the dark title
    (長尾火72B). Read the original-color, native-size title without the HP row
    only when full-frame OCR already located an annotation and independent HP.
    Keep the observed tokens; do not expand the stem or reconstruct Unicode.
    """
    width, height = image.size
    hp_lines = [line for line in full_lines
                if line.confidence >= .85 and HP_LINE.fullmatch(line.text)
                and line.bounds is not None
                and width * .25 <= line.bounds[0] < line.bounds[2] <= width * .75
                and height * .54 <= line.bounds[1] < line.bounds[3] <= height * .62]
    annotation_lines = [line for line in full_lines
                        if line.confidence >= .95 and NUMBER_TOKEN.fullmatch(line.text)
                        and line.bounds is not None
                        and width * NAME_ROW_LEFT <= line.bounds[0] < line.bounds[2] <= width * NAME_ROW_RIGHT
                        and height * NAME_ROW_TOP <= line.bounds[1] < line.bounds[3] <= height * .54]
    if len(hp_lines) != 1 or not annotation_lines:
        return None
    region = image.crop((round(width * NAME_ROW_LEFT), round(height * NAME_ROW_TOP),
                         round(width * NAME_ROW_RIGHT), round(height * .54)))
    lines = tuple(ocr_image(region))
    # Even low-confidence or unrecognized extras veto this fallback; never
    # silently discard a piece of a custom name to manufacture agreement.
    if not lines or any(line.bounds is None or line.confidence < .95 for line in lines):
        return None
    stems = [line for line in lines if re.fullmatch(r"[\u3400-\u9fff]{3}", line.text)]
    numbers = [line for line in lines if NUMBER_TOKEN.fullmatch(line.text)]
    if len(stems) != 1 or not 3 <= len(numbers) <= 4 or len(lines) != len(numbers) + 1:
        return None
    stem = stems[0].text
    if stems[0].confidence < .98 or stem in known or not any(name.startswith(stem) for name in known):
        return None
    if any(not (0 <= line.bounds[0] < line.bounds[2] <= region.width
                and 0 <= line.bounds[1] < line.bounds[3] <= region.height)
           for line in lines):
        return None
    values = [int(line.text) for line in numbers]
    if any(value > 100 for value in values) or sum(value <= 15 for value in values) < 2:
        return None
    if not {line.text for line in annotation_lines}.intersection(line.text for line in numbers):
        return None
    return NameRegionResult(
        None, False, min(line.confidence for line in lines),
        (stem, *(line.text for line in numbers), hp_lines[0].text),
    )


def analyze_name_region(image_base64: str, orientation: str) -> NameRegionResult:
    image = rotate_mcp_image_upright(image_base64, orientation)
    width, height = image.size
    known = traditional_chinese_species()
    raw_rows = []

    def read_row(top: float, bottom: float) -> tuple[OCRLine, ...]:
        region = image.crop(
            (
                round(width * NAME_ROW_LEFT),
                round(height * top),
                round(width * NAME_ROW_RIGHT),
                round(height * bottom),
            )
        )
        region = region.resize((region.width * 2, region.height * 2))
        region = ImageEnhance.Contrast(region).enhance(1.8)
        raw = tuple(ocr_image(region))
        raw_rows.append(raw)
        return tuple(line for line in raw if line.confidence >= 0.85)

    lines = read_row(NAME_ROW_TOP, NAME_ROW_BOTTOM)
    matches = {line.text: line.confidence for line in lines if line.text in known}
    if not matches and orientation == "STAGE_MANAGER_PORTRAIT_WINDOW":
        # iPad7,2 supplies a sideways, heavily compressed Stage Manager JPEG.
        # Rotating and stretching its narrow game window can leave a periodic
        # sampling pattern that PP-OCR intermittently reads as only the final
        # Han character even though the complete title is plainly visible.
        # A one-time in-memory JPEG resample removes that pattern.  This is
        # attempted only after the normal exact-species read fails and still
        # has to satisfy every existing title/HP/custom-name veto below.
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=90, subsampling=0)
        output.seek(0)
        image = Image.open(output).convert("RGB")
        raw_rows.clear()
        lines = read_row(NAME_ROW_TOP, NAME_ROW_BOTTOM)
        matches = {line.text: line.confidence for line in lines if line.text in known}
    if not matches:
        fallback_lines = read_row(OCCLUDED_NAME_ROW_TOP, OCCLUDED_NAME_ROW_BOTTOM)
        fallback_matches = {
            line.text: line.confidence for line in fallback_lines if line.text in known
        }
        # Keep a readable primary title when the fallback only sees unrelated
        # page chrome.  Replacing it made English/custom names look empty and
        # prevented the independent candy-label proof below.
        if fallback_matches:
            lines = fallback_lines
            matches = fallback_matches
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
        raw_full_lines = tuple(ocr_image(image))
        complete_title = _complete_title_from_fragments(raw_rows[0], raw_full_lines, width, height, known)
        if complete_title is not None:
            return complete_title
        full_lines = tuple(line for line in raw_full_lines if line.confidence >= 0.85)
        # Crop OCR can split/merge the IV glyphs differently from full-frame
        # OCR. For an evolved Pokemon its candy label is not its species,
        # so do not require candy text to complete a shortened title. Instead
        # corroborate the SAME visible three-character prefix in both reads,
        # with numeric annotations and HP in the crop and an annotated title
        # located inside the full-frame title row. This only proves custom:
        # retain the observed text, never expand a species or recreate Unicode.
        prefixes = {
            text for text in evidence
            if re.fullmatch(r"[\u3400-\u9fff]{3}", text)
            and text not in known
            and any(name.startswith(text) for name in known)
        }
        if (
            len(prefixes) == 1
            and any(HP_LINE.fullmatch(text) for text in evidence)
            and sum(bool(NUMBER_TOKEN.fullmatch(text)) for text in evidence) >= 2
        ):
            prefix = next(iter(prefixes))
            for line in full_lines:
                if line.bounds is None or not line.text.startswith(prefix):
                    continue
                left, top, right, bottom = line.bounds
                if not (
                    width * NAME_ROW_LEFT <= left < right <= width * NAME_ROW_RIGHT
                    and height * NAME_ROW_TOP <= top < bottom <= height * NAME_ROW_BOTTOM
                ):
                    continue
                if re.fullmatch(r"[0-9]{2,8}", line.text[len(prefix):]):
                    return NameRegionResult(None, False, line.confidence, (line.text, *evidence))
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
        # A non-Chinese nickname can have no exact match in the Traditional
        # Chinese species table.  It is still safe to preserve it when two
        # independent fields on this *same* detail page agree: (1) the title
        # row contains a substantial Latin name, and (2) the candy label
        # names one known species.  This never authorizes a rename; it only
        # prevents an already-custom nickname from blocking navigation.
        candy_species: str | None = None
        for line in full_lines:
            for known_species in known:
                if line.text.startswith(f"{known_species}的糖果"):
                    candy_species = known_species
                    break
            if candy_species is not None:
                break
        if candy_species is not None:
            # A user-supplied Traditional Chinese nickname can contain no
            # Latin letters or IV digits (for example ``完美蜈蚣``).  Preserve
            # it when two independent OCR views agree on that exact title,
            # the full-frame bounds place it on the editable name row, and
            # the text is not any official species name.  The candy label is
            # used only as same-detail corroboration; it is not assumed to be
            # the evolved Pokémon's species.
            crop_han_titles = {
                line.text
                for line in lines
                if line.confidence >= 0.98
                and re.fullmatch(r"[\u3400-\u9fff]{2,12}", line.text)
                and line.text not in known
                and not is_detail_status_label(line.text)
            }
            positioned_han_titles = []
            for line in full_lines:
                if (
                    line.bounds is None
                    or line.confidence < 0.98
                    or line.text not in crop_han_titles
                ):
                    continue
                left, top, right, bottom = line.bounds
                if (
                    width * NAME_ROW_LEFT <= left < right <= width * NAME_ROW_RIGHT
                    and height * NAME_ROW_TOP <= top < bottom <= height * NAME_ROW_BOTTOM
                ):
                    positioned_han_titles.append(line)
            if len(positioned_han_titles) == 1 and any(
                HP_LINE.fullmatch(line.text) for line in full_lines
            ):
                title = positioned_han_titles[0]
                return NameRegionResult(
                    candy_species,
                    False,
                    title.confidence,
                    (title.text,),
                )

            # The generated IV nickname intentionally shortens a long
            # species name to keep the editable title compact. RapidOCR can
            # therefore read ``麻麻小121215`` instead of the full title
            # ``麻麻小魚``. The candy label is a separate, immutable detail
            # field that supplies the complete species. A title token made of
            # at least two leading species characters followed by a number is
            # conclusive evidence of that existing generated nickname; it is
            # safe to preserve but never authorizes a rename.
            for line in full_lines:
                visible = line.text.strip()
                for prefix_length in range(len(candy_species) - 1, 1, -1):
                    prefix = candy_species[:prefix_length]
                    if not visible.startswith(prefix):
                        continue
                    suffix = visible.removeprefix(prefix)
                    if suffix and any(character.isdigit() for character in suffix):
                        return NameRegionResult(
                            candy_species,
                            False,
                            line.confidence,
                            (visible,),
                        )
        title_tokens = tuple(
            line.text
            for line in lines
            if not HP_LINE.fullmatch(line.text)
            and not NUMBER_TOKEN.fullmatch(line.text)
            and not is_detail_status_label(line.text)
        )
        if candy_species and any(
            sum(character.isascii() and character.isalpha() for character in token) >= 3
            for token in title_tokens
        ):
            confidence = min(
                [line.confidence for line in lines if line.text in title_tokens]
                or [0.85]
            )
            return NameRegionResult(
                candy_species,
                False,
                confidence,
                title_tokens,
            )
        # Circled IV glyphs can make the cropped OCR rotate the title and
        # lose its Latin stem, even while the full-frame OCR reads it clearly.
        # Use that existing read only when it locates one clearly custom title
        # inside the actual name row, with independent candy and HP evidence.
        # A Han title with an explicit numeric suffix is also non-default even
        # when OCR mixes 電/电. Keep the observed text, without normalizing it
        # into a species name or reconstructing the original Unicode glyphs.
        # These paths only preserve an existing nickname, never authorize input.
        if candy_species and any(HP_LINE.fullmatch(line.text) for line in full_lines):
            custom_titles = []
            for line in full_lines:
                if line.bounds is None or line.confidence < 0.90:
                    continue
                if HP_LINE.fullmatch(line.text) or is_detail_status_label(line.text):
                    continue
                left, top, right, bottom = line.bounds
                if not (
                    width * NAME_ROW_LEFT <= left < right <= width * NAME_ROW_RIGHT
                    and height * NAME_ROW_TOP <= top < bottom <= height * NAME_ROW_BOTTOM
                ):
                    continue
                has_latin_name = sum(char.isascii() and char.isalpha() for char in line.text) >= 3
                has_annotated_han_name = bool(
                    re.fullmatch(r"[\u3400-\u9fff]{2,}[0-9]{2,8}", line.text)
                )
                if has_latin_name or has_annotated_han_name:
                    custom_titles.append(line)
            if len(custom_titles) == 1:
                title = custom_titles[0]
                return NameRegionResult(
                    candy_species, False, title.confidence, (title.text,)
                )
        preserved_title = _preserved_short_title_from_original_crop(image, raw_full_lines, known)
        if preserved_title is not None:
            return preserved_title
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
        and not is_detail_status_label(line.text)
    ]
    is_default = len(numeric_tokens) < 2 and not unexpected
    if not is_default and lines == tuple(line for line in raw_rows[0] if line.confidence >= .85):
        # Only the primary, calibrated row can use this coordinate proof.
        resolved = _default_title_excluding_positioned_metadata(
            raw_rows[0], tuple(ocr_image(image)), width, height, species
        )
        if resolved is not None:
            return resolved
        # An unlocated Q or broken HP token is uncertainty, not proof of a
        # custom nickname. Do not count this as an already-named card.
        if unexpected and len(numeric_tokens) < 2 and all(
            token in {"Q", "♀", "♂"} or re.fullmatch(r"\d+\s*H(?:P)?", token, re.I)
            for token in unexpected
        ):
            return NameRegionResult(None, False, 0.0, evidence)
    if is_default:
        # The narrow high-contrast crop can drop dark circled values entirely
        # (the CP12 Fennekin title was read as just 火狐狸). Before permitting
        # any rename, let the existing full-frame OCR veto that decision when
        # it sees digits in the precisely located title row. A single numeric
        # suffix/percentage there is not CP, weight, candy, date or HP. Retain
        # the observed evidence only; never reconstruct the Unicode nickname.
        annotations = []
        for line in ocr_image(image):
            if line.bounds is None or line.confidence < 0.85:
                continue
            if HP_LINE.fullmatch(line.text) or is_detail_status_label(line.text):
                continue
            left, top, right, bottom = line.bounds
            if not (
                width * NAME_ROW_LEFT <= left < right <= width * NAME_ROW_RIGHT
                and height * NAME_ROW_TOP <= top < bottom <= height * NAME_ROW_BOTTOM
            ):
                continue
            if any(character.isdigit() for character in line.text):
                annotations.append(line.text)
        if annotations:
            is_default = False
            evidence = tuple(dict.fromkeys((*evidence, *annotations)))
    return NameRegionResult(species, is_default, matches[species], evidence)
