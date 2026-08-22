from __future__ import annotations

import argparse
import json
import re
import time
from typing import Any

from . import ipad_landscape_agent as base
from .config import Settings
from .deterministic_appraisal_agent import run as run_portrait
from .landscape_cv_v4 import measure_ipad14_6_appraisal_v4
from .native_agent import emit
from .native_agent_v2 import ResilientStreamableHTTPClient
from .nickname import generate_iv_nickname, iv_percent
from .policy import PolicyViolation
from .server import SafeProxy
from .species_db import exact_default_species_name, traditional_chinese_species


base.measure_ipad14_6_appraisal = measure_ipad14_6_appraisal_v4


def _walk_for_elements(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, dict):
        elements = value.get("elements")
        if isinstance(elements, list):
            return [item for item in elements if isinstance(item, dict)]
        for child in value.values():
            found = _walk_for_elements(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _walk_for_elements(child)
            if found is not None:
                return found
    return None


def accessibility_elements(snapshot) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", snapshot.text):
        try:
            value, _ = decoder.raw_decode(snapshot.text[match.start() :])
        except json.JSONDecodeError:
            continue
        found = _walk_for_elements(value)
        if found is not None:
            return found
    return []


def exact_name_field(snapshot) -> str:
    candidates: list[tuple[float, str]] = []
    excluded = {"清除文本", "完成", "取消", "键盘", "鍵盤", "听写", "聽寫"}
    for element in accessibility_elements(snapshot):
        text = str(element.get("text", "")).strip()
        rect = element.get("rect")
        if not text or text in excluded or not isinstance(rect, dict):
            continue
        width = rect.get("width")
        if element.get("type") == "control" and isinstance(width, (int, float)) and width >= 200:
            candidates.append((float(width), text))
    if not candidates:
        raise PolicyViolation("改名窗口已打开，但 accessibility 未返回完整名称字段")
    return max(candidates)[1]


def open_name_field(proxy: SafeProxy, snapshot):
    base._tap(proxy, "APPRAISAL_CLOSE")
    snapshot = base._next_snapshot(proxy)
    base._validate_expected("DETAIL", snapshot)
    base._tap(proxy, "NAME_PENCIL")
    snapshot = base._next_snapshot(proxy)
    return snapshot, exact_name_field(snapshot)


def mark_rename_screen(proxy: SafeProxy, snapshot, exact_name: str) -> None:
    elements = accessibility_elements(snapshot)
    texts = {str(item.get("text", "")).strip() for item in elements}
    if exact_name not in texts or not texts.intersection({"清除文本", "完成", "取消"}):
        raise PolicyViolation("名称字段与改名控件没有同时通过 accessibility 验证")
    if proxy.observation is None:
        raise PolicyViolation("改名窗口缺少安全观察")
    proxy.observation.text += "\n重新命名（由名称字段和 iOS 编辑控件联合验证）"


def cancel_name_field(proxy: SafeProxy) -> None:
    base._tap(proxy, "RENAME_CANCEL")
    base._next_snapshot(proxy, 1.5)


def commit_and_verify(
    proxy: SafeProxy,
    snapshot,
    *,
    current_name: str,
    species: str,
    nickname: str,
) -> None:
    mark_rename_screen(proxy, snapshot, current_name)
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
    snapshot = base._next_snapshot(proxy, 1.0)
    if exact_name_field(snapshot) != nickname:
        raise PolicyViolation("输入后名称字段与目标昵称不完全一致；未点击 OK")
    base._tap(proxy, "RENAME_OK")
    snapshot = base._next_snapshot(proxy, 3.0)
    if proxy.verified_renames >= 1:
        return

    base._validate_expected("DETAIL", snapshot)
    base._tap(proxy, "NAME_PENCIL")
    snapshot = base._next_snapshot(proxy)
    if exact_name_field(snapshot) != nickname:
        raise PolicyViolation("提交后重新打开字段，完整昵称核验失败")
    proxy.verified_renames += 1
    proxy.pending_name = None
    proxy.journal.append(
        "verified_rename_reopen",
        {"species": species, "old_name": current_name, "new_name": nickname},
    )
    cancel_name_field(proxy)


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
    emit("status", message="iPad14,6 横屏配置已启用；名称读取和 IV 测量均为确定性流程。")
    emit("status", message=f"本地繁中物种表已加载：{len(traditional_chinese_species())} 个名称。")
    proxy = SafeProxy(settings, client)
    snapshot = base.screen_snapshot(proxy)
    snapshot, measurement = base.navigate_to_appraisal(proxy, snapshot)
    if measurement.confidence < 0.90:
        raise PolicyViolation(f"IV 像素测量置信度不足：{measurement.confidence:.1%}")
    emit(
        "iv_measurement",
        attack=measurement.attack,
        defense=measurement.defense,
        stamina=measurement.stamina,
        confidence=measurement.confidence,
        endpoints=list(measurement.endpoints),
    )

    snapshot, current_name = open_name_field(proxy, snapshot)
    species = exact_default_species_name(current_name)
    if species is None:
        cancel_name_field(proxy)
        emit("finished", message=f"“{current_name}”不在本地完整繁中物种集合中，视为自定义名称并跳过。")
        return 0

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
    )
    if mode == "scan":
        cancel_name_field(proxy)
        emit("finished", message="横屏鉴定扫描完成；名称来自 accessibility，未修改昵称。")
        return 0

    commit_and_verify(
        proxy,
        snapshot,
        current_name=current_name,
        species=species,
        nickname=nickname,
    )
    emit("renamed", nickname=nickname)
    emit("finished", message=f"横屏改名并逐字复核成功：{nickname}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="iPad landscape deterministic Pokémon GO renamer")
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
