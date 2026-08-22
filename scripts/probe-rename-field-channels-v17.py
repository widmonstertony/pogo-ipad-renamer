from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

os.environ.setdefault("IPHONE_MCP_URL", "http://127.0.0.1:8090/mcp")
os.environ.setdefault("IPHONE_MCP_HEALTH_URL", "http://127.0.0.1:8090/health")
os.environ.setdefault("POKEMON_GO_BUNDLE_ID", "com.nianticlabs.pokemongo")
os.environ.setdefault("POGO_WRITE_ENABLED", "true")
os.environ.setdefault("POGO_BATCH_LIMIT", "1")
os.environ.setdefault("POGO_OBSERVATION_TTL_SECONDS", "120")
os.environ.setdefault(
    "POGO_JOURNAL_PATH", str(ROOT / ".pogo-data" / "actions.jsonl")
)

from pogo_iphone_renamer import ipad_landscape_agent as base  # noqa: E402
from pogo_iphone_renamer import ipad_landscape_agent_v14 as v14  # noqa: E402
from pogo_iphone_renamer.appraisal_agent import Snapshot, screen_snapshot  # noqa: E402
from pogo_iphone_renamer.config import Settings  # noqa: E402
from pogo_iphone_renamer.device_run_lock import DeviceRunLock  # noqa: E402
from pogo_iphone_renamer.ipad_landscape_agent_v10 import (  # noqa: E402
    _mark_rename_observation,
)
from pogo_iphone_renamer.ipad_landscape_agent_v12 import (  # noqa: E402
    _backspace_current_name,
)
from pogo_iphone_renamer.ipad_landscape_agent_v16 import (  # noqa: E402
    _open_verified_rename_dialog,
)
from pogo_iphone_renamer.ipad_landscape_agent_v5 import (  # noqa: E402
    accessibility_elements,
)
from pogo_iphone_renamer.local_ocr_v3 import analyze_name_region  # noqa: E402
from pogo_iphone_renamer.native_agent_v2 import (  # noqa: E402
    ResilientStreamableHTTPClient,
)
from pogo_iphone_renamer.nickname import generate_iv_nickname  # noqa: E402
from pogo_iphone_renamer.server import SafeProxy  # noqa: E402


def compact_elements(value: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(value, dict):
        text = value.get("text")
        rect = value.get("rect")
        if isinstance(text, str) and text.strip():
            found.append(
                {
                    "text": text,
                    "type": value.get("type"),
                    "role": value.get("role"),
                    "rect": rect,
                }
            )
        for child in value.values():
            found.extend(compact_elements(child))
    elif isinstance(value, list):
        for child in value:
            found.extend(compact_elements(child))
    return found


def main() -> int:
    settings = Settings.from_env()
    with DeviceRunLock(ROOT / ".pogo-data" / "iphone-mcp.lock"):
        proxy = SafeProxy(
            settings, ResilientStreamableHTTPClient(settings, timeout=120.0)
        )
        dialog_open = False
        try:
            start = screen_snapshot(proxy)
            appraisal, measurement = v14.navigate_to_appraisal_v14(proxy, start)
            assert appraisal.image
            name = analyze_name_region(appraisal.image, base.ORIENTATION)
            if not name.is_default or not name.species:
                raise RuntimeError("current Pokémon does not have an exact default name")
            nickname = generate_iv_nickname(
                name.species,
                measurement.attack,
                measurement.defense,
                measurement.stamina,
            )
            _open_verified_rename_dialog(proxy, appraisal, name.species)
            dialog_open = True
            count = _backspace_current_name(proxy, name.species)
            _mark_rename_observation(proxy, f"probe cleared {count} exact characters")
            assert proxy.observation is not None
            proxy.call_tool(
                "input_text",
                {
                    "text": nickname,
                    "_observation_token": proxy.observation.token,
                    "_intent": "rename probe exact field without submitting",
                    "_expected_after": "rename field contains exact probe nickname",
                    "_current_name": name.species,
                    "_species": name.species,
                    "_default_name_verified": True,
                },
            )
            immediate = accessibility_elements(
                Snapshot(proxy.observation.text if proxy.observation else "", None)
            )
            ui_elements = proxy.call_tool("get_ui_elements", {})
            refreshed = base._next_snapshot(proxy, 0.6)
            full = accessibility_elements(refreshed)
            print(
                json.dumps(
                    {
                        "species": name.species,
                        "nickname": nickname,
                        "immediate_after_input": immediate,
                        "get_ui_elements": compact_elements(ui_elements),
                        "full_describe": full,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        finally:
            if dialog_open and proxy.observation is not None:
                try:
                    proxy.observation.text += "\n重新命名（探针结束，取消且不提交）"
                    base._tap(proxy, "RENAME_CANCEL")
                    returned = base._next_snapshot(proxy, 1.5)
                    base._validate_expected("DETAIL", returned)
                    print("PROBE_CANCELLED returned=DETAIL submitted=false")
                except Exception as exc:
                    print(f"PROBE_CANCEL_FAILED {exc}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
