from __future__ import annotations

import argparse
import time

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


def unlocked(client: ResilientStreamableHTTPClient) -> bool:
    result = client.call_tool("get_screen_info", {})
    structured = result.get("structuredContent")
    if not isinstance(structured, dict):
        return False
    nested = structured.get("device_state")
    state = nested if isinstance(nested, dict) else structured
    return not bool(state.get("locked", True)) and bool(state.get("screen_on", False))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    print("WAITING_FOR_MANUAL_UNLOCK", flush=True)
    while True:
        try:
            client = ResilientStreamableHTTPClient(settings, timeout=120)
            if unlocked(client):
                break
        except Exception:
            pass
        time.sleep(1.0)

    print("UNLOCKED_INSTALLING", flush=True)
    client.call_tool("install_app", {"path": args.path})
    deadline = time.monotonic() + 120.0
    while time.monotonic() < deadline:
        try:
            client = ResilientStreamableHTTPClient(settings, timeout=120)
            health = client.health()
            if health.get("version") == args.version:
                print(f"INSTALLED_VERSION={args.version}", flush=True)
                print("WAITING_FOR_FINAL_MANUAL_UNLOCK", flush=True)
                while not unlocked(client):
                    time.sleep(1.0)
                    client = ResilientStreamableHTTPClient(settings, timeout=120)
                print("FINAL_UNLOCK_DETECTED", flush=True)
                return 0
        except Exception:
            pass
        time.sleep(1.0)
    raise RuntimeError(f"ios-mcp did not report {args.version} after installation")


if __name__ == "__main__":
    raise SystemExit(main())
