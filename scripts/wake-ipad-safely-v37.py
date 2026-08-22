from __future__ import annotations

import json

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent import tool_result_message
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient
from pogo_iphone_renamer.server import SafeProxy


def main() -> int:
    settings = Settings.from_env()
    client = ResilientStreamableHTTPClient(settings, timeout=120)
    proxy = SafeProxy(settings, client)
    before = proxy.call_tool(
        "describe_screen",
        {"clickable_only": True, "include_ocr": True, "include_screenshot": False},
    )
    if proxy.observation is None:
        raise RuntimeError("safe proxy did not create an observation")
    before_info = tool_result_message(
        "get_screen_info", proxy.call_tool("get_screen_info", {})
    )
    print(f"before={before_info.get('content', '')}", flush=True)
    result = proxy.call_tool(
        "wake_and_home",
        {
            "sequence": "home_twice",
            "_observation_token": proxy.observation.token,
            "_intent": "喚醒 iPad 以继续 Pokémon GO 改名测试",
            "_expected_after": "iPad 亮屏；若需要密码则停留锁屏等待用户手动解锁",
        },
    )
    after_info = tool_result_message(
        "get_screen_info", proxy.call_tool("get_screen_info", {})
    )
    print(f"wake={json.dumps(tool_result_message('wake_and_home', result), ensure_ascii=False)}", flush=True)
    print(f"after={after_info.get('content', '')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
