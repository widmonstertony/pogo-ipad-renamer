from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image

from pogo_iphone_renamer.landscape_cv import _is_attack_fill, _is_gold_fill
from pogo_iphone_renamer.landscape_cv_v3 import _endpoint_value
from pogo_iphone_renamer.landscape_cv_v4 import _select_track_rows
from pogo_iphone_renamer.landscape_cv_v5 import _row_consensus_endpoint


def _runs(values: list[int]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    for value in values:
        if not result or value > result[-1][1] + 1:
            result.append((value, value))
        else:
            result[-1] = (result[-1][0], value)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+")
    args = parser.parse_args()
    for name in args.paths:
        path = Path(name)
        image = Image.open(path).convert("RGB")
        try:
            rows = _select_track_rows(image)
            values = (
                _endpoint_value(image, rows[0], _is_attack_fill),
                _endpoint_value(image, rows[1], _is_gold_fill),
                _endpoint_value(image, rows[2], _is_gold_fill),
            )
            print(path, image.size, rows, values)
            robust = (
                _row_consensus_endpoint(image, rows[0], _is_attack_fill),
                _row_consensus_endpoint(image, rows[1], _is_gold_fill),
                _row_consensus_endpoint(image, rows[2], _is_gold_fill),
            )
            print("  robust", robust)
            for row, predicate in zip(
                rows, (_is_attack_fill, _is_gold_fill, _is_gold_fill)
            ):
                colored = [
                    x
                    for x in range(image.width)
                    if predicate(image.getpixel((x, row)))
                ]
                relevant = [
                    run
                    for run in _runs(colored)
                    if run[1] >= round(image.width * 0.05)
                    and run[0] <= round(image.width * 0.45)
                ]
                print(" ", row, relevant)
                row_endpoints = []
                for y in range(max(0, row - 3), min(image.height, row + 4)):
                    candidates = [
                        x
                        for x in range(round(image.width * 0.05), round(image.width * 0.45))
                        if predicate(image.getpixel((x, y)))
                    ]
                    row_endpoints.append(max(candidates) if candidates else None)
                print("   endpoints", row_endpoints)
        except Exception as exc:
            print(path, image.size, type(exc).__name__, exc)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
