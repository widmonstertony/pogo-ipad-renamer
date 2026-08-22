from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from typing import Any

from .config import Settings
from .protocol import parse_http_payload


class UpstreamError(RuntimeError):
    pass


class StreamableHTTPClient:
    def __init__(self, settings: Settings, timeout: float = 15.0) -> None:
        self.settings = settings
        self.timeout = timeout
        self.session_id: str | None = None
        self._request_id = 0
        self._initialized = False
        self._lock = threading.RLock()

    def health(self) -> dict[str, Any]:
        request = urllib.request.Request(
            self.settings.health_url,
            method="GET",
            headers={"Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                value = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise UpstreamError(f"health request failed: {exc}") from exc
        if not isinstance(value, dict):
            raise UpstreamError("health response was not an object")
        return value

    def initialize(self) -> None:
        with self._lock:
            if self._initialized:
                return
            result = self._rpc(
                "initialize",
                {
                    "protocolVersion": self.settings.protocol_version,
                    "capabilities": {},
                    "clientInfo": {
                        "name": "pogo-iphone-safe-proxy",
                        "version": "0.1.0",
                    },
                },
                initialize_call=True,
            )
            if not isinstance(result, dict):
                raise UpstreamError("initialize returned no result")
            self._notification("notifications/initialized", {})
            self._initialized = True

    def list_tools(self) -> list[dict[str, Any]]:
        self.initialize()
        result = self._rpc("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [tool for tool in tools if isinstance(tool, dict)]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.initialize()
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise UpstreamError(f"tool {name} returned no result")
        return result

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.settings.protocol_version,
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    def _rpc(
        self,
        method: str,
        params: dict[str, Any],
        *,
        initialize_call: bool = False,
    ) -> dict[str, Any] | None:
        request_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params,
        }
        response = self._post(payload)
        if response is None:
            raise UpstreamError(f"empty response for {method}")
        if response.get("id") not in {request_id, None}:
            raise UpstreamError(f"unexpected response id for {method}")
        if "error" in response:
            raise UpstreamError(f"{method} failed: {response['error']}")
        if initialize_call and not self.session_id:
            raise UpstreamError("initialize response did not include Mcp-Session-Id")
        result = response.get("result")
        return result if isinstance(result, dict) else None

    def _notification(self, method: str, params: dict[str, Any]) -> None:
        self._post({"jsonrpc": "2.0", "method": method, "params": params})

    def _post(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            self.settings.mcp_url,
            data=data,
            method="POST",
            headers=self._headers(),
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                session = response.headers.get("Mcp-Session-Id")
                if session:
                    self.session_id = session
                body = response.read()
                return parse_http_payload(response.headers.get("Content-Type", ""), body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise UpstreamError(f"HTTP {exc.code}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise UpstreamError(f"MCP request failed: {exc}") from exc

