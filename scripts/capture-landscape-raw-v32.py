from __future__ import annotations

import base64
import io
from pathlib import Path

from PIL import Image

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent import tool_result_message
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


def main() -> int:
    client = ResilientStreamableHTTPClient(Settings.from_env(), timeout=120)
    screen = tool_result_message(
        "get_screen_info", client.call_tool("get_screen_info", {})
    )
    shot = tool_result_message("screenshot", client.call_tool("screenshot", {}))
    images = shot.get("images", [])
    if not images:
        raise RuntimeError("MCP did not return a screenshot")
    image = Image.open(io.BytesIO(base64.b64decode(images[-1]))).convert("RGB")
    output = Path(__file__).resolve().parents[1] / ".pogo-data" / "landscape-raw-v32.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, format="PNG")
    print(f"screen={screen.get('content', '')}")
    print(f"image_size={image.size} extrema={image.getextrema()}")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
