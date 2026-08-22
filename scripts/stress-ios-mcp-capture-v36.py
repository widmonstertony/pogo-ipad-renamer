from __future__ import annotations

import argparse
import base64
import io
import json
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageStat

from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.native_agent import tool_result_message
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient


def _image_from_result(result: dict[str, Any]) -> Image.Image:
    for item in reversed(result.get("content", [])):
        if isinstance(item, dict) and item.get("type") == "image":
            return Image.open(io.BytesIO(base64.b64decode(item["data"]))).convert("RGB")
    raise RuntimeError("MCP screenshot did not contain an image")


def _text_blocks(result: dict[str, Any]) -> list[str]:
    return [
        str(item.get("text", "")).strip()
        for item in result.get("content", [])
        if isinstance(item, dict) and item.get("type") == "text" and item.get("text")
    ]


def _image_source(result: dict[str, Any]) -> str:
    for item in reversed(result.get("content", [])):
        if isinstance(item, dict) and item.get("type") == "image":
            return str(item.get("source", "unknown"))
    return "none"


def _near_black_fraction(image: Image.Image, threshold: int = 8) -> float:
    sample = image.copy()
    sample.thumbnail((256, 256))
    pixels = sample.getdata()
    black = sum(1 for red, green, blue in pixels if max(red, green, blue) <= threshold)
    return black / max(1, sample.width * sample.height)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=30)
    parser.add_argument("--interval", type=float, default=1.0)
    parser.add_argument("--progress-every", type=int, default=1)
    parser.add_argument("--stop-after-consecutive-errors", type=int, default=0)
    parser.add_argument("--save-dir", type=Path)
    args = parser.parse_args()

    client = ResilientStreamableHTTPClient(Settings.from_env(), timeout=120)
    health = client.health()
    screen = tool_result_message("get_screen_info", client.call_tool("get_screen_info", {}))
    front = tool_result_message("get_frontmost_app", client.call_tool("get_frontmost_app", {}))
    print(f"health={json.dumps(health, ensure_ascii=False, sort_keys=True)}", flush=True)
    print(f"screen={screen.get('content', '')}", flush=True)
    print(f"frontmost={front.get('content', '')}", flush=True)

    black_count = 0
    error_count = 0
    sizes: dict[str, int] = {}
    saved = 0
    consecutive_errors = 0
    attempted = 0
    for index in range(1, args.count + 1):
        attempted = index
        started = time.monotonic()
        try:
            raw = client.call_tool("screenshot", {"debug": True})
            image = _image_from_result(raw)
            source = _image_source(raw)
            consecutive_errors = 0
            fraction = _near_black_fraction(image)
            extrema = image.getextrema()
            mean = tuple(round(value, 1) for value in ImageStat.Stat(image).mean)
            all_black = all(channel[1] <= 8 for channel in extrema)
            black_count += int(all_black)
            key = f"{image.width}x{image.height}"
            sizes[key] = sizes.get(key, 0) + 1
            text = " | ".join(_text_blocks(raw)) or "-"
            elapsed = time.monotonic() - started
            if (
                index == 1
                or index == args.count
                or index % max(1, args.progress_every) == 0
                or all_black
            ):
                print(
                    f"{index:03d}/{args.count} size={key} all_black={all_black} "
                    f"near_black={fraction:.3%} mean={mean} source={source} "
                    f"elapsed={elapsed:.2f}s debug={text}",
                    flush=True,
                )
            if args.save_dir and (index in {1, args.count} or all_black):
                args.save_dir.mkdir(parents=True, exist_ok=True)
                image.save(args.save_dir / f"capture-{index:03d}.png")
                saved += 1
        except Exception as exc:  # diagnostic: continue to count intermittent failures
            error_count += 1
            consecutive_errors += 1
            print(f"{index:03d}/{args.count} ERROR {type(exc).__name__}: {exc}", flush=True)
            if (
                args.stop_after_consecutive_errors > 0
                and consecutive_errors >= args.stop_after_consecutive_errors
            ):
                print(
                    f"STOP consecutive_errors={consecutive_errors} at={index}",
                    flush=True,
                )
                break
        if index < args.count and args.interval > 0:
            time.sleep(args.interval)

    print(
        f"SUMMARY total={attempted} requested={args.count} black={black_count} errors={error_count} "
        f"sizes={json.dumps(sizes, sort_keys=True)} saved={saved}",
        flush=True,
    )
    return 1 if black_count or error_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
