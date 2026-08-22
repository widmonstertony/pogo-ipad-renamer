from __future__ import annotations

import base64
import re

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


PATHS = (
    "/var/jb/Library/PreferenceBundles/iosmcpprefs.bundle/iosmcpprefs",
    "/var/jb/Library/MobileSubstrate/DynamicLibraries/ios-mcp.dylib",
)
TERMS = (
    b"com.witchan",
    b"debug",
    b"log",
    b"darwin",
    b"preferences",
    b"startserver",
    b"stopserver",
)


def main() -> int:
    client = ResilientStreamableHTTPClient(Settings.from_env(), timeout=120)
    for path in PATHS:
        result = client.call_tool(
            "read_file", {"path": path, "binary": True, "max_bytes": 4_194_304}
        )
        binary = base64.b64decode(
            result.get("structuredContent", {}).get("content", "")
        )
        strings = {
            value.decode("utf-8", "ignore")
            for value in re.findall(rb"[ -~]{4,}", binary)
            if any(term in value.lower() for term in TERMS)
        }
        print(f"\n{path}")
        print("\n".join(sorted(strings)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
