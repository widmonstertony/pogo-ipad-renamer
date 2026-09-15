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


def separate_inline_screenshot(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize ios-mcp's JSON-embedded image without treating bytes as UI text."""
    images: list[dict[str, Any]] = []

    def clean(value: Any) -> Any:
        if not isinstance(value, dict) or not isinstance(value.get("screenshot"), str):
            return value
        value = dict(value)
        encoded = value.pop("screenshot")
        if encoded:
            images.append({"type": "image", "data": encoded,
                           "mimeType": value.get("screenshot_mime", "image/jpeg")})
        return value

    content = []
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                original = json.loads(item.get("text", ""))
                cleaned = clean(original)
                if cleaned is not original:
                    item = {**item, "text": json.dumps(cleaned, ensure_ascii=False)}
            except (ValueError, TypeError):
                pass
        content.append(item)
    normalized = {**result, "content": content}
    if "structuredContent" in result:
        normalized["structuredContent"] = clean(result["structuredContent"])
    # Inline describe-screen pixels are paired with this observation. Prefer
    # them to an older image block, and never request a separate fallback frame.
    normalized["content"] = images[:1] + content
    return normalized


def text_from_content(result: dict[str, Any]) -> str:
    result = separate_inline_screenshot(result)
    parts: list[str] = []
    for item in result.get("content", []):
        if isinstance(item, dict) and item.get("type") == "text":
            parts.append(str(item.get("text", "")))
    structured = result.get("structuredContent")
    if structured is not None:
        parts.append(json.dumps(structured, ensure_ascii=False, sort_keys=True))
    return "\n".join(part for part in parts if part)
