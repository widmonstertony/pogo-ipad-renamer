from __future__ import annotations

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent import tool_result_message
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


def main() -> int:
    client = ResilientStreamableHTTPClient(Settings.from_env(), timeout=120)
    for command in (
        "command -v notifyutil; command -v sh; id",
        "find /usr /var/jb/usr -type f -name notifyutil 2>/dev/null || true",
        "find /usr /var/jb/usr -type f \\\n+          \\( -name '*notify*' -o -name '*darwin*' \\) 2>/dev/null | head -100",
        "ps -A -o pid,ppid,command | grep -i '[i]os-mcp' || true",
    ):
        result = client.call_tool("run_command", {"command": command, "timeout": 10})
        message = tool_result_message("run_command", result)
        print(f"$ {command}\n{message.get('content', '')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
