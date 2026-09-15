from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.policy import PolicyViolation
from pogo_iphone_renamer.server import SafeProxy


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.screen = "frontmost_app=com.nianticlabs.pokemongo 重新命名 偷兒狐"
        self.frontmost = "com.nianticlabs.pokemongo"

    def health(self) -> dict[str, Any]:
        return {"status": "ok"}

    def list_tools(self) -> list[dict[str, Any]]:
        names = [
            "get_screen_info",
            "describe_screen",
            "input_text",
            "tap_element",
            "kill_app",
            "run_command",
            "read_file",
            "install_app",
        ]
        return [
            {
                "name": name,
                "description": name,
                "inputSchema": {"type": "object", "properties": {}},
            }
            for name in names
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((name, arguments))
        if name == "get_screen_info":
            return {
                "content": [
                    {
                        "type": "text",
                        "text": '{"width":390,"height":844,"orientation":"portrait"}',
                    }
                ]
            }
        if name == "describe_screen":
            return {"content": [{"type": "text", "text": self.screen}]}
        if name == "get_frontmost_app":
            return {"content": [{"type": "text", "text": self.frontmost}]}
        return {"content": [{"type": "text", "text": "ok"}]}


def settings(path: Path, write_enabled: bool) -> Settings:
    return Settings(
        mcp_url="http://127.0.0.1:8090/mcp",
        health_url="http://127.0.0.1:8090/health",
        protocol_version="2025-11-25",
        pokemon_go_bundle_id="com.nianticlabs.pokemongo",
        write_enabled=write_enabled,
        batch_limit=20,
        observation_ttl_seconds=20,
        journal_path=path,
    )


class SafeProxyTests(unittest.TestCase):
    def test_allows_one_exact_verified_type_text_fallback_for_pending_name(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            proxy = SafeProxy(settings(Path(directory) / "j.jsonl", True), client)
            proxy.call_tool("describe_screen", {})
            assert proxy.observation is not None
            nickname = "偷兒狐⓯❸❹⁴⁹"
            proxy.call_tool(
                "input_text",
                {
                    "text": nickname,
                    "_observation_token": proxy.observation.token,
                    "_intent": "rename default Pokemon",
                    "_expected_after": "field contains exact generated name",
                    "_current_name": "偷兒狐",
                    "_species": "偷兒狐",
                    "_default_name_verified": True,
                },
            )
            assert proxy.observation is not None
            result = proxy.call_tool(
                "type_text",
                {
                    "text": nickname,
                    "_observation_token": proxy.observation.token,
                    "_intent": "rename default Pokemon fallback",
                    "_expected_after": "field contains exact generated name",
                    "_current_name": "偷兒狐",
                    "_species": "偷兒狐",
                    "_default_name_verified": True,
                    "_fallback_default_field_verified": True,
                },
            )

        self.assertFalse(result.get("isError", False))
        self.assertEqual([name for name, _ in client.calls if name == "type_text"], ["type_text"])

    def test_pending_name_rejects_unproven_or_changed_type_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            proxy = SafeProxy(settings(Path(directory) / "j.jsonl", True), client)
            proxy.call_tool("describe_screen", {})
            assert proxy.observation is not None
            proxy.pending_name = "偷兒狐⓯❸❹⁴⁹"
            with self.assertRaisesRegex(PolicyViolation, "rename is pending"):
                proxy.call_tool(
                    "type_text",
                    {
                        "text": "偷兒狐❶❶❶²⁰",
                        "_observation_token": proxy.observation.token,
                        "_intent": "rename default Pokemon fallback",
                        "_expected_after": "field contains exact generated name",
                        "_current_name": "偷兒狐",
                        "_species": "偷兒狐",
                        "_default_name_verified": True,
                        "_fallback_default_field_verified": True,
                    },
                )

    def test_dangerous_upstream_tools_are_never_exposed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            proxy = SafeProxy(settings(Path(directory) / "j.jsonl", False), FakeClient())
            names = {tool["name"] for tool in proxy.list_tools()}
            self.assertNotIn("run_command", names)
            self.assertNotIn("read_file", names)
            self.assertNotIn("install_app", names)
            self.assertIn("describe_screen", names)
            self.assertIn("kill_app", names)

    def test_kill_app_is_restricted_to_configured_game(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            proxy = SafeProxy(settings(Path(directory) / "j.jsonl", True), client)
            proxy.call_tool("describe_screen", {})
            assert proxy.observation is not None
            with self.assertRaises(PolicyViolation):
                proxy.call_tool(
                    "kill_app",
                    {
                        "bundle_id": "com.apple.mobilesafari",
                        "_observation_token": proxy.observation.token,
                        "_intent": "restart and launch Pokemon GO capture",
                        "_expected_after": "Pokemon GO restarted",
                    },
                )

    def test_write_disabled_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            proxy = SafeProxy(settings(Path(directory) / "j.jsonl", False), client)
            proxy.call_tool("describe_screen", {})
            assert proxy.observation is not None
            with self.assertRaises(PolicyViolation):
                proxy.call_tool(
                    "input_text",
                    {
                        "text": "偷兒狐⓯❸❹⁴⁹",
                        "_observation_token": proxy.observation.token,
                        "_intent": "rename default Pokemon",
                        "_expected_after": "rename field contains exact generated name",
                        "_current_name": "偷兒狐",
                        "_species": "偷兒狐",
                        "_default_name_verified": True,
                    },
                )

    def test_default_name_guard_and_single_use_token(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            proxy = SafeProxy(settings(Path(directory) / "j.jsonl", True), client)
            proxy.call_tool("describe_screen", {})
            assert proxy.observation is not None
            bad_token = proxy.observation.token
            with self.assertRaises(PolicyViolation):
                proxy.call_tool(
                    "input_text",
                    {
                        "text": "偷兒狐⓯❸❹⁴⁹",
                        "_observation_token": bad_token,
                        "_intent": "rename default Pokemon",
                        "_expected_after": "field contains name",
                        "_current_name": "自訂名",
                        "_species": "偷兒狐",
                        "_default_name_verified": True,
                    },
                )
            proxy.call_tool("describe_screen", {})
            assert proxy.observation is not None
            good_token = proxy.observation.token
            result = proxy.call_tool(
                "input_text",
                {
                    "text": "偷兒狐⓯❸❹⁴⁹",
                    "_observation_token": good_token,
                    "_intent": "rename default Pokemon",
                    "_expected_after": "field contains exact generated name",
                    "_current_name": "偷兒狐",
                    "_species": "偷兒狐",
                    "_default_name_verified": True,
                },
            )
            self.assertFalse(result.get("isError", False))
            with self.assertRaises(PolicyViolation):
                proxy.call_tool(
                    "tap_element",
                    {
                        "text": "好",
                        "_observation_token": good_token,
                        "_intent": "confirm rename",
                        "_expected_after": "detail page shows new name",
                    },
                )

    def test_frontmost_read_recovers_stale_stage_manager_accessibility(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            client.screen = "程序坞 网易云音乐HD Shijima 重新命名"
            proxy = SafeProxy(settings(Path(directory) / "j.jsonl", True), client)
            proxy.call_tool("describe_screen", {})
            assert proxy.observation is not None

            result = proxy.call_tool(
                "input_text",
                {
                    "text": "偷兒狐⓯❸❹⁴⁹",
                    "_observation_token": proxy.observation.token,
                    "_intent": "rename default Pokemon",
                    "_expected_after": "field contains exact generated name",
                    "_current_name": "偷兒狐",
                    "_species": "偷兒狐",
                    "_default_name_verified": True,
                },
            )

        self.assertFalse(result.get("isError", False))
        self.assertIn("get_frontmost_app", [name for name, _ in client.calls])

    def test_foreground_recheck_tolerates_one_stale_dock_answer(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            client.screen = "程序坞 App Store Shijima 重新命名 偷兒狐"
            answers = iter(["com.apple.springboard", "com.nianticlabs.pokemongo"])

            original_call = client.call_tool

            def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
                if name == "get_frontmost_app":
                    client.calls.append((name, arguments))
                    return {"content": [{"type": "text", "text": next(answers)}]}
                return original_call(name, arguments)

            client.call_tool = call_tool  # type: ignore[method-assign]
            proxy = SafeProxy(settings(Path(directory) / "j.jsonl", True), client)
            proxy.call_tool("describe_screen", {})
            assert proxy.observation is not None
            result = proxy.call_tool(
                "input_text",
                {
                    "text": "偷兒狐⓯❸❹⁴⁹",
                    "_observation_token": proxy.observation.token,
                    "_intent": "rename default Pokemon",
                    "_expected_after": "field contains exact generated name",
                    "_current_name": "偷兒狐",
                    "_species": "偷兒狐",
                    "_default_name_verified": True,
                },
            )

        self.assertFalse(result.get("isError", False))
        self.assertEqual(
            2, sum(name == "get_frontmost_app" for name, _ in client.calls)
        )


if __name__ == "__main__":
    unittest.main()
