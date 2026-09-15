from __future__ import annotations

import time
from typing import Any

from . import native_agent as native_worker
from .policy import READ_TOOLS
from .upstream import StreamableHTTPClient, UpstreamError


class ResilientStreamableHTTPClient(StreamableHTTPClient):
    """Reconnect and retry reads only; writes are never replayed."""

    def __init__(self, settings: Any, timeout: float = 120.0) -> None:
        # Callers that expect a long operation can still request it, while the
        # direct-detail batch can deliberately use a short transport timeout.
        # That lets its detached owner reclaim a disconnected MCP session
        # promptly instead of leaving a worker blocked for minutes.
        super().__init__(settings, timeout=max(timeout, 5.0))

    def _reset_session(self) -> None:
        self.session_id = None
        self._initialized = False

    def reset_read_session(self) -> None:
        """Start the next read in a new MCP session without replaying a write.

        Some iPadOS 17 Stage Manager builds cache screenshots per streamable
        HTTP session.  The batch uses this only before a read; it never resets
        or retries a user-visible write.
        """

        self._reset_session()

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
    # The original loop resolves this global at runtime. Replace only its transport;
    # all safety policy, tool filtering and audit behavior remain unchanged.
    native_worker.StreamableHTTPClient = ResilientStreamableHTTPClient
    return native_worker.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
