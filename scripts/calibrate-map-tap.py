from __future__ import annotations

import argparse
import base64
import time
from pathlib import Path

from pogo_iphone_renamer import ipad_landscape_agent as base
from pogo_iphone_renamer.appraisal_agent import screen_snapshot
from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.device_run_lock import DeviceRunLock
from pogo_iphone_renamer.ipad_landscape_agent_v14 import robust_page_state
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient
from pogo_iphone_renamer.server import SafeProxy


def _save(snapshot, path: Path) -> None:
    if not snapshot.image:
        raise RuntimeError("snapshot image missing")
    path.write_bytes(base64.b64decode(snapshot.image))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--anchor",
        choices=("MAP", "MAIN_MENU", "INVENTORY", "DETAIL_CLOSE"),
        default="MAP",
    )
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    settings = Settings.from_env()
    with DeviceRunLock(settings.journal_path.parent / "iphone-mcp.lock"):
        client = ResilientStreamableHTTPClient(settings, timeout=120.0)
        proxy = SafeProxy(settings, client)
        before = screen_snapshot(proxy)
        state_before = robust_page_state(before)
        expected_before = "DETAIL" if args.anchor == "DETAIL_CLOSE" else args.anchor
        if state_before != expected_before:
            raise RuntimeError(f"expected {expected_before}, got {state_before}")
        before = base._ensure_stage_geometry_for_state(
            proxy, before, expected_before, state_reader=robust_page_state
        )
        geometry = base.current_stage_geometry(proxy)
        observation = proxy.observation
        if observation is None or observation.width is None or observation.height is None:
            raise RuntimeError("observation bounds missing")
        x_ratio, y_ratio, _label, expected = base.ANCHORS[args.anchor]
        x, y = base.upright_ratio_to_touch(
            observation.width,
            observation.height,
            x_ratio,
            y_ratio,
            geometry=geometry,
        )
        _save(before, args.output / "before.png")
        print("geometry", geometry, flush=True)
        print(f"tap=({x:.2f},{y:.2f}) state={state_before}", flush=True)
        base._tap(proxy, args.anchor)
        time.sleep(2.5)
        after = screen_snapshot(proxy)
        _save(after, args.output / "after.png")
        state_after = robust_page_state(after)
        print(f"after={state_after}", flush=True)
        reached = state_after == expected or (
            expected == "MAIN_MENU" and state_after == "INVENTORY"
        )
        return 0 if reached else 2


if __name__ == "__main__":
    raise SystemExit(main())
