from __future__ import annotations

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent import tool_result_message
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


COMMANDS = (
    "for x in screencapture screenshot screendump pngcrush ffmpeg; do "
    "printf '%s=' \"$x\"; command -v \"$x\" || true; done",
    "dpkg-query -W -f='${Package} ${Version}\\n' 2>/dev/null | "
    "grep -Ei 'screen|capture|record|ffmpeg' || true",
)


def main() -> int:
    client = ResilientStreamableHTTPClient(Settings.from_env(), timeout=120)
    for command in COMMANDS:
        result = client.call_tool("run_command", {"command": command, "timeout": 20})
        message = tool_result_message("run_command", result)
        print(f"$ {command}\n{message.get('content', '')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
