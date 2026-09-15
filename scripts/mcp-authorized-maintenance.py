"""MCP-only maintenance console. Requires explicit user maintenance authorization.

This is separate from the Pokémon worker's SafeProxy and never used by that worker.
"""
import argparse
import base64
import json
import os
from pathlib import Path

from pogo_iphone_renamer.background_batch_runner import background_run_is_active
from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.device_run_lock import DeviceRunLock
from pogo_iphone_renamer.gui import load_settings
from pogo_iphone_renamer.headless_batch_launcher import background_environment
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("tool")
parser.add_argument("arguments", nargs="?", default="{}")
args = parser.parse_args()
allowed = {"describe_screen", "get_screen_info", "screenshot", "ocr_screen", "get_ui_elements", "get_frontmost_app", "get_device_info", "get_app_info", "tap_element", "tap_screen", "input_text", "type_text", "press_key", "open_url", "launch_app", "install_app", "run_command", "wake_and_home"}
assert args.tool in allowed
root = Path(__file__).resolve().parents[1]
if background_run_is_active(root / ".pogo-data/batch-state.json"):
    raise RuntimeError("Background worker must be stopped")
os.environ.update(background_environment(root, load_settings(root)))
settings = Settings.from_env()
assert settings.mcp_url == "http://192.168.68.84:8090/mcp"
with DeviceRunLock(root / ".pogo-data/iphone-mcp.lock"):
    client = ResilientStreamableHTTPClient(settings, timeout=25)
    reads = {"describe_screen", "get_screen_info", "screenshot", "ocr_screen", "get_ui_elements", "get_frontmost_app", "get_device_info", "get_app_info", "wake_and_home"}
    if args.tool not in reads:
        state = client.call_tool("get_screen_info", {}).get("structuredContent", {})
        if state.get("locked") is not False or state.get("screen_on") is not True:
            raise RuntimeError("No positive unlocked/screen-on evidence; maintenance action refused")
    response = client.call_tool(args.tool, json.loads(args.arguments))
    if args.tool == "screenshot":
        block = next(c for c in response["content"] if c.get("type") == "image")
        path = root / ".pogo-data/mcp-maintenance-current.jpg"
        path.write_bytes(base64.b64decode(block["data"]))
        print(path)
    else:
        data = response.get("structuredContent", response)
        print(json.dumps(data, ensure_ascii=False), flush=True)
        if response.get("isError"):
            raise SystemExit(1)
