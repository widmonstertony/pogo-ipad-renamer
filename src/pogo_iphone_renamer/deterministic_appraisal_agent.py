from __future__ import annotations

import argparse
import copy
import json
import re
import time
from typing import Any

from .appraisal_agent import (
    APPRAISAL_SCHEMA,
    NAVIGATION_SCHEMA,
    Snapshot,
    StructuredVisionClient,
    appraisal_prompt,
    perform_navigation,
    screen_snapshot,
)
from .config import Settings
from .native_agent import emit
from .native_agent_v2 import ResilientStreamableHTTPClient
from .nickname import generate_iv_nickname, iv_percent
from .policy import PolicyViolation
from .server import SafeProxy


DETERMINISTIC_NAVIGATION_SCHEMA = copy.deepcopy(NAVIGATION_SCHEMA)
DETERMINISTIC_NAVIGATION_SCHEMA["properties"]["content_orientation"] = {
    "type": "string",
    "enum": [
        "PORTRAIT_UPRIGHT",
        "ROTATED_90_CLOCKWISE",
        "ROTATED_90_COUNTERCLOCKWISE",
        "UNKNOWN",
    ],
}
DETERMINISTIC_NAVIGATION_SCHEMA["required"].append("content_orientation")


def deterministic_navigation_prompt(snapshot: Snapshot, goal: str) -> str:
    return f"""\
目标：{goal}

你只负责判断 Pokémon GO 当前页面状态和游戏内容方向，不负责决定点击坐标。
content_orientation 描述截图内人物、文字和按钮的真实朝向；若内容横着显示，必须返回 ROTATED_90_*。
x/y/from/to 字段全部填 0，确定性程序会使用固定锚点或 OCR 文字坐标。

页面状态与意图：
- MAP：准备打开底部中央精灵球。
- MAIN_MENU：准备点击“寶可夢”。
- INVENTORY：准备打开第一只可见宝可梦。
- DETAIL：鉴定目标准备打开右下角更多菜单；改名目标准备点击名称。
- DETAIL_MENU：只能准备选择“鑑定”，绝不能选择传送。
- APPRAISAL：鉴定目标 action=ready；改名目标准备关闭鉴定。
- RENAME_DIALOG：输入前 action=ready；输入后准备确认。
- 画面未知、弹窗、锁屏或任何传送相关页面：action=stop。

结构化屏幕文本：
{snapshot.text}
"""


def _walk_for_ocr(value: Any) -> list[dict[str, Any]] | None:
    if isinstance(value, dict):
        found = value.get("ocr_texts")
        if isinstance(found, list):
            return [item for item in found if isinstance(item, dict)]
        for child in value.values():
            result = _walk_for_ocr(child)
            if result is not None:
                return result
    elif isinstance(value, list):
        for child in value:
            result = _walk_for_ocr(child)
            if result is not None:
                return result
    return None


def ocr_items(snapshot: Snapshot) -> list[dict[str, Any]]:
    decoder = json.JSONDecoder()
    for match in re.finditer(r"\{", snapshot.text):
        try:
            value, _ = decoder.raw_decode(snapshot.text[match.start() :])
        except json.JSONDecodeError:
            continue
        result = _walk_for_ocr(value)
        if result is not None:
            return result
    return []


def ocr_tap(snapshot: Snapshot, terms: tuple[str, ...]) -> tuple[float, float, str] | None:
    for item in ocr_items(snapshot):
        label = str(item.get("text", "")).strip()
        lowered = label.casefold()
        tap = item.get("tap")
        if (
            any(term.casefold() in lowered for term in terms)
            and isinstance(tap, dict)
            and isinstance(tap.get("x"), (int, float))
            and isinstance(tap.get("y"), (int, float))
        ):
            return float(tap["x"]), float(tap["y"]), label
    return None


def deterministic_navigation(
    decision: dict[str, Any],
    snapshot: Snapshot,
    proxy: SafeProxy,
    goal: str,
    current_name: str = "",
) -> dict[str, Any]:
    """Discard model coordinates and resolve the next action from a known anchor."""
    state = str(decision["screen_state"])
    action = str(decision["action"])
    if action in {"ready", "stop"}:
        return decision

    orientation = str(decision.get("content_orientation", "UNKNOWN"))
    if orientation != "PORTRAIT_UPRIGHT":
        raise PolicyViolation(
            "游戏内容与 MCP 触控坐标方向不一致（"
            + orientation
            + "）。请把设备和 Pokémon GO 转成竖屏后重试；已停止，未点击。"
        )
    observation = proxy.observation
    if observation is None or observation.width is None or observation.height is None:
        raise PolicyViolation("MCP 没有返回可验证的屏幕边界")
    width = float(observation.width)
    height = float(observation.height)
    resolved = dict(decision)

    if state == "MAP":
        resolved.update(
            action="tap",
            target_label="精靈球主選單（固定锚点）",
            x=width * 0.5,
            y=height * 0.895,
            expected_after="MAIN_MENU",
        )
        return resolved

    if state == "MAIN_MENU":
        target = ocr_tap(snapshot, ("寶可夢", "宝可梦", "pokémon", "pokemon"))
        if target is None:
            raise PolicyViolation("主菜单已识别，但 OCR 未确认“寶可夢”按钮；已停止，未猜坐标。")
        x, y, label = target
        resolved.update(
            action="tap",
            target_label=f"寶可夢（OCR：{label}）",
            x=x,
            y=y,
            expected_after="INVENTORY",
        )
        return resolved

    if state == "INVENTORY":
        resolved.update(
            action="tap",
            target_label="第一只可见寶可夢（固定网格锚点）",
            x=width * 0.18,
            y=height * 0.285,
            expected_after="DETAIL",
        )
        return resolved

    if state == "DETAIL" and not goal.startswith("OPEN_RENAME"):
        resolved.update(
            action="tap",
            target_label="更多選單（固定锚点）",
            x=width * 0.91,
            y=height * 0.92,
            expected_after="DETAIL_MENU",
        )
        return resolved

    if state == "DETAIL_MENU":
        target = ocr_tap(snapshot, ("鑑定", "鉴定", "appraise", "appraisal"))
        if target is None:
            raise PolicyViolation("详情菜单已识别，但 OCR 未确认“鑑定”；已停止，绝不点击相邻的传送。")
        x, y, label = target
        resolved.update(
            action="tap",
            target_label=f"鑑定（OCR：{label}）",
            x=x,
            y=y,
            expected_after="APPRAISAL",
        )
        return resolved

    if state == "APPRAISAL" and goal.startswith("OPEN_RENAME"):
        resolved.update(
            action="tap",
            target_label="關閉鑑定（固定锚点）",
            x=width * 0.5,
            y=height * 0.94,
            expected_after="DETAIL",
        )
        return resolved

    if state == "DETAIL" and goal.startswith("OPEN_RENAME"):
        target = ocr_tap(snapshot, (current_name,)) if current_name else None
        if target is None:
            raise PolicyViolation("详情页未能用 OCR 精确定位当前名称；已停止，未猜铅笔坐标。")
        x, y, label = target
        resolved.update(
            action="tap",
            target_label=f"名稱（OCR：{label}）",
            x=x,
            y=y,
            expected_after="RENAME_DIALOG",
        )
        return resolved

    if state == "RENAME_DIALOG" and goal.startswith("CONFIRM_RENAME"):
        target = ocr_tap(snapshot, ("確定", "确认", "確認", "完成", "ok"))
        if target is None:
            raise PolicyViolation("改名窗口未能用 OCR 精确定位确认按钮；已停止，昵称尚未提交。")
        x, y, label = target
        resolved.update(
            action="tap",
            target_label=f"確認（OCR：{label}）",
            x=x,
            y=y,
            expected_after="DETAIL",
        )
        return resolved

    raise PolicyViolation(f"当前步骤没有确定性点击锚点：{state}/{goal}")


def _device_details(result: dict[str, Any]) -> dict[str, Any]:
    structured = result.get("structuredContent")
    return structured if isinstance(structured, dict) else {}


def run(mode: str, settings: Settings, ollama_url: str, model: str) -> int:
    client = ResilientStreamableHTTPClient(settings, timeout=120.0)
    device = _device_details(client.call_tool("get_device_info", {}))
    emit(
        "device",
        name=str(device.get("deviceName", "未知设备")),
        machine=str(device.get("machine", "未知型号")),
        system=str(device.get("systemName", "iOS/iPadOS")),
        version=str(device.get("systemVersion", "未知版本")),
        width=device.get("screenWidth"),
        height=device.get("screenHeight"),
    )
    proxy = SafeProxy(settings, client)
    vision = StructuredVisionClient(ollama_url, model)
    goal = "OPEN_APPRAISAL_FOR_CURRENT_POKEMON"
    emit("status", message="正在识别页面；模型不再决定任何点击坐标。")
    appraisal: dict[str, Any] | None = None
    nickname: str | None = None

    for step in range(1, 15):
        snapshot = screen_snapshot(proxy)
        decision = vision.analyze(
            prompt=deterministic_navigation_prompt(snapshot, goal),
            image=snapshot.image,
            schema=DETERMINISTIC_NAVIGATION_SCHEMA,
        )
        state = str(decision["screen_state"])
        action = str(decision["action"])
        emit(
            "navigation",
            state=state,
            orientation=str(decision.get("content_orientation", "UNKNOWN")),
            step=step,
        )
        if action == "stop":
            emit("error", message=f"无法安全继续：{decision['reason']}")
            return 2

        if goal.startswith("OPEN_APPRAISAL") and state == "APPRAISAL" and action == "ready":
            appraisal = vision.analyze(
                prompt=appraisal_prompt(snapshot),
                image=snapshot.image,
                schema=APPRAISAL_SCHEMA,
            )
            values = (
                int(appraisal["attack"]),
                int(appraisal["defense"]),
                int(appraisal["stamina"]),
            )
            if (
                not appraisal["appraisal_visible"]
                or any(value < 0 or value > 15 for value in values)
                or float(appraisal["confidence"]) < 0.94
            ):
                emit("error", message=f"鉴定值不够确定，已停止：{appraisal['reason']}")
                return 3
            species = str(appraisal["species_text"]).strip()
            current = str(appraisal["current_name"]).strip()
            nickname = generate_iv_nickname(species, *values)
            emit(
                "pokemon",
                species=species,
                current_name=current,
                attack=values[0],
                defense=values[1],
                stamina=values[2],
                percent=iv_percent(*values),
                nickname=nickname,
                confidence=float(appraisal["confidence"]),
            )
            if mode == "scan":
                emit("finished", message="鉴定扫描完成；没有修改昵称。")
                return 0
            if not appraisal["default_name_verified"] or current != species:
                emit("finished", message="检测到已有自定义昵称，已保留并跳过。")
                return 0
            goal = f"OPEN_RENAME_DIALOG_FOR_{nickname}"
            continue

        current_name = str(appraisal["current_name"]).strip() if appraisal else ""

        if goal.startswith("OPEN_RENAME") and state == "RENAME_DIALOG" and action == "ready":
            assert appraisal is not None and nickname is not None and proxy.observation is not None
            species = str(appraisal["species_text"]).strip()
            proxy.call_tool(
                "input_text",
                {
                    "text": nickname,
                    "_observation_token": proxy.observation.token,
                    "_intent": "rename default-name Pokemon using deterministic appraisal IV nickname",
                    "_expected_after": "rename field contains exact deterministic nickname",
                    "_current_name": current_name,
                    "_species": species,
                    "_default_name_verified": True,
                },
            )
            goal = f"CONFIRM_RENAME_{nickname}"
            continue

        if action in {"tap", "swipe"}:
            resolved = deterministic_navigation(decision, snapshot, proxy, goal, current_name)
            perform_navigation(proxy, resolved, goal)
            if goal.startswith("CONFIRM_RENAME"):
                time.sleep(1.0)
                proxy.call_tool(
                    "describe_screen",
                    {"clickable_only": True, "include_ocr": True, "include_screenshot": False},
                )
                if proxy.verified_renames < 1:
                    emit("error", message="昵称提交后未能验证，已停止。")
                    return 4
                emit("renamed", nickname=nickname)
                emit("finished", message=f"改名成功：{nickname}")
                return 0
            continue

        emit("error", message=f"页面状态与目标不匹配：{state}/{goal}")
        return 5

    emit("error", message="达到导航上限，已停止，未继续盲点。")
    return 6


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic Pokémon GO appraisal scanner")
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
