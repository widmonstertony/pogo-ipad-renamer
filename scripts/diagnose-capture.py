from __future__ import annotations

import base64
import io
import json
import time

from PIL import Image

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent import tool_result_message
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


def main() -> int:
    client = ResilientStreamableHTTPClient(Settings.from_env(), timeout=120)
    recovery_tools = [
        tool
        for tool in client.list_tools()
        if tool.get("name") in {
            "wake_and_home",
            "launch_app",
            "kill_app",
            "screenshot",
            "get_syslog",
        }
    ]
    tool_names = sorted(str(tool.get("name")) for tool in client.list_tools())
    print(f"tool_names={json.dumps(tool_names, ensure_ascii=False)}", flush=True)
    print(f"recovery_tools={json.dumps(recovery_tools, ensure_ascii=False)}", flush=True)
    screen_info = tool_result_message(
        "get_screen_info", client.call_tool("get_screen_info", {})
    )
    frontmost = tool_result_message(
        "get_frontmost_app", client.call_tool("get_frontmost_app", {})
    )
    print(f"screen_info={screen_info.get('content', '')}", flush=True)
    print(f"frontmost={frontmost.get('content', '')}", flush=True)
    for index in range(1, 5):
        time.sleep(10)
        shot = tool_result_message(
            "screenshot", client.call_tool("screenshot", {"debug": True})
        )
        description = tool_result_message(
            "describe_screen",
            client.call_tool(
                "describe_screen",
                {
                    "include_screenshot": True,
                    "include_ocr": True,
                    "clickable_only": False,
                },
            ),
        )
        images = shot.get("images", [])
        image = (
            Image.open(io.BytesIO(base64.b64decode(images[-1]))).convert("RGB")
            if images
            else None
        )
        description_images = description.get("images", [])
        description_image = (
            Image.open(io.BytesIO(base64.b64decode(description_images[-1]))).convert("RGB")
            if description_images
            else None
        )
        print(
            f"{index * 10}s extrema={image.getextrema() if image else None} "
            f"describe_extrema={description_image.getextrema() if description_image else None} "
            f"screenshot_debug={shot.get('content', '')} "
            f"text={description.get('content', '')}",
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
