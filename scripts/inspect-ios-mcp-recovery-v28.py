from __future__ import annotations

import base64
import json

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


def main() -> int:
    client = ResilientStreamableHTTPClient(Settings.from_env(), timeout=120)
    wanted = {"run_command", "read_file", "get_syslog"}
    schemas = [tool for tool in client.list_tools() if tool.get("name") in wanted]
    print(json.dumps(schemas, ensure_ascii=False, indent=2), flush=True)

    for path in (
        "/var/mobile/Library/Logs/iOSMCP/ios-mcp.log",
        "/var/mobile/Library/Logs/iOSMCP/ios-mcp.1.log",
    ):
        try:
            result = client.call_tool(
                "read_file", {"path": path, "binary": True, "max_bytes": 262_144}
            )
            encoded = result.get("structuredContent", {}).get("content", "")
            content = base64.b64decode(encoded).decode("utf-8", errors="replace")
            print(f"\n--- {path} (tail) ---", flush=True)
            print(content[-32_000:], flush=True)
        except Exception as exc:
            print(f"\n--- {path}: {exc} ---", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
