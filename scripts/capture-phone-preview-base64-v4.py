from __future__ import annotations

import base64
import io
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

os.environ.setdefault("IPHONE_MCP_URL", "http://192.168.68.61:8090/mcp")
os.environ.setdefault("IPHONE_MCP_HEALTH_URL", "http://192.168.68.61:8090/health")
os.environ.setdefault("POKEMON_GO_BUNDLE_ID", "com.nianticlabs.pokemongo")

from pogo_iphone_renamer.config import Settings  # noqa: E402
from pogo_iphone_renamer.landscape_cv import rotate_mcp_image_upright  # noqa: E402
from pogo_iphone_renamer.native_agent import tool_result_message  # noqa: E402
from pogo_iphone_renamer.native_agent_v2 import (  # noqa: E402
    ResilientStreamableHTTPClient,
)


def main() -> int:
    client = ResilientStreamableHTTPClient(Settings.from_env(), timeout=120.0)
    result = tool_result_message("screenshot", client.call_tool("screenshot", {}))
    images = result.get("images")
    if not isinstance(images, list) or not images:
        raise RuntimeError("MCP did not return screenshot")
    image = rotate_mcp_image_upright(
        str(images[-1]), "ROTATED_90_COUNTERCLOCKWISE"
    )
    image.thumbnail((1000, 750))
    output = io.BytesIO()
    image.save(output, format="JPEG", quality=72, optimize=True)
    print(base64.b64encode(output.getvalue()).decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
