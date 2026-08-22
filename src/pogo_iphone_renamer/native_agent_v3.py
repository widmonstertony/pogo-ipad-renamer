from __future__ import annotations

from typing import Any

from . import native_agent as agent_v1
from .native_agent_v2 import ResilientStreamableHTTPClient


FOLLOWUP_TEXT = (
    "继续执行最初的同一个任务。下面是上一轮本地工具结果；"
    "请据此决定下一步，必要时直接调用现有工具。不要重复已经完成的动作。"
)


def qwen_safe_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Adapt a tool-only tail to the Qwen3.5/3.8 chat-template guard.

    Qwen's multimodal template can reject a history ending in tool messages with
    `No user query found in messages`. Keep canonical history intact, but send a
    recent continuation user turn. Images belong on that user turn per Ollama's
    native vision API rather than on a role=tool message.
    """
    normalized: list[dict[str, Any]] = []
    tail_images: list[str] = []
    for original in messages:
        message = dict(original)
        images = message.pop("images", None)
        if message.get("role") == "tool" and isinstance(images, list):
            tail_images.extend(str(image) for image in images if image)
        elif images:
            message["images"] = images
        normalized.append(message)

    if normalized and normalized[-1].get("role") == "tool":
        continuation: dict[str, Any] = {"role": "user", "content": FOLLOWUP_TEXT}
        if tail_images:
            continuation["images"] = tail_images[-1:]
        normalized.append(continuation)
    return normalized


class QwenSafeOllamaClient(agent_v1.OllamaNativeClient):
    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return super().chat(qwen_safe_messages(messages), tools)


def main(argv: list[str] | None = None) -> int:
    agent_v1.StreamableHTTPClient = ResilientStreamableHTTPClient
    agent_v1.OllamaNativeClient = QwenSafeOllamaClient
    return agent_v1.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

