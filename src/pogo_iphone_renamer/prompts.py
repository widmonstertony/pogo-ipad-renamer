from __future__ import annotations

from .config import BATCH_LIMIT_UNLIMITED


READ_ONLY_PROMPT = """\
你正在执行 Pokémon GO iPhone 改名系统的只读预演。

目标：
1. 连接 iphone_safe MCP，检查当前前台应用和屏幕。
2. 如果 Pokémon GO 已在前台，观察当前宝可梦详情页、当前繁中名称，以及 Poke Genie 可见的完整建议昵称。
3. 清楚说明：是否仍是物种默认名、准备采用的完整昵称、原本下一步会点击什么。

硬性规则：
- 绝对不要调用任何写工具，包括 launch_app、wake_and_home、tap、swipe、input_text、type_text 或 press_key。
- 不要切换应用，不要更改手机状态。
- 不要猜测 IV、招式、星标或 (+)；看不到 Poke Genie 的完整昵称就报告无法确认。
- 看到传送、Transfer、确认删除或未知界面时立即调用 pogo_abort 并停止。
- 最后用简短中文汇报观察结果。这次预演不改名。
"""


def rename_prompt(batch_limit: int) -> str:
    if batch_limit < BATCH_LIMIT_UNLIMITED:
        raise ValueError("batch_limit must be zero (unlimited) or positive")
    limit_text = (
        "持续处理，直到没有下一只或用户停止"
        if batch_limit == BATCH_LIMIT_UNLIMITED
        else f"本次最多处理 {batch_limit} 只"
    )
    return f"""\
你正在执行一次有人监督的 Pokémon GO iPhone 改名任务：{limit_text}。

唯一允许的改动是修改宝可梦昵称。工作流程：
1. 每一步先用 describe_screen 重新观察，取得新鲜 observation token。
2. 确认前台是 Pokémon GO，且处于单只宝可梦详情或改名相关界面。
3. 读取当前繁中物种名和当前昵称。只有当前昵称严格等于该物种默认名时才允许继续；已有自定义名必须跳过并记录 skip_custom。
4. 从 Poke Genie 读取它已经生成的完整昵称。原样使用，不自行重算或补写。必须保留星标、A/D/S 圆圈值、IV 上标百分比和绝版技能 (+) 标记。
5. 看不到或无法确定完整建议昵称时跳过并记录 skip_uncertain。
6. 写入前把 current_name、species、default_name_verified=true 和新鲜 token 一并交给安全工具。
7. 提交后重新观察，确认昵称确实更新，再记录 renamed；失败则立即停止，不要盲目重试。
8. 安全地前往下一只，直到达到 {batch_limit} 只、没有更多候选或出现任何不确定情况。

硬性规则：
- 绝不点击或尝试传送、Transfer、批量选择、删除、交换、强化、进化、解锁招式或购买。
- 任何出现“传送”“TRANSFER”“确定要传送”等内容的屏幕都立即调用 pogo_abort。
- 不调用 Shell、文件、安装、卸载、系统设置或网络发布工具。
- 每个写动作只能使用最新且未使用过的 observation token，并说明 intent 与 expected_after。
- 不依靠记忆猜坐标；每次动作后都验证屏幕。
- 达到批次上限后必须停止并用中文汇总 renamed、skip_custom、skip_uncertain 数量。
"""
