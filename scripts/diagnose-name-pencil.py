from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

os.environ.setdefault("IPHONE_MCP_URL", "http://127.0.0.1:8090/mcp")
os.environ.setdefault("IPHONE_MCP_HEALTH_URL", "http://127.0.0.1:8090/health")
os.environ.setdefault("POKEMON_GO_BUNDLE_ID", "com.nianticlabs.pokemongo")

import numpy as np  # noqa: E402

from pogo_iphone_renamer.config import Settings  # noqa: E402
from pogo_iphone_renamer.landscape_cv import rotate_mcp_image_upright  # noqa: E402
from pogo_iphone_renamer.local_ocr import _engine  # noqa: E402
from pogo_iphone_renamer.native_agent import tool_result_message  # noqa: E402
from pogo_iphone_renamer.native_agent_v2 import (  # noqa: E402
    ResilientStreamableHTTPClient,
)


def main() -> int:
    settings = Settings.from_env()
    client = ResilientStreamableHTTPClient(settings, timeout=120.0)
    raw = client.call_tool("screenshot", {})
    message = tool_result_message("screenshot", raw)
    images = message.get("images")
    if not isinstance(images, list) or not images:
        raise RuntimeError("MCP did not return a screenshot")
    upright = rotate_mcp_image_upright(
        str(images[-1]), "ROTATED_90_COUNTERCLOCKWISE"
    )
    result = _engine()(np.asarray(upright.convert("RGB")))
    for text, score, box in zip(
        tuple(result.txts or ()),
        tuple(result.scores or ()),
        tuple(result.boxes or ()),
    ):
        clean = str(text).strip()
        if clean:
            print(f"{float(score):.4f}\t{clean}\t{np.asarray(box).tolist()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
