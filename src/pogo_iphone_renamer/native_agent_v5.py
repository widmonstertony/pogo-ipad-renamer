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
from .native_agent_v4 import READONLY_REPORT_INSTRUCTION
from .server import SafeProxy


def readonly_user_message(
    frontmost_result: dict[str, Any],
    screen_result: dict[str, Any],
    screenshot_result: dict[str, Any],
) -> dict[str, Any]:
    front = tool_result_message("get_frontmost_app", frontmost_result)
    screen = tool_result_message("describe_screen", screen_result)
    screenshot = tool_result_message("screenshot", screenshot_result)
    screen_text = str(screen.get("content", ""))
    if len(screen_text) > 24_000:
        screen_text = screen_text[:24_000] + "\n[结构化屏幕文本已截断]"
    content = (
        READ_ONLY_PROMPT
        + "\n\n"
        + READONLY_REPORT_INSTRUCTION
        + "\n\nFRONTMOST_APP\n"
        + str(front.get("content", ""))
        + "\n\nSTRUCTURED_SCREEN_AND_OCR\n"
        + screen_text
        + "\n\n随消息附带的图片是同一时刻的 iPhone 屏幕截图，请直接观察它。"
    )
    message: dict[str, Any] = {"role": "user", "content": content}
    images = screenshot.get("images")
    if isinstance(images, list) and images:
        message["images"] = images[-1:]
    return message


def run_readonly_once(settings: Settings, ollama_url: str, model: str) -> int:
    proxy = SafeProxy(
        settings,
        ResilientStreamableHTTPClient(settings, timeout=120.0),
    )
    emit("status", message="确定性只读预演已启动；读取前台、结构化屏幕和独立截图后生成一次报告。")
    emit("tool", name="get_frontmost_app", arguments={})
    frontmost = proxy.call_tool("get_frontmost_app", {})
    emit("tool_result", name="get_frontmost_app", message="完成")

    describe_arguments = {
        "include_screenshot": False,
        "include_ocr": True,
        "clickable_only": False,
    }
    emit("tool", name="describe_screen", arguments=describe_arguments)
    screen = proxy.call_tool("describe_screen", describe_arguments)
    emit("tool_result", name="describe_screen", message="完成")

    emit("tool", name="screenshot", arguments={})
    screenshot = proxy.call_tool("screenshot", {})
    emit("tool_result", name="screenshot", message="完成")

    emit("thinking", message="本地视觉模型正在生成最终只读报告…")
    response = OllamaNativeClient(ollama_url, model).chat(
        [
            {
                "role": "system",
                "content": SYSTEM_PROMPT + "\n本轮没有工具可调用，只输出最终报告。",
            },
            readonly_user_message(frontmost, screen, screenshot),
        ],
        [],
    )
    message = response.get("message")
    content = str(message.get("content", "")).strip() if isinstance(message, dict) else ""
    if not content:
        emit("error", message="本地视觉模型没有返回只读报告。")
        return 2
    emit("assistant", text=content)
    emit("finished", message="只读预演成功完成；仅执行三次读取，未执行任何写操作。")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pokémon GO deterministic vision/native agent")
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

