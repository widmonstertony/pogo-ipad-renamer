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
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image


# The native macOS dashboard shows the iPad frame at roughly 252×318 points.
# On a Retina/4K display that is about 504×636 physical pixels.  The previous
# 330×440 JPEG was therefore being enlarged by SwiftUI and its 4:2:0 chroma
# subsampling made game text and IV bars visibly soft.  Preserve the original
# iPad capture dimensions (1024×1366) so the dashboard only downsamples.
_PREVIEW_SIZE = (1024, 1366)
_PREVIEW_JPEG_QUALITY = 95


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
    mirror_dir = os.getenv("POGO_DASHBOARD_MIRROR_DIR", "").strip()
    if mirror_dir:
        mirror = Path(mirror_dir) / path.name
        if mirror != path:
            mirror.parent.mkdir(parents=True, exist_ok=True)
            mirror_temporary = mirror.with_suffix(mirror.suffix + ".tmp")
            mirror_temporary.write_text(
                json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            mirror_temporary.replace(mirror)


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
        phase = str(event.get("phase", "")).strip()
        activity["progress"] = {
            key: event.get(key)
            for key in ("current", "limit", "phase", "renamed", "skipped", "scanned", "unreadable")
            if key in event
        }
        # ``completed`` is emitted after *one card* is safely finished, not
        # when the unlimited batch has ended.  A stale ``finished`` result
        # used to survive into the following run and made the desktop look as
        # though it had silently stopped.  Keep the per-card outcome explicit
        # and reserve ``last_result`` for a true terminal worker event.
        if phase in {"processing", "resumed", "completed"}:
            activity.pop("last_result", None)
            activity.pop("attention", None)
        if phase == "processing":
            # A new card begins before its three-frame name proof completes.
            # Do not leave the previous card's IV/nickname/result on screen:
            # that made a healthy identity wait look like the old Pokémon had
            # frozen in appraisal.
            activity.pop("item_result", None)
            activity.pop("iv", None)
            activity.pop("nickname", None)
            activity.pop("pokemon", None)
        if phase == "completed":
            # Each outcome reaches a verified detail before completion is
            # published; do not retain an earlier appraisal/rename caption.
            activity["screen"] = "DETAIL"
            current = event.get("current")
            activity["item_result"] = (
                f"第 {current} 只已安全处理；正在验证翻到下一只。"
                if current
                else "当前只已安全处理；正在验证翻到下一只。"
            )
    elif event_type == "status":
        message = str(event.get("message", "")).strip()
        if message:
            activity["step"] = message
            activity.pop("waiting", None)
            activity.pop("attention", None)
    elif event_type == "waiting":
        # A new active wait is proof that the worker is still alive.  Clear a
        # prior-run success banner before rendering the live explanation.
        activity.pop("last_result", None)
        activity.pop("attention", None)
        activity["waiting"] = {
            key: event.get(key)
            for key in (
                "stage",
                "reason",
                "attempt",
                "total",
                "elapsed_seconds",
                "next_action",
                "user_action",
            )
            if event.get(key) is not None
        }
        stage = str(event.get("stage", "正在等待")).strip()
        reason = str(event.get("reason", "")).strip()
        activity["step"] = f"{stage}：{reason}" if reason else stage
        if event.get("screen") in {"DETAIL", "APPRAISAL", "APPRAISAL_BARS", "RENAME_DIALOG"}:
            activity["screen"] = event["screen"]
        elif "详情" in stage:
            activity["screen"] = "DETAIL"
        elif "鉴定" in stage:
            activity["screen"] = "APPRAISAL"
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
    elif event_type == "maintenance":
        # Explicit service maintenance is not a running rename step. Never
        # present the previous Pokémon/IV as the current locked system screen.
        for key in ("pokemon", "iv", "nickname", "item_result", "waiting"):
            activity.pop(key, None)
        activity["screen"] = str(event.get("screen", "UNKNOWN"))
        activity["step"] = str(event.get("message", "设备维护")).strip()
        activity["last_result"] = "设备维护；真机验收尚未完成"
        activity["attention"] = {
            "required": bool(event.get("user_action")),
            "reason": activity["step"],
            "user_action": str(event.get("user_action", "无需操作。")),
        }
    elif event_type == "error":
        activity["step"] = str(event.get("message", "错误")).strip()
        activity["last_result"] = "发生安全错误"
        activity["attention"] = {
            "required": True,
            "reason": activity["step"],
            "user_action": "任务已安全停止；请在控制面板查看原因后再选择继续。",
        }
        if "axRuntimeMode: inactive" in activity["step"]:
            activity["attention"]["user_action"] = (
                "需要恢复 iPad MCP 的辅助功能服务；无需解锁或重开游戏，"
                "不要反复点开始或手动提交昵称。"
            )
        activity.pop("waiting", None)
    elif event_type == "finished":
        # A successful batch is always left on its current Pokémon detail;
        # retaining the last IV-overlay state made the dashboard look stuck
        # even after the worker had safely verified the storage end.
        activity["screen"] = "DETAIL"
        activity["step"] = str(event.get("message", "批量已完成")).strip()
        activity["last_result"] = "批量已完成"
        activity["attention"] = {
            "required": False,
            "reason": activity["step"],
            "user_action": "无需操作；任务已正常结束。",
        }
        activity.pop("waiting", None)

    _write(destination, activity)
    return activity


def publish_preview(
    image_base64: str | None,
    *,
    path: Path | None = None,
    orientation: str = "STAGE_MANAGER_MAXIMIZED",
) -> bool:
    """Save a high-density upright preview from a frame the worker captured."""

    if not image_base64:
        return False
    try:
        from .landscape_cv import rotate_mcp_image_upright

        raw_preview = os.getenv("POGO_LIVE_RAW_PREVIEW_PATH", "").strip()
        if raw_preview:
            # Keep an audit-only copy of the exact MCP pixels alongside the
            # normalized desktop preview. This adds no device read; it lets a
            # changed iPad screenshot encoding be diagnosed from evidence
            # rather than guessed from the transformed dashboard image.
            raw_destination = Path(raw_preview)
            raw_destination.parent.mkdir(parents=True, exist_ok=True)
            raw_temporary = raw_destination.with_suffix(raw_destination.suffix + ".tmp")
            raw_temporary.write_bytes(base64.b64decode(image_base64))
            raw_temporary.replace(raw_destination)
        image = rotate_mcp_image_upright(image_base64, orientation)
        image.thumbnail(_PREVIEW_SIZE, Image.Resampling.LANCZOS)
        destination = path or _preview_path()
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        image.save(
            temporary,
            format="JPEG",
            quality=_PREVIEW_JPEG_QUALITY,
            subsampling=0,
            optimize=True,
        )
        temporary.replace(destination)
        mirror_dir = os.getenv("POGO_DASHBOARD_MIRROR_DIR", "").strip()
        if mirror_dir:
            mirror = Path(mirror_dir) / destination.name
            if mirror != destination:
                mirror.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(destination, mirror)
        return True
    except (OSError, ValueError, base64.binascii.Error):
        return False
