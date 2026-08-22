from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from typing import Any

from .config import Settings
from .policy import PolicyViolation, READ_TOOLS, WRITE_TOOLS
from .prompts import READ_ONLY_PROMPT, rename_prompt
from .server import SafeProxy
from .upstream import StreamableHTTPClient, UpstreamError


SYSTEM_PROMPT = """\
你是 Pokémon GO iPhone 本地执行代理。你可以直接调用所提供的函数工具。

必须遵守：
- 工具已经加载完成；需要观察或操作时直接调用工具，不要声称要加载 MCP、插件或技能。
- 只能使用工具列表中精确存在的名称，不得编造名称或输出 invalid。
- 每次只根据最新工具结果决定下一步；工具失败时不要假装成功。
- 所有自然语言输出使用简体中文。
- 不向互联网发送任何手机内容。
- 绝不传送、删除、交换、强化、进化或购买；遇到危险或未知界面立即停止。
"""


def emit(event_type: str, **payload: Any) -> None:
    print(
        json.dumps({"type": event_type, **payload}, ensure_ascii=False),
        flush=True,
    )


def ollama_tool_schemas(
    tools: list[dict[str, Any]], *, read_only: bool
) -> list[dict[str, Any]]:
    allowed_local = {"pogo_run_status", "pogo_abort"}
    converted: list[dict[str, Any]] = []
    for tool in tools:
        name = str(tool.get("name", ""))
        if read_only and name not in READ_TOOLS | allowed_local:
            continue
        schema = tool.get("inputSchema")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        converted.append(
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": str(tool.get("description", "")),
                    "parameters": schema,
                },
            }
        )
    return converted


def available_tool_names(tools: list[dict[str, Any]]) -> set[str]:
    return {
        str(tool.get("function", {}).get("name", ""))
        for tool in tools
        if isinstance(tool.get("function"), dict)
    }


def normalize_tool_name(name: str, available: set[str]) -> str:
    if name in available:
        return name
    prefix = "iphone_safe_"
    if name.startswith(prefix) and name[len(prefix) :] in available:
        return name[len(prefix) :]
    raise PolicyViolation(f"模型请求了不存在的工具：{name or '<empty>'}")


def tool_result_message(name: str, result: dict[str, Any]) -> dict[str, Any]:
    texts: list[str] = []
    images: list[str] = []
    for item in result.get("content", []):
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            texts.append(str(item.get("text", "")))
        elif item.get("type") == "image" and item.get("data"):
            images.append(str(item["data"]))
    content = "\n".join(texts).strip()
    if len(content) > 48_000:
        content = content[:48_000] + "\n[工具输出因上下文限制已截断]"
    if not content:
        content = "工具返回了图像。" if images else json.dumps(result, ensure_ascii=False)
    message: dict[str, Any] = {
        "role": "tool",
        "tool_name": name,
        "content": content,
    }
    if images:
        message["images"] = images[:1]
    return message


class OllamaNativeClient:
    def __init__(self, base_url: str, model: str, timeout: float = 600.0) -> None:
        self.url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self.timeout = timeout

    def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": messages,
            "tools": tools,
            "stream": False,
            "keep_alive": "30m",
            "options": {
                "num_ctx": 32768,
                "temperature": 0.05,
            },
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(2):
            request = urllib.request.Request(
                self.url,
                data=data,
                method="POST",
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    value = json.loads(response.read().decode("utf-8"))
                if not isinstance(value, dict) or not isinstance(value.get("message"), dict):
                    raise RuntimeError("Ollama 返回格式无效")
                return value
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = RuntimeError(f"Ollama HTTP {exc.code}: {detail}")
                if exc.code < 500 or attempt == 1:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt == 1:
                    break
            time.sleep(1.0)
        raise RuntimeError(f"Ollama 原生调用失败：{last_error}")


def run_agent(
    *,
    settings: Settings,
    ollama_url: str,
    model: str,
    read_only: bool,
    max_steps: int,
) -> int:
    proxy = SafeProxy(settings, StreamableHTTPClient(settings, timeout=30.0))
    schemas = ollama_tool_schemas(proxy.list_tools(), read_only=read_only)
    names = available_tool_names(schemas)
    prompt = READ_ONLY_PROMPT if read_only else rename_prompt(settings.batch_limit)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    client = OllamaNativeClient(ollama_url, model)
    emit(
        "status",
        message=(
            f"原生 Ollama Agent 已启动；只读模式，仅暴露 {len(schemas)} 个读取/停止工具。"
            if read_only
            else f"原生 Ollama Agent 已启动；本批最多改名 {settings.batch_limit} 只。"
        ),
    )

    for step in range(1, max_steps + 1):
        emit("thinking", message=f"模型分析中（第 {step}/{max_steps} 轮）…")
        response = client.chat(messages, schemas)
        assistant = dict(response["message"])
        assistant.setdefault("role", "assistant")
        messages.append(assistant)

        content = str(assistant.get("content", "")).strip()
        if content:
            emit("assistant", text=content)

        calls = assistant.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            emit("finished", message="模型已完成只读预演。" if read_only else "模型已完成本批任务。")
            return 0

        for call in calls:
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                emit("error", message="模型返回了无效工具调用结构。")
                return 2
            try:
                name = normalize_tool_name(str(function.get("name", "")), names)
                arguments = function.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}
                if read_only and name in WRITE_TOOLS:
                    raise PolicyViolation(f"只读模式拒绝写工具：{name}")
                emit("tool", name=name, arguments=arguments)
                result = proxy.call_tool(name, arguments)
                messages.append(tool_result_message(name, result))
                if result.get("isError"):
                    emit("tool_error", name=name, message="安全工具返回错误。")
                else:
                    emit("tool_result", name=name, message="完成")
                if proxy.aborted:
                    emit("error", message=f"安全代理已中止：{proxy.abort_reason}")
                    return 3
            except (PolicyViolation, UpstreamError, ValueError, KeyError) as exc:
                error_result = {
                    "content": [{"type": "text", "text": f"SAFE_PROXY_REJECTED: {exc}"}],
                    "isError": True,
                }
                messages.append(tool_result_message(str(function.get("name", "")), error_result))
                emit("tool_error", name=str(function.get("name", "")), message=str(exc))

    emit("error", message=f"达到最大 {max_steps} 轮，已安全停止，防止无限循环。")
    return 4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pokémon GO native Ollama agent")
    parser.add_argument("--mode", choices=("readonly", "rename"), required=True)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.8:27b")
    parser.add_argument("--max-steps", type=int, default=30)
    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env()
        return run_agent(
            settings=settings,
            ollama_url=args.ollama_url,
            model=args.model,
            read_only=args.mode == "readonly",
            max_steps=max(1, min(args.max_steps, 100)),
        )
    except Exception as exc:
        emit("error", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

