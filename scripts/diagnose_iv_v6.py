from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pogo_iphone_renamer.landscape_cv_v4 import _select_track_rows
from pogo_iphone_renamer.landscape_cv_v5 import (
    _detected_bar_geometry,
    _is_any_iv_fill,
    _row_consensus_endpoint,
)
from pogo_iphone_renamer.landscape_cv_v6 import _track_component_at


def inspect(path: Path) -> None:
    image = Image.open(path).convert("RGB")
    rows = _select_track_rows(image)
    geometry = _detected_bar_geometry(image, rows)[:2]
    x_start, full_endpoint = geometry
    unit = (full_endpoint - x_start + 1.0) / 15.0
    print(path.name, "rows", rows, "geometry", geometry, "unit", unit)
    for row in rows:
        components = [
            _track_component_at(image, y, x_start + unit * 1.5)
            for y in range(max(0, row - 2), min(image.height, row + 3))
        ]
        last_components = [
            _track_component_at(image, y, x_start + unit * 14.5)
            for y in range(max(0, row - 2), min(image.height, row + 3))
        ]
        scores: list[float] = []
        for cell in range(15):
            center_x = x_start + (cell + 0.5) * unit
            x_radius = max(1, round(unit * 0.12))
            samples = [
                _is_any_iv_fill(image.getpixel((x, y)))
                for y in range(max(0, row - 2), min(image.height - 1, row + 2) + 1)
                for x in range(
                    max(0, round(center_x) - x_radius),
                    min(image.width - 1, round(center_x) + x_radius) + 1,
                )
            ]
            scores.append(sum(samples) / len(samples))
        print(
            " row",
            row,
            "endpoint",
            _row_consensus_endpoint(
                image, row, _is_any_iv_fill, geometry=geometry
            )[:2],
            "components",
            components,
            "last_components",
            last_components,
            "scores",
            " ".join(f"{score:.2f}" for score in scores),
        )


for argument in sys.argv[1:]:
    inspect(Path(argument))
