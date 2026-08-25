from __future__ import annotations

"""Small, local-only live monitor shared by the worker and Tk control window.

The monitor deliberately consumes the screenshots already requested by the
batch worker.  It never makes a second MCP request just to update the desktop
UI, and it records only the current on-device view in ``.pogo-data``.
"""

import base64
import io
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image


_PREVIEW_SIZE = (330, 440)


def live_activity_paths(root: Path) -> tuple[Path, Path]:
    data = root / ".pogo-data"
    return data / "live-activity.json", data / "live-preview.jpg"


def _configured_path(variable: str, fallback: Path) -> Path:
    value = os.getenv(variable, "").strip()
    return Path(value) if value else fallback


def _activity_path() -> Path:
    journal = Path(os.getenv("POGO_JOURNAL_PATH", ".pogo-data/actions.jsonl"))
    return _configured_path("POGO_LIVE_ACTIVITY_PATH", journal.parent / "live-activity.json")


def _preview_path() -> Path:
    journal = Path(os.getenv("POGO_JOURNAL_PATH", ".pogo-data/actions.jsonl"))
    return _configured_path("POGO_LIVE_PREVIEW_PATH", journal.parent / "live-preview.jpg")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _display_detail(event: dict[str, Any]) -> str:
    current_name = str(event.get("current_name", "")).strip()
    species = str(event.get("species", "")).strip()
    return current_name or species or "正在确认名称…"


def update_live_activity(event: dict[str, Any], *, path: Path | None = None) -> dict[str, Any]:
    """Merge one worker event into a compact GUI-facing activity snapshot."""

    destination = path or _activity_path()
    activity = _read(destination)
    event_type = str(event.get("type", ""))
    activity["updated_at"] = _now()

    if event_type == "progress":
        activity["progress"] = {
            key: event.get(key)
            for key in ("current", "limit", "phase", "renamed", "skipped", "scanned", "unreadable")
            if key in event
        }
    elif event_type == "status":
        message = str(event.get("message", "")).strip()
        if message:
            activity["step"] = message
    elif event_type == "navigation":
        activity["screen"] = str(event.get("state", "UNKNOWN"))
        activity["step"] = str(event.get("state", "UNKNOWN"))
    elif event_type == "detail":
        activity["pokemon"] = {
            "name": _display_detail(event),
            "species": str(event.get("species", "")).strip(),
            "is_default": bool(event.get("is_default")),
        }
        activity["screen"] = "DETAIL"
    elif event_type == "iv_measurement":
        activity["iv"] = {
            "attack": event.get("attack"),
            "defense": event.get("defense"),
            "stamina": event.get("stamina"),
            "confidence": event.get("confidence"),
        }
        activity["screen"] = "APPRAISAL_BARS"
    elif event_type == "pokemon":
        activity["pokemon"] = {
            "name": _display_detail(event),
            "species": str(event.get("species", "")).strip(),
            "is_default": True,
        }
        activity["iv"] = {
            "attack": event.get("attack"),
            "defense": event.get("defense"),
            "stamina": event.get("stamina"),
            "percent": event.get("percent"),
            "confidence": event.get("confidence"),
        }
        activity["nickname"] = str(event.get("nickname", "")).strip()
    elif event_type == "renamed":
        activity["last_result"] = "已改名并核验"
        activity["nickname"] = str(event.get("nickname", "")).strip()
    elif event_type == "error":
        activity["step"] = str(event.get("message", "错误")).strip()
        activity["last_result"] = "发生安全错误"

    _write(destination, activity)
    return activity


def publish_preview(image_base64: str | None, *, path: Path | None = None) -> bool:
    """Save a small upright preview from a frame the worker already captured."""

    if not image_base64:
        return False
    try:
        from .landscape_cv import rotate_mcp_image_upright

        image = rotate_mcp_image_upright(image_base64, "STAGE_MANAGER_MAXIMIZED")
        image.thumbnail(_PREVIEW_SIZE, Image.Resampling.LANCZOS)
        destination = path or _preview_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        image.save(temporary, format="JPEG", quality=82, optimize=True)
        temporary.replace(destination)
        return True
    except (OSError, ValueError, base64.binascii.Error):
        return False

