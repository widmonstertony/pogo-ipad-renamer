from __future__ import annotations

import argparse
import base64
from pathlib import Path

from PIL import Image

from pogo_iphone_renamer.ipad_landscape_agent import ANCHORS
from pogo_iphone_renamer import ipad_landscape_agent as base
from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.ipad_landscape_agent_v14 import robust_page_state
from pogo_iphone_renamer.landscape_cv import (
    stage_manager_geometry,
    stage_manager_upright_ratio_to_touch,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("image", type=Path)
    parser.add_argument("--touch-width", type=float, default=1366)
    parser.add_argument("--touch-height", type=float, default=1024)
    args = parser.parse_args()
    image = Image.open(args.image).convert("RGB")
    geometry = stage_manager_geometry(image, use_preferred=False)
    print(geometry)
    for key in ("MAP", "MAIN_MENU", "INVENTORY"):
        x_ratio, y_ratio, label, expected = ANCHORS[key]
        touch = stage_manager_upright_ratio_to_touch(
            geometry,
            args.touch_width,
            args.touch_height,
            x_ratio,
            y_ratio,
        )
        print(key, label, "->", expected, "touch=", touch)
    encoded = base64.b64encode(args.image.read_bytes()).decode("ascii")
    snapshot = Snapshot("", encoded)
    print("bright_fraction", base._bright_fraction(snapshot))
    print("local_page_state", base.local_page_state(snapshot))
    print("robust_page_state", robust_page_state(snapshot))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
