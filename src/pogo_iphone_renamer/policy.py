from __future__ import annotations

import copy
import hashlib
import json
import re
import time
import unicodedata
from dataclasses import dataclass
from typing import Any


READ_TOOLS = frozenset(
    {
        "get_screen_info",
        "screenshot",
        "get_clipboard",
        "get_frontmost_app",
        "get_ui_elements",
        "get_element_at_point",
        "ocr_screen",
        "describe_screen",
        "wait_for_element",
        "wait_for_disappear",
    }
)

WRITE_TOOLS = frozenset(
    {
        "wake_and_home",
        "kill_app",
        "launch_app",
        "tap_screen",
        "tap_element",
        "swipe_screen",
        "input_text",
        "type_text",
        "press_key",
    }
)

LOCAL_TOOLS = frozenset({"pogo_run_status", "pogo_record_decision", "pogo_abort"})

DANGEROUS_ARGUMENT_TERMS = (
    "transfer",
    "傳送",
    "传送",
    "delete",
    "刪除",
    "删除",
    "uninstall",
    "install",
    "shell",
    "command",
)

DANGEROUS_SCREEN_TERMS = (
    "確認傳送",
    "确认传送",
    "are you sure you want to transfer",
    "transfer this pokémon",
    "transfer selected",
    "bulk transfer",
)

RENAME_SCREEN_TERMS = (
    "重新命名",
    "更改名稱",
    "更改名字",
    "暱稱",
    "nickname",
    "rename",
)

ANNOTATION_CHARS = frozenset("⓿❶❷❸❹❺❻❼❽❾❿⓫⓬⓭⓮⓯⁰¹²³⁴⁵⁶⁷⁸⁹")


class PolicyViolation(RuntimeError):
    pass


@dataclass
class Observation:
    token: str
    created_at: float
    text: str
    width: float | None
    height: float | None
    used: bool = False

    def is_fresh(self, ttl: float, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        return not self.used and current - self.created_at <= ttl


def normalize_text(value: str) -> str:
    return unicodedata.normalize("NFC", value).strip()


def validate_poke_genie_name(nickname: str, species: str) -> None:
    nickname = normalize_text(nickname)
    species = normalize_text(species)
    if not nickname or not species:
        raise PolicyViolation("nickname and species must be non-empty")
    if any(unicodedata.category(char) in {"Cc", "Cs"} for char in nickname):
        raise PolicyViolation("nickname contains control characters")
    if len(nickname) > 32:
        raise PolicyViolation("nickname is unexpectedly long")
    lowered = nickname.casefold()
    if any(term in lowered for term in DANGEROUS_ARGUMENT_TERMS):
        raise PolicyViolation("nickname contains a forbidden term")
    if not any(char in ANNOTATION_CHARS for char in nickname):
        raise PolicyViolation("nickname lacks Poke Genie IV annotation characters")

    candidates = (nickname, nickname[1:] if len(nickname) > 1 else nickname)
    required_prefix = min(2, len(species))
    if not any(
        candidate[:required_prefix] == species[:required_prefix]
        for candidate in candidates
    ):
        raise PolicyViolation("nickname does not preserve the species-name prefix")
    if "(+" in nickname and not nickname.endswith("(+)"):
        raise PolicyViolation("legacy marker must be the exact '(+)' suffix")


def screen_is_dangerous(text: str) -> bool:
    lowered = normalize_text(text).casefold()
    return any(term in lowered for term in DANGEROUS_SCREEN_TERMS)


def on_rename_screen(text: str) -> bool:
    lowered = normalize_text(text).casefold()
    return any(term in lowered for term in RENAME_SCREEN_TERMS)


def arguments_are_dangerous(arguments: dict[str, Any]) -> bool:
    serialized = json.dumps(arguments, ensure_ascii=False, sort_keys=True).casefold()
    return any(term in serialized for term in DANGEROUS_ARGUMENT_TERMS)


def make_observation_token(text: str, width: float | None, height: float | None) -> str:
    payload = json.dumps(
        {"text": text, "width": width, "height": height, "time": time.time_ns()},
        ensure_ascii=False,
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:24]


def add_safety_schema(tool: dict[str, Any], is_write: bool) -> dict[str, Any]:
    cloned = copy.deepcopy(tool)
    if not is_write:
        return cloned
    schema = cloned.setdefault("inputSchema", {"type": "object"})
    properties = schema.setdefault("properties", {})
    properties.update(
        {
            "_observation_token": {
                "type": "string",
                "description": "Fresh single-use token appended by the latest observed screen.",
            },
            "_intent": {
                "type": "string",
                "description": "Concise allowed renaming/navigation intent.",
            },
            "_expected_after": {
                "type": "string",
                "description": "Concrete screen postcondition expected after this action.",
            },
        }
    )
    required = list(schema.get("required", []))
    for name in ("_observation_token", "_intent", "_expected_after"):
        if name not in required:
            required.append(name)
    if cloned.get("name") in {"input_text", "type_text"}:
        properties.update(
            {
                "_current_name": {"type": "string"},
                "_species": {"type": "string"},
                "_default_name_verified": {"type": "boolean", "const": True},
            }
        )
        for name in ("_current_name", "_species", "_default_name_verified"):
            if name not in required:
                required.append(name)
    schema["required"] = required
    cloned["description"] = (
        str(cloned.get("description", ""))
        + " Safety proxy: requires a fresh observation token and audited rename intent."
    )
    return cloned


def extract_safety_metadata(arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    upstream = dict(arguments)
    metadata: dict[str, Any] = {}
    for key in list(upstream):
        if key.startswith("_"):
            metadata[key] = upstream.pop(key)
    return upstream, metadata


def validate_bounds(name: str, arguments: dict[str, Any], observation: Observation) -> None:
    if name not in {"tap_screen", "swipe_screen"}:
        return
    if observation.width is None or observation.height is None:
        raise PolicyViolation("screen bounds are unknown; observe get_screen_info first")
    coordinates: list[tuple[float, float]] = []
    if name == "tap_screen":
        coordinates.append((float(arguments["x"]), float(arguments["y"])))
    else:
        coordinates.extend(
            [
                (float(arguments["fromX"]), float(arguments["fromY"])),
                (float(arguments["toX"]), float(arguments["toY"])),
            ]
        )
    for x, y in coordinates:
        if not (0 <= x < observation.width and 0 <= y < observation.height):
            raise PolicyViolation(
                f"coordinate ({x}, {y}) outside {observation.width}x{observation.height}"
            )
