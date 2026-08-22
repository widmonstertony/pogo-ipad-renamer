from __future__ import annotations

import argparse
from pathlib import Path

from pogo_iphone_renamer import ipad_landscape_agent as base
from pogo_iphone_renamer.appraisal_agent import screen_snapshot
from pogo_iphone_renamer.config import Settings
from pogo_iphone_renamer.device_run_lock import DeviceRunLock
from pogo_iphone_renamer.ipad_landscape_agent_v20 import (
    _field_value_from_all_elements,
)
from pogo_iphone_renamer.local_ocr import ocr_mcp_screenshot, rename_dialog_visible
from pogo_iphone_renamer.native_agent_v2 import ResilientStreamableHTTPClient
from pogo_iphone_renamer.policy import PolicyViolation, normalize_text, validate_poke_genie_name
from pogo_iphone_renamer.rename_controls_v20 import tap_ok
from pogo_iphone_renamer.server import SafeProxy


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Commit a crash-left rename only after exact live field verification"
    )
    parser.add_argument("--species", required=True)
    parser.add_argument("--expected", required=True)
    args = parser.parse_args()

    validate_poke_genie_name(args.expected, args.species)
    settings = Settings.from_env()
    lock = settings.journal_path.parent / "iphone-mcp.lock"
    with DeviceRunLock(lock):
        proxy = SafeProxy(
            settings, ResilientStreamableHTTPClient(settings, timeout=120.0)
        )
        snapshot = screen_snapshot(proxy)
        if not snapshot.image or not rename_dialog_visible(
            ocr_mcp_screenshot(snapshot.image, base.ORIENTATION)
        ):
            raise PolicyViolation("当前画面没有通过 OCR 验证的完整改名弹窗")
        actual = _field_value_from_all_elements(proxy)
        if normalize_text(actual) != normalize_text(args.expected):
            raise PolicyViolation(
                f"当前输入字段与预期昵称不一致：{actual!r} != {args.expected!r}"
            )
        if proxy.observation is None:
            raise PolicyViolation("提交前缺少安全观察")
        proxy.pending_name = normalize_text(args.expected)
        proxy.observation.text += (
            f"\n重新命名（实时 accessibility 逐字一致：{args.expected}；"
            "本地 OCR 验证設定暱稱/OK/取消）"
        )
        tap_ok(proxy)
        detail = base._next_snapshot(proxy, 3.0)
        base._validate_expected("DETAIL", detail)
        if detail.image and rename_dialog_visible(
            ocr_mcp_screenshot(detail.image, base.ORIENTATION)
        ):
            raise PolicyViolation("点击 OK 后改名弹窗仍显示；未记录成功")
        if proxy.pending_name is not None:
            proxy.verified_renames += 1
            proxy.pending_name = None
            proxy.journal.append(
                "verified_rename_recovered_after_process_exit",
                {
                    "species": args.species,
                    "new_name": args.expected,
                    "evidence": "exact live field + OCR dialog + OCR OK + dialog gone + DETAIL",
                },
            )
        print(f"RECOVERED_RENAME_OK {args.expected}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
