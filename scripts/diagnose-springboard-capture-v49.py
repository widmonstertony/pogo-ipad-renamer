from __future__ import annotations

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent import tool_result_message
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


COMMANDS = (
    "ps -A -o pid,ppid,rss,vsz,command | grep -E '[S]pringBoard|[b]ackboardd'",
    "vm_stat",
    "ulimit -a",
    "launchctl procinfo $(pgrep -x SpringBoard | head -1) 2>&1 | head -160",
    "find /var/mobile/Library/Logs/CrashReporter -maxdepth 1 -type f "
    "\\( -iname 'SpringBoard*' -o -iname 'JetsamEvent*' \\) -print | tail -20",
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
