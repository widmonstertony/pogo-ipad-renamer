from __future__ import annotations

import hashlib
import json
import sys
import time
import traceback
from typing import Any, Protocol

from .config import Settings
from .journal import Journal
from .policy import (
    LOCAL_TOOLS,
    READ_TOOLS,
    WRITE_TOOLS,
    Observation,
    PolicyViolation,
    add_safety_schema,
    arguments_are_dangerous,
    extract_safety_metadata,
    make_observation_token,
    normalize_text,
    on_rename_screen,
    screen_is_dangerous,
    validate_bounds,
    validate_poke_genie_name,
)
from .protocol import text_from_content
from .upstream import StreamableHTTPClient, UpstreamError


class PhoneClient(Protocol):
    def health(self) -> dict[str, Any]: ...
    def list_tools(self) -> list[dict[str, Any]]: ...
    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


def _text_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _number_from_tree(value: Any, keys: tuple[str, ...]) -> float | None:
    if isinstance(value, dict):
        for key in keys:
            candidate = value.get(key)
            if isinstance(candidate, (int, float)):
                return float(candidate)
        for candidate in value.values():
            found = _number_from_tree(candidate, keys)
            if found is not None:
                return found
    elif isinstance(value, list):
        for candidate in value:
            found = _number_from_tree(candidate, keys)
            if found is not None:
                return found
    return None


class SafeProxy:
    def __init__(self, settings: Settings, client: PhoneClient) -> None:
        self.settings = settings
        self.client = client
        self.journal = Journal(settings.journal_path)
        self.observation: Observation | None = None
        self.aborted = False
        self.abort_reason: str | None = None
        self.verified_renames = 0
        self.pending_name: str | None = None
        self._upstream_tools: dict[str, dict[str, Any]] | None = None

    def list_tools(self) -> list[dict[str, Any]]:
        if self._upstream_tools is None:
            self._upstream_tools = {
                str(tool.get("name")): tool for tool in self.client.list_tools()
            }
        exposed: list[dict[str, Any]] = []
        for name in sorted(READ_TOOLS | WRITE_TOOLS):
            tool = self._upstream_tools.get(name)
            if tool is not None:
                exposed.append(add_safety_schema(tool, name in WRITE_TOOLS))
        exposed.extend(self._local_tool_schemas())
        return exposed

    def _local_tool_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "name": "pogo_run_status",
                "description": "Show safety mode, batch progress, and pending rename state.",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "pogo_record_decision",
                "description": "Append a non-mutating per-Pokemon decision to the audit journal.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "species": {"type": "string"},
                        "current_name": {"type": "string"},
                        "decision": {
                            "type": "string",
                            "enum": ["renamed", "skip_custom", "skip_uncertain"],
                        },
                        "reason": {"type": "string"},
                        "new_name": {"type": "string"},
                    },
                    "required": ["species", "current_name", "decision", "reason"],
                },
            },
            {
                "name": "pogo_abort",
                "description": "Irreversibly abort this proxy process after a safety concern.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"reason": {"type": "string"}},
                    "required": ["reason"],
                },
            },
        ]

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name in LOCAL_TOOLS:
            return self._call_local(name, arguments)
        if name in READ_TOOLS:
            return self._call_read(name, arguments)
        if name in WRITE_TOOLS:
            return self._call_write(name, arguments)
        raise PolicyViolation(f"tool is not exposed: {name}")

    def _call_local(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "pogo_run_status":
            return _text_result(
                json.dumps(
                    {
                        "write_enabled": self.settings.write_enabled,
                        "aborted": self.aborted,
                        "abort_reason": self.abort_reason,
                        "verified_renames": self.verified_renames,
                        "batch_limit": self.settings.batch_limit,
                        "pending_name": self.pending_name,
                        "has_fresh_observation": bool(
                            self.observation
                            and self.observation.is_fresh(
                                self.settings.observation_ttl_seconds
                            )
                        ),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        if name == "pogo_abort":
            reason = normalize_text(str(arguments.get("reason", "")))
            if not reason:
                raise PolicyViolation("abort reason is required")
            self.aborted = True
            self.abort_reason = reason
            self.journal.append("abort", {"reason": reason})
            return _text_result(f"ABORTED: {reason}")
        if name == "pogo_record_decision":
            decision = str(arguments.get("decision", ""))
            if decision not in {"renamed", "skip_custom", "skip_uncertain"}:
                raise PolicyViolation("invalid decision")
            if decision == "renamed" and self.pending_name is not None:
                raise PolicyViolation("cannot record renamed before pending rename is verified")
            self.journal.append("decision", dict(arguments))
            return _text_result("decision recorded")
        raise PolicyViolation(f"unknown local tool: {name}")

    def _call_read(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.aborted and name not in {"screenshot", "describe_screen", "get_screen_info"}:
            raise PolicyViolation("run is aborted; only evidence observation remains allowed")
        result = self.client.call_tool(name, arguments)
        if name == "describe_screen":
            screen_info = self.client.call_tool("get_screen_info", {})
            result = self._record_observation(result, screen_info)
        return result

    def _record_observation(
        self, description: dict[str, Any], screen_info: dict[str, Any]
    ) -> dict[str, Any]:
        description_text = text_from_content(description)
        info_text = text_from_content(screen_info)
        combined = f"SCREEN_INFO\n{info_text}\nDESCRIBE_SCREEN\n{description_text}"
        width = _number_from_tree(screen_info, ("width", "screen_width", "logical_width"))
        height = _number_from_tree(screen_info, ("height", "screen_height", "logical_height"))
        token = make_observation_token(combined, width, height)
        self.observation = Observation(token, time.time(), combined, width, height)
        if screen_is_dangerous(combined):
            self.aborted = True
            self.abort_reason = "dangerous Transfer confirmation detected"
            self.journal.append(
                "automatic_abort",
                {"reason": self.abort_reason, "screen_hash": _hash_text(combined)},
            )
        content = list(description.get("content", []))
        content.append(
            {
                "type": "text",
                "text": (
                    f"SAFETY_OBSERVATION_TOKEN={token}\n"
                    f"SCREEN_BOUNDS={width}x{height}\n"
                    f"ABORTED={str(self.aborted).lower()}"
                ),
            }
        )
        return {**description, "content": content}

    def _call_write(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        upstream_args, metadata = extract_safety_metadata(arguments)
        intent = normalize_text(str(metadata.get("_intent", "")))
        expected_after = normalize_text(str(metadata.get("_expected_after", "")))
        token = str(metadata.get("_observation_token", ""))
        event: dict[str, Any] = {
            "tool": name,
            "arguments": upstream_args,
            "intent": intent,
            "expected_after": expected_after,
            "observation_token": token,
            "write_enabled": self.settings.write_enabled,
        }
        try:
            self._validate_write(name, upstream_args, metadata)
            assert self.observation is not None
            event["before_screen_hash"] = _hash_text(self.observation.text)
            self.observation.used = True
            result = self.client.call_tool(name, upstream_args)
            if bool(result.get("isError")):
                raise PolicyViolation(f"upstream tool {name} reported an error")
            if name in {"input_text", "type_text"}:
                self.pending_name = normalize_text(str(upstream_args.get("text", "")))
            after = self.client.call_tool(
                "describe_screen",
                {"clickable_only": True, "include_ocr": True, "include_screenshot": False},
            )
            screen_info = self.client.call_tool("get_screen_info", {})
            observed_after = self._record_observation(after, screen_info)
            assert self.observation is not None
            event["after_screen_hash"] = _hash_text(self.observation.text)
            event["success"] = True

            if (
                self.pending_name
                and name in {"tap_screen", "tap_element", "press_key"}
                and self.pending_name in self.observation.text
                and not on_rename_screen(self.observation.text)
            ):
                self.verified_renames += 1
                event["verified_name"] = self.pending_name
                self.pending_name = None
            result_content = list(result.get("content", []))
            result_content.extend(observed_after.get("content", []))
            return {**result, "content": result_content}
        except Exception as exc:
            event["success"] = False
            event["error"] = str(exc)
            raise
        finally:
            self.journal.append("write_attempt", event)

    def _validate_write(
        self, name: str, arguments: dict[str, Any], metadata: dict[str, Any]
    ) -> None:
        if not self.settings.write_enabled:
            raise PolicyViolation("phone writes are disabled (POGO_WRITE_ENABLED=false)")
        if self.aborted:
            raise PolicyViolation(f"run is aborted: {self.abort_reason}")
        if (
            self.settings.batch_limit > 0
            and self.verified_renames >= self.settings.batch_limit
        ):
            raise PolicyViolation("verified rename batch limit reached")
        if self.observation is None:
            raise PolicyViolation("describe_screen must be called before every write")
        token = str(metadata.get("_observation_token", ""))
        if token != self.observation.token:
            raise PolicyViolation("observation token does not match the latest screen")
        if not self.observation.is_fresh(self.settings.observation_ttl_seconds):
            raise PolicyViolation("observation token is stale or already used")
        if screen_is_dangerous(self.observation.text):
            raise PolicyViolation("dangerous screen detected")
        if arguments_are_dangerous(arguments):
            raise PolicyViolation("action arguments contain a forbidden operation")
        intent = normalize_text(str(metadata.get("_intent", ""))).casefold()
        expected = normalize_text(str(metadata.get("_expected_after", "")))
        if not intent or not expected:
            raise PolicyViolation("intent and expected postcondition are required")
        if not any(term in intent for term in ("rename", "name", "命名", "改名", "navigate", "導航", "恢复", "喚醒", "launch")):
            raise PolicyViolation("intent is outside the rename/navigation scope")

        if name in {"launch_app", "kill_app"}:
            if arguments.get("bundle_id") != self.settings.pokemon_go_bundle_id:
                raise PolicyViolation(
                    "only the configured Pokemon GO bundle may launch or terminate"
                )
        elif name != "wake_and_home" and self.settings.pokemon_go_bundle_id.casefold() not in self.observation.text.casefold():
            raise PolicyViolation("Pokemon GO is not proven to be the foreground app")

        validate_bounds(name, arguments, self.observation)

        if name == "press_key" and arguments.get("key") not in {"enter", "delete", "backspace"}:
            raise PolicyViolation("only enter/delete/backspace keyboard keys are allowed")

        if self.pending_name and name in {"swipe_screen", "launch_app", "kill_app", "wake_and_home", "input_text", "type_text"}:
            raise PolicyViolation("a rename is pending; confirm and verify it before navigation")

        if name in {"input_text", "type_text"}:
            if not on_rename_screen(self.observation.text):
                raise PolicyViolation("text input is allowed only on a detected rename screen")
            current_name = normalize_text(str(metadata.get("_current_name", "")))
            species = normalize_text(str(metadata.get("_species", "")))
            verified = metadata.get("_default_name_verified") is True
            if not verified or current_name != species:
                raise PolicyViolation("current name is not verified as the exact default species name")
            validate_poke_genie_name(str(arguments.get("text", "")), species)


class StdioMCPServer:
    def __init__(self, proxy: SafeProxy) -> None:
        self.proxy = proxy

    def serve(self) -> None:
        for line in sys.stdin:
            if not line.strip():
                continue
            request: dict[str, Any] | None = None
            try:
                request = json.loads(line)
                response = self._handle(request)
            except Exception as exc:
                response = {
                    "jsonrpc": "2.0",
                    "id": request.get("id") if isinstance(request, dict) else None,
                    "error": {"code": -32000, "message": str(exc)},
                }
                print(traceback.format_exc(), file=sys.stderr, flush=True)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)

    def _handle(self, request: dict[str, Any]) -> dict[str, Any] | None:
        method = request.get("method")
        request_id = request.get("id")
        if request_id is None:
            return None
        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "pogo-iphone-safe", "version": "0.1.0"},
                    "instructions": "Only supervised Pokemon GO renaming tools are exposed.",
                },
            }
        if method == "ping":
            return {"jsonrpc": "2.0", "id": request_id, "result": {}}
        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "result": {"tools": self.proxy.list_tools()},
            }
        if method == "tools/call":
            params = request.get("params") or {}
            try:
                result = self.proxy.call_tool(
                    str(params.get("name", "")), dict(params.get("arguments") or {})
                )
            except (PolicyViolation, UpstreamError, ValueError, KeyError) as exc:
                result = _text_result(f"SAFE_PROXY_REJECTED: {exc}", is_error=True)
            return {"jsonrpc": "2.0", "id": request_id, "result": result}
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"method not found: {method}"},
        }


def build_server(settings: Settings) -> StdioMCPServer:
    client = StreamableHTTPClient(settings)
    return StdioMCPServer(SafeProxy(settings, client))
