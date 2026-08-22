from __future__ import annotations

import os
import sys
from pathlib import Path


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
os.environ.setdefault("POGO_JOURNAL_PATH", str(ROOT / ".pogo-data" / "actions.jsonl"))

from pogo_iphone_renamer import ipad_landscape_agent as base  # noqa: E402
from pogo_iphone_renamer import ipad_landscape_agent_v14 as v14  # noqa: E402
from pogo_iphone_renamer.appraisal_agent import screen_snapshot  # noqa: E402
from pogo_iphone_renamer.config import Settings  # noqa: E402
from pogo_iphone_renamer.device_run_lock import DeviceRunLock  # noqa: E402
from pogo_iphone_renamer.landscape_cv import rotate_mcp_image_upright  # noqa: E402
from pogo_iphone_renamer.local_ocr import ocr_mcp_screenshot  # noqa: E402
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient  # noqa: E402
from pogo_iphone_renamer.policy import PolicyViolation  # noqa: E402
from pogo_iphone_renamer.server import SafeProxy  # noqa: E402


def main() -> int:
    settings = Settings.from_env()
    with DeviceRunLock(ROOT / ".pogo-data" / "iphone-mcp.lock"):
        proxy = SafeProxy(
            settings, ResilientStreamableHTTPClient(settings, timeout=120.0)
        )
        entry = screen_snapshot(proxy)
        state = v14.robust_page_state(entry)
        if state != "POKEDEX_DETAIL":
            raise PolicyViolation(f"当前不是已验证的图鉴条目（检测={state}）；不会点击")
        base._tap(proxy, "POKEDEX_CLOSE")
        returned = base._next_snapshot(proxy, 2.0)
        if not returned.image:
            raise RuntimeError("关闭后 MCP 未返回截图")
        upright = rotate_mcp_image_upright(returned.image, base.ORIENTATION)
        output = ROOT / ".pogo-data" / "pokedex-return-upright.png"
        upright.save(output, format="PNG")
        lines = ocr_mcp_screenshot(returned.image, base.ORIENTATION)
        print(f"returned_state={v14.robust_page_state(returned)}")
        print("ocr=" + " | ".join(line.text for line in lines))
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
