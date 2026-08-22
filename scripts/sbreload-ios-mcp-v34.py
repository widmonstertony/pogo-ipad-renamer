from __future__ import annotations

import time

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent import tool_result_message
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


COMMAND = (
    "/var/jb/usr/bin/sh -c '"
    "(sleep 1; /var/jb/usr/bin/sbreload) "
    ">/tmp/ios-mcp-sbreload.log 2>&1 &'"
)


def main() -> int:
    settings = Settings.from_env()
    client = ResilientStreamableHTTPClient(settings, timeout=120)
    client.call_tool("run_command", {"command": COMMAND, "timeout": 10})

    saw_offline = False
    last_error: Exception | None = None
    started = time.monotonic()
    while time.monotonic() - started < 90:
        time.sleep(1.0)
        try:
            probe = ResilientStreamableHTTPClient(settings, timeout=120)
            health = probe.health()
            if not saw_offline and time.monotonic() - started < 4:
                continue
            tools = probe.list_tools()
            screen = tool_result_message(
                "get_screen_info", probe.call_tool("get_screen_info", {})
            )
            print(
                f"recovered health={health} tools={len(tools)} "
                f"saw_offline={saw_offline} screen={screen.get('content', '')}",
                flush=True,
            )
            return 0
        except Exception as exc:
            saw_offline = True
            last_error = exc
    raise RuntimeError(f"ios-mcp did not return after sbreload: {last_error}")


if __name__ == "__main__":
    raise SystemExit(main())
