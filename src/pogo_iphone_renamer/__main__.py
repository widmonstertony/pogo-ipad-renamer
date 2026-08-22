from __future__ import annotations

import json
import sys

from .config import Settings
from .policy import READ_TOOLS, WRITE_TOOLS
from .server import build_server
from .upstream import StreamableHTTPClient


def doctor(settings: Settings) -> int:
    client = StreamableHTTPClient(settings)
    health = client.health()
    tools = client.list_tools()
    names = {str(tool.get("name")) for tool in tools}
    report = {
        "health": health,
        "mcp_url": settings.mcp_url,
        "write_enabled": settings.write_enabled,
        "batch_limit": settings.batch_limit,
        "pokemon_go_bundle_id": settings.pokemon_go_bundle_id,
        "allowed_read_tools_present": sorted(names & READ_TOOLS),
        "allowed_write_tools_present": sorted(names & WRITE_TOOLS),
        "upstream_tool_count": len(names),
        "exposed_tool_count": len(names & (READ_TOOLS | WRITE_TOOLS)) + 3,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "serve"
    settings = Settings.from_env()
    if command == "doctor":
        return doctor(settings)
    if command == "serve":
        build_server(settings).serve()
        return 0
    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

