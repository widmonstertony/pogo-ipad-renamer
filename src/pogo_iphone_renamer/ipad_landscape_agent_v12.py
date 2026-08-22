from __future__ import annotations

import sys
import unicodedata


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
from .ipad_landscape_agent_v10 import _field_value, _mark_rename_observation  # noqa: E402
from .local_ocr_v2 import exact_species_from_name_region  # noqa: E402
from .native_agent import emit  # noqa: E402
from .policy import PolicyViolation  # noqa: E402
from .server import SafeProxy  # noqa: E402


def clear_key_count(current_name: str) -> int:
    normalized = unicodedata.normalize("NFC", current_name).strip()
    if not normalized:
        raise PolicyViolation("原名称为空，无法计算安全退格次数")
    return len(normalized)


def _backspace_current_name(proxy: SafeProxy, current_name: str) -> int:
    count = clear_key_count(current_name)
    for index in range(count):
        if proxy.observation is None:
            raise PolicyViolation("退格清空时缺少安全观察")
        proxy.call_tool(
            "press_key",
            {
                "key": "backspace",
                "_observation_token": proxy.observation.token,
                "_intent": "rename clear one original-name character by backspace",
                "_expected_after": f"rename field removed original character {index + 1}/{count}",
            },
        )
    return count


def _commit_and_verify(
    proxy: SafeProxy,
    snapshot: Snapshot,
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
    if proxy.verified_renames >= 1:
        return
    base._validate_expected("DETAIL", detail)

    base._tap(proxy, "NAME_PENCIL")
    try:
        committed_value = _field_value(proxy)
    except PolicyViolation:
        base._next_snapshot(proxy, 0.5)
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
