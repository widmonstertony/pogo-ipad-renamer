"""Inspect rename-field AX through SafeProxy; never clear, type or submit.

The optional open step requires three verified default-name detail frames.
The background worker must be stopped so there is only one device controller.
"""
import argparse
import json
import os
from pathlib import Path

from pogo_iphone_renamer import device_controller as base
from pogo_iphone_renamer.appraisal_agent import screen_snapshot
from pogo_iphone_renamer.background_batch_runner import background_run_is_active
from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.device_run_lock import DeviceRunLock
from pogo_iphone_renamer.gui import load_settings
from pogo_iphone_renamer.headless_batch_launcher import background_environment
from pogo_iphone_renamer.batch_agent import (
    _confirm_fresh_detail_identity, _restore_direct_detail_after_interrupted_appraisal,
)
from pogo_iphone_renamer.rename_dialog import open_dynamic_rename_from_detail
from pogo_iphone_renamer.rename_submission import (
    _focus_ocr_default_name_field, _cancel_unverified_input, RenameFieldVerificationUnavailable,
    AccessibilityRuntimeUnavailable, _ax_runtime_is_inactive,
)
from pogo_iphone_renamer.accessibility_names import exact_name_field
from pogo_iphone_renamer.appraisal_agent import Snapshot
from pogo_iphone_renamer.resilient_mcp import ResilientStreamableHTTPClient
from pogo_iphone_renamer.protocol import text_from_content
from pogo_iphone_renamer.server import SafeProxy

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--open-default-field", action="store_true")
parser.add_argument("--cancel-field", action="store_true")
args = parser.parse_args()
root = Path(__file__).resolve().parents[1]
if background_run_is_active(root / ".pogo-data" / "batch-state.json"):
    raise RuntimeError("Stop the background worker before diagnostic reads")
settings_ui = load_settings(root)
assert settings_ui.mcp_url.rstrip("/") == "http://192.168.68.84:8090/mcp"
os.environ.update(background_environment(root, settings_ui))
settings = Settings.from_env()
base.ORIENTATION = "STAGE_MANAGER_PORTRAIT_WINDOW"

with DeviceRunLock(root / ".pogo-data" / "iphone-mcp.lock"):
    proxy = SafeProxy(settings, ResilientStreamableHTTPClient(settings, timeout=20))
    print(text_from_content(proxy.call_tool("get_screen_info", {})), flush=True)
    snapshot = screen_snapshot(proxy)
    front = text_from_content(proxy.call_tool("get_frontmost_app", {}))
    if settings.pokemon_go_bundle_id.casefold() not in front.casefold():
        raise RuntimeError("Configured game is not foreground; no navigation")
    print("Current page:", base.local_page_state(snapshot), flush=True)
    if args.open_default_field:
        snapshot = _restore_direct_detail_after_interrupted_appraisal(proxy, snapshot)
        confirmed = _confirm_fresh_detail_identity(proxy, snapshot)
        if confirmed is None:
            raise RuntimeError("Default identity could not be verified; no field opened")
        snapshot, name = confirmed
        if not name.is_default or not name.species:
            raise RuntimeError("Existing/uncertain nickname: diagnostic must not open it")
        snapshot = open_dynamic_rename_from_detail(proxy, snapshot, name.species)
        try:
            _focus_ocr_default_name_field(proxy, name.species)
        except AccessibilityRuntimeUnavailable:
            try:
                _cancel_unverified_input(proxy, name.species)
            except RenameFieldVerificationUnavailable:
                pass
            raise
        snapshot = screen_snapshot(proxy)
    if base.local_page_state(snapshot) != "RENAME_DIALOG":
        raise RuntimeError("No verified rename dialog; no field data collected")
    if args.cancel_field:
        try:
            _cancel_unverified_input(proxy, "")
        except RenameFieldVerificationUnavailable as cancelled:
            base._validate_expected("DETAIL", cancelled.snapshot)
            print("Diagnostic dialog cancelled; verified DETAIL, no text was submitted.", flush=True)
        raise SystemExit(0)
    results = {"description": snapshot.text}
    try:
        for attempt in range(1, 4):
            proxy.client.reset_read_session()
            key = f"fresh_full_tree={attempt}"
            results[key] = proxy.call_tool("get_ui_elements", {
                "visible_only": False, "clickable_only": False, "limit": 160, "debug": True,
            })
            print(key, "runtime_inactive:", _ax_runtime_is_inactive(results[key]), flush=True)
            try:
                print("EXACT FIELD:", repr(exact_name_field(Snapshot(text_from_content(results[key]), None))), flush=True)
            except Exception as exc:
                print("FIELD UNREADABLE:", str(exc), flush=True)
        destination = root / ".pogo-data" / "rename-ax-diagnostic.json"
        destination.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(destination, flush=True)
    finally:
        if args.open_default_field:
            try:
                _cancel_unverified_input(proxy, name.species)
            except RenameFieldVerificationUnavailable as cancelled:
                base._validate_expected("DETAIL", cancelled.snapshot)
                print("Diagnostic cancelled; same DETAIL preserved, no input or submit.", flush=True)
