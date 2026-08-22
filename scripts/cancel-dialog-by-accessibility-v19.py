from __future__ import annotations

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "src"
if str(SOURCE) not in sys.path:
    sys.path.insert(0, str(SOURCE))

os.environ.setdefault("IPHONE_MCP_URL", "http://192.168.68.61:8090/mcp")
os.environ.setdefault("IPHONE_MCP_HEALTH_URL", "http://192.168.68.61:8090/health")
os.environ.setdefault("POKEMON_GO_BUNDLE_ID", "com.nianticlabs.pokemongo")
os.environ.setdefault("POGO_WRITE_ENABLED", "true")
os.environ.setdefault("POGO_BATCH_LIMIT", "1")
os.environ.setdefault("POGO_OBSERVATION_TTL_SECONDS", "120")
os.environ.setdefault(
    "POGO_JOURNAL_PATH", str(ROOT / ".pogo-data" / "actions.jsonl")
)

from pogo_iphone_renamer import ipad_landscape_agent as base  # noqa: E402
from pogo_iphone_renamer.appraisal_agent import screen_snapshot  # noqa: E402
from pogo_iphone_renamer.config import Settings  # noqa: E402
from pogo_iphone_renamer.device_run_lock import DeviceRunLock  # noqa: E402
from pogo_iphone_renamer.local_ocr import (  # noqa: E402
    ocr_mcp_screenshot,
    rename_dialog_visible,
)
from pogo_iphone_renamer.native_agent_v2 import (  # noqa: E402
    ResilientStreamableHTTPClient,
)
from pogo_iphone_renamer.policy import PolicyViolation  # noqa: E402
from pogo_iphone_renamer.server import SafeProxy  # noqa: E402


def main() -> int:
    settings = Settings.from_env()
    with DeviceRunLock(ROOT / ".pogo-data" / "iphone-mcp.lock"):
        proxy = SafeProxy(
            settings, ResilientStreamableHTTPClient(settings, timeout=120.0)
        )
        snapshot = screen_snapshot(proxy)
        if not snapshot.image or not rename_dialog_visible(
            ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
        ):
            raise PolicyViolation("当前未严格验证到改名弹窗；不会点击")
        assert proxy.observation is not None
        proxy.observation.text += "\n重新命名（离线 OCR 已验证；精确取消控件）"
        proxy.call_tool(
            "tap_element",
            {
                "text": "取消",
                "match": "exact",
                "role": "control",
                "_observation_token": proxy.observation.token,
                "_intent": "navigate cancel verified rename dialog without submitting",
                "_expected_after": "DETAIL",
            },
        )
        returned = base._next_snapshot(proxy, 1.5)
        base._validate_expected("DETAIL", returned)
        print("ACCESSIBILITY_CANCEL_OK returned=DETAIL submitted=false")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
