from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .native_agent import emit, tool_result_message
from .native_agent_v2 import ResilientStreamableHTTPClient
from .nickname import generate_iv_nickname, iv_percent
from .policy import PolicyViolation
from .server import SafeProxy


NAVIGATION_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "screen_state": {
            "type": "string",
            "enum": [
                "MAP",
                "MAIN_MENU",
                "INVENTORY",
                "DETAIL",
                "DETAIL_MENU",
                "APPRAISAL",
                "RENAME_DIALOG",
                "UNKNOWN",
            ],
        },
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "action": {"type": "string", "enum": ["tap", "swipe", "ready", "stop"]},
        "target_label": {"type": "string"},
        "x": {"type": "number"},
        "y": {"type": "number"},
        "from_x": {"type": "number"},
        "from_y": {"type": "number"},
        "to_x": {"type": "number"},
        "to_y": {"type": "number"},
        "expected_after": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": [
        "screen_state",
        "confidence",
        "action",
        "target_label",
        "x",
        "y",
        "from_x",
        "from_y",
        "to_x",
        "to_y",
        "expected_after",
        "reason",
    ],
    "additionalProperties": False,
}


APPRAISAL_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "appraisal_visible": {"type": "boolean"},
        "species_text": {"type": "string"},
        "current_name": {"type": "string"},
        "default_name_verified": {"type": "boolean"},
        "attack": {"type": "integer", "minimum": -1, "maximum": 15},
        "defense": {"type": "integer", "minimum": -1, "maximum": 15},
        "stamina": {"type": "integer", "minimum": -1, "maximum": 15},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "reason": {"type": "string"},
    },
    "required": [
        "appraisal_visible",
        "species_text",
        "current_name",
        "default_name_verified",
        "attack",
        "defense",
        "stamina",
        "confidence",
        "reason",
    ],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Snapshot:
    text: str
    image: str | None


class StructuredVisionClient:
    def __init__(self, base_url: str, model: str, timeout: float = 600.0) -> None:
        self.url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self.timeout = timeout

    def analyze(
        self,
        *,
        prompt: str,
        image: str | None,
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "user", "content": prompt}
        if image:
            message["images"] = [image]
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是本地 Pokémon GO 繁中界面视觉解析器。"
                        "只根据当前截图和结构化屏幕文本返回符合 schema 的 JSON。"
                        "不确定时降低 confidence 或返回 UNKNOWN/-1；绝不猜测。"
                    ),
                },
                message,
            ],
            "format": schema,
            "stream": False,
            "keep_alive": "30m",
            "options": {"num_ctx": 16384, "temperature": 0},
        }
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                request = urllib.request.Request(
                    self.url,
                    data=data,
                    method="POST",
                    headers={"Content-Type": "application/json", "Accept": "application/json"},
                )
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                content = str(body.get("message", {}).get("content", "")).strip()
                content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content)
                value = json.loads(content)
                if not isinstance(value, dict):
                    raise ValueError("structured response is not an object")
                return value
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
                last_error = exc
                if attempt == 0:
                    continue
        raise RuntimeError(f"本地视觉模型未返回有效 JSON：{last_error}")


def screen_snapshot(proxy: SafeProxy) -> Snapshot:
    description = proxy.call_tool(
        "describe_screen",
        {"include_screenshot": False, "include_ocr": True, "clickable_only": False},
    )
    screenshot = proxy.call_tool("screenshot", {})
    description_message = tool_result_message("describe_screen", description)
    screenshot_message = tool_result_message("screenshot", screenshot)
    text = str(description_message.get("content", ""))
    if len(text) > 22_000:
        text = text[:22_000] + "\n[screen text truncated]"
    images = screenshot_message.get("images")
    image = str(images[-1]) if isinstance(images, list) and images else None
    return Snapshot(text=text, image=image)


def navigation_prompt(snapshot: Snapshot, goal: str) -> str:
    return f"""\
目标：{goal}

识别当前 Pokémon GO 繁体中文页面，并选择唯一安全的下一步。截图坐标就是 iPhone screen points。

允许的导航序列：
- MAP：点底部中央精灵球主菜单。
- MAIN_MENU：点“寶可夢”进入宝可梦盒。
- INVENTORY：点第一张可见宝可梦卡片进入单只详情。
- DETAIL：若目标是鉴定，点右下角菜单；若目标是改名，点名称旁铅笔。
- DETAIL_MENU：只能点“鑑定/寶可夢鑑定”，绝不能点传送。
- APPRAISAL：鉴定目标返回 ready；改名目标可点关闭鉴定的 X/完成区域。
- RENAME_DIALOG：返回 ready，让确定性程序输入或确认。

若画面未知、锁屏、弹窗、传送相关或置信度不足，action=stop。
不要点击强化、进化、招式、购买、交换、传送或删除。

结构化屏幕文本：
{snapshot.text}
"""


def target_allowed(state: str, goal: str, label: str, action: str) -> bool:
    if action in {"ready", "stop"}:
        return True
    lowered = label.casefold()
    forbidden = ("transfer", "傳送", "传送", "delete", "刪除", "删除", "強化", "进化", "進化")
    if any(term in lowered for term in forbidden):
        return False
    terms: dict[str, tuple[str, ...]] = {
        "MAP": ("精靈球", "poké ball", "pokeball", "主選單", "menu"),
        "MAIN_MENU": ("寶可夢", "pokemon", "pokémon"),
        "INVENTORY": ("寶可夢", "pokemon", "card", "第一", "first"),
        "DETAIL_MENU": ("鑑定", "apprais"),
        "RENAME_DIALOG": ("確認", "確定", "ok", "完成", "confirm"),
    }
    if state == "DETAIL":
        allowed = ("鉛筆", "pencil", "edit", "名稱", "name") if goal.startswith("OPEN_RENAME") else ("選單", "menu", "更多", "more")
        return any(term in lowered for term in allowed)
    if state == "APPRAISAL":
        return goal.startswith("OPEN_RENAME") and any(term in lowered for term in ("關閉", "close", "完成", "done", "x"))
    allowed = terms.get(state)
    return bool(allowed and any(term in lowered for term in allowed))


def perform_navigation(proxy: SafeProxy, decision: dict[str, Any], goal: str) -> None:
    if proxy.observation is None:
        raise PolicyViolation("missing fresh observation")
    state = str(decision["screen_state"])
    action = str(decision["action"])
    label = str(decision["target_label"])
    if float(decision["confidence"]) < 0.86:
        raise PolicyViolation(f"screen confidence too low: {decision['confidence']}")
    if not target_allowed(state, goal, label, action):
        raise PolicyViolation(f"navigation target is not allowed: {state}/{label}")
    metadata = {
        "_observation_token": proxy.observation.token,
        "_intent": f"navigate {label} for appraisal rename workflow",
        "_expected_after": str(decision["expected_after"]),
    }
    if action == "tap":
        proxy.call_tool(
            "tap_screen",
            {"x": float(decision["x"]), "y": float(decision["y"]), **metadata},
        )
    elif action == "swipe":
        proxy.call_tool(
            "swipe_screen",
            {
                "fromX": float(decision["from_x"]),
                "fromY": float(decision["from_y"]),
                "toX": float(decision["to_x"]),
                "toY": float(decision["to_y"]),
                **metadata,
            },
        )
    else:
        raise PolicyViolation(f"not a navigation action: {action}")


def appraisal_prompt(snapshot: Snapshot) -> str:
    return f"""\
这是 Pokémon GO 繁中“鑑定”页。读取顶部当前名称/物种，并读取三条橙色鉴定条：攻擊、防禦、HP。
每条范围 0–15，两个内部刻度分别是 5 和 10；只在填充终点清晰对齐时给出精确整数。
不要根据星级或总百分比反推。无法精确确认就填 -1 并降低 confidence。
default_name_verified 只有在当前名称确实是完整繁中默认物种名时才为 true。

结构化屏幕文本：
{snapshot.text}
"""


def run(mode: str, settings: Settings, ollama_url: str, model: str) -> int:
    proxy = SafeProxy(settings, ResilientStreamableHTTPClient(settings, timeout=120.0))
    vision = StructuredVisionClient(ollama_url, model)
    goal = "OPEN_APPRAISAL_FOR_CURRENT_POKEMON"
    emit("status", message="正在寻找当前宝可梦并打开鉴定页…")
    appraisal: dict[str, Any] | None = None
    nickname: str | None = None

    for step in range(1, 15):
        snapshot = screen_snapshot(proxy)
        decision = vision.analyze(
            prompt=navigation_prompt(snapshot, goal),
            image=snapshot.image,
            schema=NAVIGATION_SCHEMA,
        )
        state = str(decision["screen_state"])
        action = str(decision["action"])
        emit("navigation", state=state, target=str(decision["target_label"]), step=step)
        if action == "stop":
            emit("error", message=f"无法安全继续：{decision['reason']}")
            return 2

        if goal.startswith("OPEN_APPRAISAL") and state == "APPRAISAL" and action == "ready":
            appraisal = vision.analyze(
                prompt=appraisal_prompt(snapshot),
                image=snapshot.image,
                schema=APPRAISAL_SCHEMA,
            )
            values = (int(appraisal["attack"]), int(appraisal["defense"]), int(appraisal["stamina"]))
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

        if goal.startswith("OPEN_RENAME") and state == "RENAME_DIALOG" and action == "ready":
            assert appraisal is not None and nickname is not None and proxy.observation is not None
            species = str(appraisal["species_text"]).strip()
            current = str(appraisal["current_name"]).strip()
            proxy.call_tool(
                "input_text",
                {
                    "text": nickname,
                    "_observation_token": proxy.observation.token,
                    "_intent": "rename default-name Pokemon using deterministic appraisal IV nickname",
                    "_expected_after": "rename field contains exact deterministic nickname",
                    "_current_name": current,
                    "_species": species,
                    "_default_name_verified": True,
                },
            )
            goal = f"CONFIRM_RENAME_{nickname}"
            continue

        if goal.startswith("CONFIRM_RENAME") and state == "RENAME_DIALOG" and action == "tap":
            perform_navigation(proxy, decision, goal)
            assert nickname is not None
            if proxy.verified_renames < 1:
                emit("error", message="昵称提交后未能验证，已停止。")
                return 4
            emit("renamed", nickname=nickname)
            emit("finished", message=f"改名成功：{nickname}")
            return 0

        if action in {"tap", "swipe"}:
            perform_navigation(proxy, decision, goal)
            continue
        emit("error", message=f"页面状态与目标不匹配：{state}/{goal}")
        return 5

    emit("error", message="达到导航上限，已停止，未继续盲点。")
    return 6


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pokémon GO appraisal scanner and renamer")
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

