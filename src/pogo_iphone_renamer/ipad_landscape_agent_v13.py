from __future__ import annotations

import argparse
import sys


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdio()

from . import ipad_landscape_agent as base  # noqa: E402
from .config import Settings  # noqa: E402
from .deterministic_appraisal_agent import run as run_portrait  # noqa: E402
from .ipad_landscape_agent_v10 import _field_value, _mark_rename_observation  # noqa: E402
from .ipad_landscape_agent_v12 import _backspace_current_name  # noqa: E402
from .landscape_cv_v4 import measure_ipad14_6_appraisal_v4  # noqa: E402
from .local_ocr import ocr_mcp_screenshot, rename_dialog_visible  # noqa: E402
from .local_ocr_v3 import analyze_name_region  # noqa: E402
from .native_agent import emit  # noqa: E402
from .native_agent_v2 import ResilientStreamableHTTPClient  # noqa: E402
from .nickname import generate_iv_nickname, iv_percent  # noqa: E402
from .policy import PolicyViolation  # noqa: E402
from .server import SafeProxy  # noqa: E402
from .species_db import traditional_chinese_species  # noqa: E402


base.measure_ipad14_6_appraisal = measure_ipad14_6_appraisal_v4


def _commit_with_transition_verification(
    proxy: SafeProxy,
    *,
    current_name: str,
    species: str,
    nickname: str,
) -> None:
    count = _backspace_current_name(proxy, current_name)
    _mark_rename_observation(proxy, f"已发送与精确原名等长的 {count} 次退格")
    emit("status", message=f"已清除原名称的 {count} 个字符；正在输入并逐字核验目标昵称。")

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
    try:
        entered_value = _field_value(proxy)
    except PolicyViolation:
        base._next_snapshot(proxy, 0.5)
        entered_value = _field_value(proxy)
    if entered_value != nickname:
        raise PolicyViolation(
            f"输入后字段不完全一致：期望 {nickname!r}，实际 {entered_value!r}；未点击 OK"
        )
    emit("status", message="完整昵称逐字核验通过；正在提交。")

    base._tap(proxy, "RENAME_OK")
    detail = base._next_snapshot(proxy, 3.0)
    base._validate_expected("DETAIL", detail)
    if not detail.image:
        raise PolicyViolation("提交后详情页截图缺失")
    lines = ocr_mcp_screenshot(detail.image, base.ORIENTATION)
    if rename_dialog_visible(lines):
        raise PolicyViolation("点击 OK 后改名弹窗仍然可见；未记录成功")

    if proxy.verified_renames < 1:
        proxy.verified_renames += 1
        proxy.pending_name = None
        proxy.journal.append(
            "verified_rename_transition",
            {
                "species": species,
                "old_name": current_name,
                "new_name": nickname,
                "evidence": "exact pre-submit field + OK success + rename dialog gone + DETAIL",
            },
        )


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

    name_result = analyze_name_region(snapshot.image, base.ORIENTATION)
    if not name_result.is_default or not name_result.species:
        base._tap(proxy, "APPRAISAL_CLOSE")
        detail = base._next_snapshot(proxy, 1.5)
        base._validate_expected("DETAIL", detail)
        evidence = " / ".join(name_result.evidence) or "名称不是完整默认物种名"
        emit("finished", message=f"检测到已有自定义/IV昵称，已保留并跳过：{evidence}")
        return 0

    species = name_result.species
    nickname = generate_iv_nickname(
        species, measurement.attack, measurement.defense, measurement.stamina
    )
    emit(
        "pokemon",
        species=species,
        current_name=species,
        attack=measurement.attack,
        defense=measurement.defense,
        stamina=measurement.stamina,
        percent=iv_percent(measurement.attack, measurement.defense, measurement.stamina),
        nickname=nickname,
        confidence=measurement.confidence,
        name_confidence=name_result.confidence,
    )
    if mode == "scan":
        base._tap(proxy, "APPRAISAL_CLOSE")
        detail = base._next_snapshot(proxy, 1.5)
        base._validate_expected("DETAIL", detail)
        emit("finished", message="横屏鉴定扫描完成；未打开键盘，未修改昵称。")
        return 0

    rename_snapshot = v7._open_verified_rename_dialog(proxy, snapshot, species)
    _commit_with_transition_verification(
        proxy, current_name=species, species=species, nickname=nickname
    )
    emit("renamed", nickname=nickname)
    emit("finished", message=f"横屏改名成功：{nickname}")
    return 0


# Imported late to avoid circular imports in the v7 adapter chain.
from . import ipad_landscape_agent_v7 as v7  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="iPad landscape verified Pokémon GO renamer v13")
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
