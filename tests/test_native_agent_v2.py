from __future__ import annotations

import http.client
import unittest
from unittest.mock import patch

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient
from pogo_iphone_renamer.upstream import StreamableHTTPClient, UpstreamError


def settings() -> Settings:
    from pathlib import Path

    return Settings(
        mcp_url="http://127.0.0.1:8090/mcp",
        health_url="http://127.0.0.1:8090/health",
        protocol_version="2025-11-25",
        pokemon_go_bundle_id="com.nianticlabs.pokemongo",
        write_enabled=False,
        batch_limit=20,
        observation_ttl_seconds=20,
        journal_path=Path("test.jsonl"),
    )


class ResilientClientTests(unittest.TestCase):
    def test_accepts_short_transport_timeout_for_detached_recovery(self) -> None:
        client = ResilientStreamableHTTPClient(settings(), timeout=20.0)

        self.assertEqual(client.timeout, 20.0)

    def test_read_is_retried_with_fresh_session(self) -> None:
        client = ResilientStreamableHTTPClient(settings())
        client.session_id = "old"
        client._initialized = True
        with patch.object(
            StreamableHTTPClient,
            "call_tool",
            side_effect=[http.client.RemoteDisconnected("closed"), {"content": []}],
        ) as mocked:
            result = client.call_tool("get_frontmost_app", {})
        self.assertEqual(result, {"content": []})
        self.assertEqual(mocked.call_count, 2)
        self.assertIsNone(client.session_id)
        self.assertFalse(client._initialized)

    def test_write_is_never_retried(self) -> None:
        client = ResilientStreamableHTTPClient(settings())
        with patch.object(
            StreamableHTTPClient,
            "call_tool",
            side_effect=http.client.RemoteDisconnected("closed"),
        ) as mocked:
            with self.assertRaises(UpstreamError):
                client.call_tool("tap_screen", {"x": 1, "y": 1})
        self.assertEqual(mocked.call_count, 1)


if __name__ == "__main__":
    unittest.main()
