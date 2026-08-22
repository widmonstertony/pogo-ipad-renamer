from __future__ import annotations

import unicodedata
from decimal import Decimal, ROUND_HALF_UP


CIRCLED_IV = tuple("⓿❶❷❸❹❺❻❼❽❾❿⓫⓬⓭⓮⓯")
SUPERSCRIPT = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")


def iv_percent(attack: int, defense: int, stamina: int) -> int:
    values = (attack, defense, stamina)
    if any(not isinstance(value, int) or isinstance(value, bool) for value in values):
        raise ValueError("IV values must be integers")
    if any(value < 0 or value > 15 for value in values):
        raise ValueError("IV values must be between 0 and 15")
    percentage = (Decimal(sum(values)) * Decimal(100)) / Decimal(45)
    return int(percentage.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def superscript_number(value: int) -> str:
    if value < 0:
        raise ValueError("superscript value must not be negative")
    return str(value).translate(SUPERSCRIPT)


def graphemeish_clusters(text: str) -> list[str]:
    clusters: list[str] = []
    for char in unicodedata.normalize("NFC", text):
        if clusters and (unicodedata.combining(char) or char == "\ufe0f"):
            clusters[-1] += char
        else:
            clusters.append(char)
    return clusters


def generate_iv_nickname(
    species: str,
    attack: int,
    defense: int,
    stamina: int,
    *,
    legacy_move: bool = False,
    max_characters: int = 12,
    max_utf8_bytes: int = 24,
) -> str:
    species = unicodedata.normalize("NFC", species).strip()
    if not species:
        raise ValueError("species must not be empty")
    suffix = (
        CIRCLED_IV[attack]
        + CIRCLED_IV[defense]
        + CIRCLED_IV[stamina]
        + superscript_number(iv_percent(attack, defense, stamina))
        + ("(+)" if legacy_move else "")
    )
    clusters = graphemeish_clusters(species)
    prefix: list[str] = []
    for cluster in clusters:
        candidate = "".join((*prefix, cluster)) + suffix
        if len(graphemeish_clusters(candidate)) > max_characters:
            break
        # The UI advertises a 12-character limit, while the game also rejects
        # nicknames over 24 UTF-8 bytes. CJK, circled IV digits and most
        # superscripts occupy multiple bytes, so character-only truncation can
        # reach the OK dialog and then be silently refused by Pokemon GO.
        if len(candidate.encode("utf-8")) > max_utf8_bytes:
            break
        prefix.append(cluster)
    if len(prefix) < min(2, len(clusters)):
        raise ValueError("nickname capacity cannot preserve the species prefix")
    return "".join(prefix) + suffix
