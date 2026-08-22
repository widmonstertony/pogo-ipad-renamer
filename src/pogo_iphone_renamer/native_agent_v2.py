from __future__ import annotations

import time
from typing import Any

from . import native_agent as agent_v1
from .policy import READ_TOOLS
from .upstream import StreamableHTTPClient, UpstreamError


class ResilientStreamableHTTPClient(StreamableHTTPClient):
    """Reconnect and retry reads only; writes are never replayed."""

    def __init__(self, settings: Any, timeout: float = 120.0) -> None:
        super().__init__(settings, timeout=max(timeout, 120.0))

    def _reset_session(self) -> None:
        self.session_id = None
        self._initialized = False

    def list_tools(self) -> list[dict[str, Any]]:
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                return super().list_tools()
            except Exception as exc:
                last_error = exc
                self._reset_session()
                if attempt == 0:
                    time.sleep(0.5)
        raise UpstreamError(f"MCP tools/list reconnect failed: {last_error}")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        attempts = 2 if name in READ_TOOLS else 1
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return super().call_tool(name, arguments)
            except Exception as exc:
                last_error = exc
                self._reset_session()
                if attempt + 1 < attempts:
                    time.sleep(0.5)
        if name in READ_TOOLS:
            raise UpstreamError(f"MCP read {name} failed after reconnect: {last_error}")
        raise UpstreamError(
            f"MCP write {name} outcome is unknown; it was not retried: {last_error}"
        )


def main(argv: list[str] | None = None) -> int:
    # The V1 loop resolves this global at runtime. Replace only its transport;
    # all safety policy, tool filtering and audit behavior remain unchanged.
    agent_v1.StreamableHTTPClient = ResilientStreamableHTTPClient
    return agent_v1.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())

