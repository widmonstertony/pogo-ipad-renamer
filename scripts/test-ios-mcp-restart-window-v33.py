from __future__ import annotations

import base64
import io
import time

from PIL import Image

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent import tool_result_message
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


COMMAND = (
    "/var/jb/usr/bin/sh -c '"
    "(date > /tmp/ios-mcp-restart-window.log; sleep 2; "
    "/var/jb/usr/bin/notifyutil -p com.witchan.ios-mcp.control/stop; "
    "echo stop=$? >> /tmp/ios-mcp-restart-window.log; sleep 8; "
    "/var/jb/usr/bin/notifyutil -p com.witchan.ios-mcp.control/start; "
    "echo start=$? >> /tmp/ios-mcp-restart-window.log; date >> /tmp/ios-mcp-restart-window.log) "
    ">>/tmp/ios-mcp-restart-window.log 2>&1 &'"
)


def main() -> int:
    settings = Settings.from_env()
    client = ResilientStreamableHTTPClient(settings, timeout=120)
    client.call_tool("run_command", {"command": COMMAND, "timeout": 10})
    states: list[tuple[float, bool]] = []
    started = time.monotonic()
    while time.monotonic() - started < 16:
        try:
            ResilientStreamableHTTPClient(settings, timeout=120).health()
            alive = True
        except Exception:
            alive = False
        states.append((round(time.monotonic() - started, 2), alive))
        time.sleep(0.25)
    print(f"health_states={states}", flush=True)

    probe = ResilientStreamableHTTPClient(settings, timeout=120)
    info = tool_result_message("get_screen_info", probe.call_tool("get_screen_info", {}))
    shot = tool_result_message("screenshot", probe.call_tool("screenshot", {}))
    images = shot.get("images", [])
    image = (
        Image.open(io.BytesIO(base64.b64decode(images[-1]))).convert("RGB")
        if images
        else None
    )
    log = tool_result_message(
        "run_command",
        probe.call_tool(
            "run_command",
            {"command": "cat /tmp/ios-mcp-restart-window.log", "timeout": 10},
        ),
    )
    print(f"screen={info.get('content', '')}", flush=True)
    print(
        f"image_size={image.size if image else None} "
        f"extrema={image.getextrema() if image else None}",
        flush=True,
    )
    print(f"device_log={log.get('content', '')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
