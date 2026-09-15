"""Read-only MCP maintenance diagnostics after explicit device-maintenance consent."""
import json
import os
from pathlib import Path

from pogo_iphone_renamer.background_batch_runner import background_run_is_active
from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.device_run_lock import DeviceRunLock
from pogo_iphone_renamer.gui import load_settings
from pogo_iphone_renamer.headless_batch_launcher import background_environment
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient

root = Path(__file__).resolve().parents[1]
if background_run_is_active(root / ".pogo-data/batch-state.json"):
    raise RuntimeError("Background worker must be stopped for maintenance")
os.environ.update(background_environment(root, load_settings(root)))
settings = Settings.from_env()
assert settings.mcp_url.rstrip("/") == "http://192.168.68.84:8090/mcp"
with DeviceRunLock(root / ".pogo-data/iphone-mcp.lock"):
    client = ResilientStreamableHTTPClient(settings, timeout=25)
    for name, args in [
        ("describe_screen", {}),
        ("get_screen_info", {}),
        ("get_device_info", {"debug": True}),
        ("run_command", {"command": "id; dpkg-query -s com.witchan.ios-mcp; dpkg --print-architecture", "timeout": 15}),
        ("run_command", {"command": "ps -A -o pid,ppid,comm | grep -E 'SpringBoard|backboardd|Accessibility|accessibility|assistive|voiceover|Pok.mon'", "timeout": 15}),
        ("run_command", {"command": "tail -n 120 /var/mobile/Library/Logs/iOSMCP/ios-mcp.log", "timeout": 15}),
    ]:
        response = client.call_tool(name, args)
        print(json.dumps({"tool": name, "args": args, "response": response}, ensure_ascii=False), flush=True)
        if name == "get_screen_info":
            state = response.get("structuredContent", {})
            if not state:
                state = json.loads(response["content"][0]["text"])
            if state.get("locked") or state.get("screen_on") is False:
                raise RuntimeError("Device locked/off; no maintenance commands sent")
