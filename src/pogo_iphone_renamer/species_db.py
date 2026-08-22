from __future__ import annotations

import csv
import sys
import unicodedata
from functools import lru_cache
from pathlib import Path


ZH_HANT_LANGUAGE_ID = "4"


def _data_candidates() -> list[Path]:
    candidates: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        candidates.append(Path(frozen_root) / "data" / "pokemon_species_names.csv")
    candidates.append(Path(__file__).resolve().parents[2] / "data" / "pokemon_species_names.csv")
    candidates.append(Path.cwd() / "data" / "pokemon_species_names.csv")
    return candidates


def species_data_path() -> Path:
    for candidate in _data_candidates():
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("找不到本地繁中物种表 pokemon_species_names.csv")


@lru_cache(maxsize=1)
def traditional_chinese_species() -> frozenset[str]:
    names: set[str] = set()
    with species_data_path().open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("local_language_id") != ZH_HANT_LANGUAGE_ID:
                continue
            name = unicodedata.normalize("NFC", str(row.get("name", ""))).strip()
            if name:
                names.add(name)
    if len(names) < 1000:
        raise ValueError(f"本地繁中物种表不完整：仅 {len(names)} 条")
    return frozenset(names)


def exact_default_species_name(current_name: str) -> str | None:
    normalized = unicodedata.normalize("NFC", current_name).strip()
    return normalized if normalized in traditional_chinese_species() else None
