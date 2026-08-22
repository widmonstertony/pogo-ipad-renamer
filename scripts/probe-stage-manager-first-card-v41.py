from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from pogo_iphone_renamer.appraisal_agent import screen_snapshot
from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient
from pogo_iphone_renamer.protocol import text_from_content
from pogo_iphone_renamer.server import SafeProxy


def main() -> int:
    settings = Settings.from_env()
    proxy = SafeProxy(settings, ResilientStreamableHTTPClient(settings, timeout=120))
    screen_snapshot(proxy)
    front = proxy.call_tool("get_frontmost_app", {})
    if settings.pokemon_go_bundle_id.casefold() not in text_from_content(front).casefold():
        raise RuntimeError("Pokemon GO is not foreground")
    if proxy.observation is None:
        raise RuntimeError("missing safe observation")
    proxy.call_tool(
        "tap_screen",
        {
            "x": 792,
            "y": 197,
            "_observation_token": proxy.observation.token,
            "_intent": "navigate calibrated Stage Manager first Pokemon card",
            "_expected_after": "Pokemon GO DETAIL",
        },
    )
    after = screen_snapshot(proxy)
    if after.image:
        output = Path(__file__).resolve().parents[1] / ".pogo-data" / "stage-manager-after-card-v41.png"
        output.parent.mkdir(parents=True, exist_ok=True)
        Image.open(io.BytesIO(base64.b64decode(after.image))).save(output)
        print(output)
    print(after.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
