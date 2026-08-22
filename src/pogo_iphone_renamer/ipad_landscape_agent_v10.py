from __future__ import annotations

import sys


def _force_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


_force_utf8_stdio()

from . import ipad_landscape_agent as base  # noqa: E402
from . import ipad_landscape_agent_v5 as v5  # noqa: E402
from . import ipad_landscape_agent_v7 as v7  # noqa: E402
from .appraisal_agent import Snapshot  # noqa: E402
from .local_ocr_v2 import exact_species_from_name_region  # noqa: E402
from .native_agent import emit  # noqa: E402
from .policy import PolicyViolation  # noqa: E402
from .server import SafeProxy  # noqa: E402


EMPTY_FIELD_LABELS = {"", "文本", "text"}


def _field_value(proxy: SafeProxy) -> str:
    if proxy.observation is None:
        raise PolicyViolation("改名窗口缺少安全观察")
    return v5.exact_name_field(Snapshot(proxy.observation.text, None))


def _mark_rename_observation(proxy: SafeProxy, evidence: str) -> None:
    if proxy.observation is None:
        raise PolicyViolation("改名窗口缺少安全观察")
    proxy.observation.text += f"\n重新命名（{evidence}）"


def _commit_and_verify(
    proxy: SafeProxy,
    snapshot: Snapshot,
    *,
    current_name: str,
    species: str,
    nickname: str,
) -> None:
    if proxy.observation is None:
        raise PolicyViolation("改名窗口缺少安全观察")

    # iphone-mcp's input_text inserts at the current caret; it does not replace
    # the existing Pokémon name.  Clear through the exact iOS accessibility
    # control first, then require the wide text field to report its empty label.
    proxy.call_tool(
        "tap_element",
        {
            "text": "清除文本",
            "match": "exact",
            "role": "control",
            "_observation_token": proxy.observation.token,
            "_intent": "navigate clear current rename field before entering nickname",
            "_expected_after": "rename field is empty",
        },
    )
    cleared_value = _field_value(proxy)
    if cleared_value.casefold() not in EMPTY_FIELD_LABELS:
        raise PolicyViolation(f"清除旧名称后字段仍有内容：{cleared_value!r}；未输入新昵称")
    _mark_rename_observation(proxy, "accessibility 已验证字段为空")
    emit("status", message="旧名称已清空；正在输入并逐字核验目标昵称。")

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
    entered_value = _field_value(proxy)
    if entered_value != nickname:
        raise PolicyViolation(
            f"输入后字段不完全一致：期望 {nickname!r}，实际 {entered_value!r}；未点击 OK"
        )
    emit("status", message="完整昵称逐字核验通过；正在提交。")

    base._tap(proxy, "RENAME_OK")
    detail = base._next_snapshot(proxy, 3.0)
    if proxy.verified_renames >= 1:
        return

    base._validate_expected("DETAIL", detail)
    base._tap(proxy, "NAME_PENCIL")
    # A fresh full accessibility read is more reliable than the immediate
    # post-tap tree, which can briefly contain keyboard-only nodes.
    reopened = base._next_snapshot(proxy, 1.0)
    committed_value = _field_value(proxy)
    if committed_value != nickname:
        raise PolicyViolation(
            f"提交后重新打开字段核验失败：期望 {nickname!r}，实际 {committed_value!r}"
        )
    proxy.verified_renames += 1
    proxy.pending_name = None
    proxy.journal.append(
        "verified_rename_reopen",
        {"species": species, "old_name": current_name, "new_name": nickname},
    )
    _mark_rename_observation(proxy, "提交后 accessibility 已逐字核验")
    v5.cancel_name_field(proxy)


v7.exact_species_from_mcp_screenshot = exact_species_from_name_region
v7._commit_and_verify = _commit_and_verify


def main(argv: list[str] | None = None) -> int:
    return v7.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
