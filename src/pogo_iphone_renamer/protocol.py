from __future__ import annotations

import json
from typing import Any


def parse_http_payload(content_type: str, body: bytes) -> dict[str, Any] | None:
    if not body.strip():
        return None
    text = body.decode("utf-8")
    if "text/event-stream" in content_type.lower() or text.lstrip().startswith(
        ("event:", "data:", ":")
    ):
        candidates: list[dict[str, Any]] = []
        for line in text.splitlines():
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if not data or data == "[DONE]":
                continue
            value = json.loads(data)
            if isinstance(value, dict):
                candidates.append(value)
        return candidates[-1] if candidates else None
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("MCP response must be a JSON object")
    return value


def text_from_content(result: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    structured = result.get("structuredContent")
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, sort_keys=True))
    return "\n".join(part for part in parts if part)

