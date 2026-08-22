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
os.environ.setdefault(
    "POGO_JOURNAL_PATH", str(ROOT / ".pogo-data" / "actions.jsonl")
)

from pogo_iphone_renamer import ipad_landscape_agent as base  # noqa: E402
from pogo_iphone_renamer.appraisal_agent import screen_snapshot  # noqa: E402
from pogo_iphone_renamer.config import Settings  # noqa: E402
from pogo_iphone_renamer.ipad_landscape_agent_v16 import (  # noqa: E402
    open_dynamic_rename_from_detail,
)
from pogo_iphone_renamer.local_ocr_v3 import analyze_name_region  # noqa: E402
from pogo_iphone_renamer.native_agent_v2 import (  # noqa: E402
    ResilientStreamableHTTPClient,
)
from pogo_iphone_renamer.policy import PolicyViolation  # noqa: E402
from pogo_iphone_renamer.server import SafeProxy  # noqa: E402


def main() -> int:
    settings = Settings.from_env()
    proxy = SafeProxy(
        settings, ResilientStreamableHTTPClient(settings, timeout=120.0)
    )
    detail = screen_snapshot(proxy)
    base._validate_expected("DETAIL", detail)
    if not detail.image:
        raise PolicyViolation("详情页截图缺失")
    name = analyze_name_region(detail.image, base.ORIENTATION)
    if not name.is_default or not name.species:
        raise PolicyViolation("当前名称不是精确默认物种名；不测试铅笔")
    open_dynamic_rename_from_detail(proxy, detail, name.species)
    base._tap(proxy, "RENAME_CANCEL")
    returned = base._next_snapshot(proxy, 1.5)
    base._validate_expected("DETAIL", returned)
    print(f"PROBE_OK species={name.species} input_text=false returned=DETAIL")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
