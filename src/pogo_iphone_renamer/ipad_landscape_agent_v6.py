from __future__ import annotations

from . import ipad_landscape_agent as base
from . import ipad_landscape_agent_v5 as v5
from .appraisal_agent import Snapshot
from .native_agent import tool_result_message
from .policy import PolicyViolation
from .server import SafeProxy


def _observation_snapshot(proxy: SafeProxy) -> Snapshot:
    """Return the accessibility frame captured immediately after a safe write."""
    if proxy.observation is None:
        raise PolicyViolation("iOS MCP 没有返回写操作后的安全观察")
    return Snapshot(text=proxy.observation.text, image=None)


def _fresh_name_field(proxy: SafeProxy) -> tuple[Snapshot, str]:
    """Read the rename value without waiting for iOS to collapse to keyboard-only UI."""
    immediate = _observation_snapshot(proxy)
    try:
        return immediate, v5.exact_name_field(immediate)
    except PolicyViolation:
        result = proxy.call_tool("get_ui_elements", {})
        message = tool_result_message("get_ui_elements", result)
        fallback = Snapshot(text=str(message.get("content", "")), image=None)
        return fallback, v5.exact_name_field(fallback)


def open_name_field(proxy: SafeProxy, snapshot: Snapshot) -> tuple[Snapshot, str]:
    base._tap(proxy, "APPRAISAL_CLOSE")
    snapshot = base._next_snapshot(proxy)
    base._validate_expected("DETAIL", snapshot)

    # SafeProxy already obtains a post-write accessibility observation.  It is
    # the only reliable frame containing both the text field and iOS controls.
    base._tap(proxy, "NAME_PENCIL")
    return _fresh_name_field(proxy)


def commit_and_verify(
    proxy: SafeProxy,
    snapshot: Snapshot,
    *,
    current_name: str,
    species: str,
    nickname: str,
) -> None:
    v5.mark_rename_screen(proxy, snapshot, current_name)
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

    # Verify the complete string in the exact post-input accessibility frame
    # before pressing OK.  No OCR or visual model is involved.
    _, entered_name = _fresh_name_field(proxy)
    if entered_name != nickname:
        raise PolicyViolation("输入后名称字段与目标昵称不完全一致；未点击 OK")

    base._tap(proxy, "RENAME_OK")
    snapshot = base._next_snapshot(proxy, 3.0)
    if proxy.verified_renames >= 1:
        return

    base._validate_expected("DETAIL", snapshot)
    base._tap(proxy, "NAME_PENCIL")
    reopened, committed_name = _fresh_name_field(proxy)
    if committed_name != nickname:
        raise PolicyViolation("提交后重新打开字段，完整昵称核验失败")
    proxy.verified_renames += 1
    proxy.pending_name = None
    proxy.journal.append(
        "verified_rename_reopen",
        {"species": species, "old_name": current_name, "new_name": nickname},
    )
    v5.mark_rename_screen(proxy, reopened, nickname)
    v5.cancel_name_field(proxy)


# Keep v5's tested state machine and replace only the two timing-sensitive
# rename-dialog operations.
v5.open_name_field = open_name_field
v5.commit_and_verify = commit_and_verify


def main(argv: list[str] | None = None) -> int:
    return v5.main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
