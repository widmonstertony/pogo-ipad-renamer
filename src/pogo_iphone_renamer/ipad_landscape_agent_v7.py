from __future__ import annotations

import argparse
import time

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v5 as v5
from .appraisal_agent import Snapshot
from .config import Settings
from .deterministic_appraisal_agent import run as run_portrait
from .landscape_cv_v4 import measure_ipad14_6_appraisal_v4
from .local_ocr import (
    exact_species_from_mcp_screenshot,
    ocr_mcp_screenshot,
    rename_dialog_visible,
)
from .native_agent import emit, tool_result_message
from .native_agent_v2 import ResilientStreamableHTTPClient
from .nickname import generate_iv_nickname, iv_percent
from .policy import PolicyViolation
from .server import SafeProxy
from .species_db import traditional_chinese_species


base.measure_ipad14_6_appraisal = measure_ipad14_6_appraisal_v4


def _screenshot_only(proxy: SafeProxy) -> str:
    result = proxy.call_tool("screenshot", {})
    message = tool_result_message("screenshot", result)
    images = message.get("images")
    if not isinstance(images, list) or not images:
        raise PolicyViolation("iOS MCP 没有返回截图")
    return str(images[-1])


def _open_verified_rename_dialog(proxy: SafeProxy, snapshot: Snapshot, current_name: str) -> Snapshot:
    base._tap(proxy, "APPRAISAL_CLOSE")
    snapshot = base._next_snapshot(proxy)
    base._validate_expected("DETAIL", snapshot)
    base._tap(proxy, "NAME_PENCIL")

    # Do not wait for another accessibility tree: on this iPad the keyboard
    # replaces the text-field nodes.  A screenshot read preserves the fresh
    # post-tap observation token used by SafeProxy.
    time.sleep(0.8)
    image = _screenshot_only(proxy)
    lines = ocr_mcp_screenshot(image, base.ORIENTATION)
    if not rename_dialog_visible(lines):
        raise PolicyViolation("离线 OCR 未同时验证到“設定暱稱 / OK / 取消”；未输入文字")
    if proxy.observation is None:
        raise PolicyViolation("改名窗口缺少安全观察")
    proxy.observation.text += (
        f"\n重新命名（离线 OCR 已验证設定暱稱/OK/取消；来源物种={current_name}）"
    )
    return Snapshot(text=proxy.observation.text, image=image)


def _exact_nickname_visible(proxy: SafeProxy, nickname: str) -> bool:
    if proxy.observation is not None:
        try:
            if v5.exact_name_field(Snapshot(proxy.observation.text, None)) == nickname:
                return True
        except PolicyViolation:
            pass
    image = _screenshot_only(proxy)
    return any(
        line.text == nickname and line.confidence >= 0.65
        for line in ocr_mcp_screenshot(image, base.ORIENTATION)
    )


def _commit_and_verify(
    proxy: SafeProxy,
    snapshot: Snapshot,
    *,
    current_name: str,
    species: str,
    nickname: str,
) -> None:
    assert proxy.observation is not None
    proxy.call_tool(
        "input_text",
        {
            "text": nickname,
            "_observation_token": proxy.observation.token,
            "_intent": "rename exact default species using deterministic pixel IV nickname",
            "_expected_after": "rename field contains exact deterministic nickname",
            "_current_name": current_name,
            "_species": species,
            "_default_name_verified": True,
        },
    )
    if not _exact_nickname_visible(proxy, nickname):
        raise PolicyViolation("输入后无法逐字核验完整昵称；未点击 OK")

    base._tap(proxy, "RENAME_OK")
    snapshot = base._next_snapshot(proxy, 3.0)
    if proxy.verified_renames >= 1:
        return

    base._validate_expected("DETAIL", snapshot)
    base._tap(proxy, "NAME_PENCIL")
    time.sleep(0.8)
    if not _exact_nickname_visible(proxy, nickname):
        raise PolicyViolation("提交后重新打开字段，完整昵称核验失败")
    proxy.verified_renames += 1
    proxy.pending_name = None
    proxy.journal.append(
        "verified_rename_reopen",
        {"species": species, "old_name": current_name, "new_name": nickname},
    )
    if proxy.observation is not None:
        proxy.observation.text += "\n重新命名（提交后已逐字核验）"
    v5.cancel_name_field(proxy)


def run(mode: str, settings: Settings, ollama_url: str, model: str) -> int:
    client = ResilientStreamableHTTPClient(settings, timeout=120.0)
    device = base._device_details(client.call_tool("get_device_info", {}))
    machine = str(device.get("machine", ""))
    if machine != "iPad14,6":
        emit("status", message=f"设备 {machine or '未知'} 转入竖屏执行器。")
        return run_portrait(mode, settings, ollama_url, model)

    emit(
        "device",
        name=str(device.get("deviceName", "iPad")),
        machine=machine,
        system=str(device.get("systemName", "iPadOS")),
        version=str(device.get("systemVersion", "")),
        width=device.get("screenWidth"),
        height=device.get("screenHeight"),
    )
    emit("status", message="iPad14,6 横屏触控映射已启用；不需要旋转设备。")
    emit("status", message="名称由本地离线 OCR 读取，IV 由像素条测量；模型不决定点击。")
    emit("status", message=f"本地繁中物种表已加载：{len(traditional_chinese_species())} 个名称。")

    proxy = SafeProxy(settings, client)
    snapshot = base.screen_snapshot(proxy)
    snapshot, measurement = base.navigate_to_appraisal(proxy, snapshot)
    if measurement.confidence < 0.90:
        raise PolicyViolation(f"IV 像素测量置信度不足：{measurement.confidence:.1%}")
    if not snapshot.image:
        raise PolicyViolation("鉴定截图缺失，无法读取名称")

    emit(
        "iv_measurement",
        attack=measurement.attack,
        defense=measurement.defense,
        stamina=measurement.stamina,
        confidence=measurement.confidence,
        endpoints=list(measurement.endpoints),
    )
    current_name, name_confidence = exact_species_from_mcp_screenshot(
        snapshot.image, base.ORIENTATION
    )
    species = current_name
    nickname = generate_iv_nickname(
        species,
        measurement.attack,
        measurement.defense,
        measurement.stamina,
    )
    emit(
        "pokemon",
        species=species,
        current_name=current_name,
        attack=measurement.attack,
        defense=measurement.defense,
        stamina=measurement.stamina,
        percent=iv_percent(measurement.attack, measurement.defense, measurement.stamina),
        nickname=nickname,
        confidence=measurement.confidence,
        name_confidence=name_confidence,
    )

    if mode == "scan":
        base._tap(proxy, "APPRAISAL_CLOSE")
        detail = base._next_snapshot(proxy, 1.5)
        base._validate_expected("DETAIL", detail)
        emit("finished", message="横屏鉴定扫描完成；未打开键盘，未修改昵称。")
        return 0

    rename_snapshot = _open_verified_rename_dialog(proxy, snapshot, current_name)
    _commit_and_verify(
        proxy,
        rename_snapshot,
        current_name=current_name,
        species=species,
        nickname=nickname,
    )
    emit("renamed", nickname=nickname)
    emit("finished", message=f"横屏改名并逐字复核成功：{nickname}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="iPad landscape offline-OCR Pokémon GO renamer")
    parser.add_argument("--mode", choices=("scan", "rename"), required=True)
    parser.add_argument("--ollama-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default="qwen3.8:27b")
    args = parser.parse_args(argv)
    try:
        return run(args.mode, Settings.from_env(), args.ollama_url, args.model)
    except Exception as exc:
        emit("error", message=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
