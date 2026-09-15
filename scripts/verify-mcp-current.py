"""Read the connected iPad through SafeProxy and save a current diagnostic frame."""
import base64
import os
import sys
import json
from pathlib import Path

from pogo_iphone_renamer.headless_batch_launcher import background_environment
from pogo_iphone_renamer.gui import load_settings
from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.resilient_mcp import ResilientStreamableHTTPClient
from pogo_iphone_renamer.server import SafeProxy
from pogo_iphone_renamer.protocol import text_from_content
from pogo_iphone_renamer.appraisal_agent import screen_snapshot

root = Path(__file__).resolve().parents[1]
os.environ.update(background_environment(root, load_settings(root)))
settings = Settings.from_env()
client = ResilientStreamableHTTPClient(settings, timeout=20)
proxy = SafeProxy(settings, client)
print(client.health(), flush=True)
print(text_from_content(proxy.call_tool('get_screen_info', {})), flush=True)
snapshot = screen_snapshot(proxy)
print('Current frame captured.', flush=True)
if snapshot.image:
    destination = root / '.pogo-data' / 'mcp-current-verification.jpg'
    destination.write_bytes(base64.b64decode(snapshot.image))
    print(destination, flush=True)
if '--open-menu' in sys.argv:
    from pogo_iphone_renamer import device_controller as base
    from pogo_iphone_renamer.name_recognition import analyze_name_region
    base.ORIENTATION = 'STAGE_MANAGER_PORTRAIT_WINDOW'
    state = base.local_page_state(snapshot)
    name = analyze_name_region(snapshot.image, base.ORIENTATION)
    print('Verified before touch:', state, name, flush=True)
    assert state == 'DETAIL' and name.species == '噴嚏熊', (state, name)
    assert proxy.observation and proxy.observation.width == 1366 and proxy.observation.height == 1024
    result = proxy.call_tool('tap_screen', {
        # HIDManager divides by reported 1366x1024 bounds, while the
        # physical digitizer and current JPEG use portrait 1024x1366.
        'x': 96 * 1366 / 1024, 'y': 973 * 1024 / 1366,
        '_observation_token': proxy.observation.token,
        '_intent': 'navigate visible Pokemon detail more menu',
        '_expected_after': 'Pokemon GO DETAIL_MENU with Appraise visible',
    })
    print(text_from_content(result), flush=True)
    after = screen_snapshot(proxy)
    print('After touch:', base.local_page_state(after), flush=True)
    destination.write_bytes(base64.b64decode(after.image))
