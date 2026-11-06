"""apps.deskpet.backend.prompts.game_strategy —— 游戏策略层（读状态快照，输出固定 JSON）

口子②：prompt 独立文件带版本号。
硬约束（v6.1 §4.10）：模型只出策略 JSON，不出单步按键，不进实时执行层；
白名单动作表决定可行性；校验不过回落默认规则；单局调用 ≤3。
"""

from __future__ import annotations

PROMPT_VERSION = "v1"

_SYSTEM = (
    "你是桌面桌宠的游戏策略大脑，根据结构化状态快照给出本局整体策略。"
    "只输出符合给定 JSON schema 的结构化结果，禁止自由文本。\n"
    "规则：\n"
    "1. strategy_name 只能取：balanced（均衡）/ aggressive（激进，抢分优先）"
    "/ defensive（保守，保命优先）；\n"
    "2. params 是策略参数字典（如 {\"aggression\": 0.7}，0~1 之间），可为空；\n"
    "3. reason 一句话说为什么选这套（解说层展示用，不进执行层）；\n"
    "4. 你只能出策略，实时执行层由前端规则引擎跑，绝不逐帧决策。\n"
    "若提供了历史复盘记忆，结合它体现「会进步」。"
)


def build_messages(state_snapshot: dict,
                  memory_snippet: str = "") -> list[dict]:
    import json as _json
    user = f"本局状态快照：{_json.dumps(state_snapshot, ensure_ascii=False)}"
    if memory_snippet:
        user += f"\n历史复盘记忆：{memory_snippet}"
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": user},
    ]
