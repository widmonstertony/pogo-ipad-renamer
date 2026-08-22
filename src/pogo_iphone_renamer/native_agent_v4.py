from __future__ import annotations

import argparse
from typing import Any

from .config import Settings
from .native_agent import (
    OllamaNativeClient,
    READ_ONLY_PROMPT,
    SYSTEM_PROMPT,
    emit,
    tool_result_message,
)
from .native_agent_v2 import ResilientStreamableHTTPClient
from .native_agent_v3 import main as agent_loop_main
from .server import SafeProxy


READONLY_REPORT_INSTRUCTION = """\
以下是程序刚刚从 iPhone 安全读取的真实观察结果。不要调用工具；直接根据结果生成最终中文只读报告。
报告必须说明前台应用、屏幕是否适合改名、是否能确认默认名称及 Poke Genie 完整昵称，以及原本下一步会做什么。
如果条件不满足，明确说明需要用户先在手机上做什么。本次没有并且不允许任何写操作。
"""


def readonly_user_message(
    frontmost_result: dict[str, Any], screen_result: dict[str, Any]
) -> dict[str, Any]:
    front = tool_result_message("get_frontmost_app", frontmost_result)
    screen = tool_result_message("describe_screen", screen_result)
    content = (
        READ_ONLY_PROMPT
        + "\n\n"
        + READONLY_REPORT_INSTRUCTION
        + "\n\nFRONTMOST_APP\n"
        + str(front.get("content", ""))
        + "\n\nSCREEN_OBSERVATION\n"
        + str(screen.get("content", ""))
    )
    message: dict[str, Any] = {"role": "user", "content": content}
    images = screen.get("images")
    if isinstance(images, list) and images:
        message["images"] = images[-1:]
    return message


def run_readonly_once(settings: Settings, ollama_url: str, model: str) -> int:
    proxy = SafeProxy(
        settings,
        ResilientStreamableHTTPClient(settings, timeout=120.0),
    )
    emit("status", message="确定性只读预演已启动；程序只读取两次，然后由本地模型生成报告。")
    emit("tool", name="get_frontmost_app", arguments={})
    frontmost = proxy.call_tool("get_frontmost_app", {})
    emit("tool_result", name="get_frontmost_app", message="完成")
    emit(
        "tool",
        name="describe_screen",
        arguments={
            "include_screenshot": True,
            "include_ocr": True,
            "clickable_only": False,
        },
    )
    screen = proxy.call_tool(
        "describe_screen",
        {
            "include_screenshot": True,
            "include_ocr": True,
            "clickable_only": False,
        },
    )
    emit("tool_result", name="describe_screen", message="完成")
    emit("thinking", message="本地模型正在生成最终只读报告…")
    client = OllamaNativeClient(ollama_url, model)
    response = client.chat(
        [
            {
                "role": "system",
                "content": SYSTEM_PROMPT + "\n本轮没有工具可调用，只输出最终报告。",
            },
            readonly_user_message(frontmost, screen),
        ],
        [],
    )
    message = response.get("message")
    content = str(message.get("content", "")).strip() if isinstance(message, dict) else ""
    if not content:
        emit("error", message="本地模型没有返回只读报告。")
        return 2
    emit("assistant", text=content)
    emit("finished", message="只读预演已完成；共读取两次，未执行任何写操作。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pokémon GO deterministic/native agent")
    parser.add_argument("--mode", choices=("readonly", "rename"), required=True)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.8:27b")
    parser.add_argument("--max-steps", type=int, default=30)
    args = parser.parse_args(argv)
    if args.mode == "rename":
        return agent_loop_main(argv)
    try:
        return run_readonly_once(Settings.from_env(), args.ollama_url, args.model)
    except Exception as exc:
        emit("error", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

