from __future__ import annotations

import time

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent import tool_result_message
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


RESET_COMMAND = (
    "/var/jb/usr/bin/sh -c '"
    "(sleep 1; "
    "/var/jb/usr/bin/notifyutil -p com.witchan.ios-mcp.control/stop; "
    "sleep 2; "
    "/var/jb/usr/bin/notifyutil -p com.witchan.ios-mcp.control/start) "
    ">/tmp/ios-mcp-capture-reset.log 2>&1 &'"
)


def main() -> int:
    settings = Settings.from_env()
    client = ResilientStreamableHTTPClient(settings, timeout=120)
    result = client.call_tool(
        "run_command", {"command": RESET_COMMAND, "timeout": 10}
    )
    message = tool_result_message("run_command", result)
    print(f"scheduled={message.get('content', '')}", flush=True)

    last_error: Exception | None = None
    for attempt in range(1, 31):
        time.sleep(1.0)
        try:
            probe = ResilientStreamableHTTPClient(settings, timeout=120)
            health = probe.health()
            tools = probe.list_tools()
            screen = tool_result_message(
                "get_screen_info", probe.call_tool("get_screen_info", {})
            )
            print(
                f"recovered_attempt={attempt} health={health} "
                f"tools={len(tools)} screen={screen.get('content', '')}",
                flush=True,
            )
            return 0
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"ios-mcp did not recover after reset: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
