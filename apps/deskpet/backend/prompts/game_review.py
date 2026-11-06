"""apps.deskpet.backend.prompts.game_review —— 游戏复盘层（每局结束一次）

口子②：prompt 独立文件带版本号。
复盘结论写入 GameMemory（输在哪/下次怎么改/用了哪套策略），
下一局策略层读它做「会进步」；不做全量回放、不做用户画像。
降级原则：模型不可用时走静态兜底文案。
"""

from __future__ import annotations

PROMPT_VERSION = "v1"

_SYSTEM = (
    "你是桌面桌宠的游戏复盘大脑，对刚结束的一局做简短复盘。"
    "只输出符合给定 JSON schema 的结构化结果，禁止自由文本。\n"
    "规则：\n"
    "1. lost_at：输在哪（一句话，赢/平局时写制胜点或写空串）；\n"
    "2. next_change：下次怎么改（一句可执行的具体改法）；\n"
    "3. takeaway：一句话复盘结论，口语化，给玩家听。\n"
    "结合本局用过的策略序列与最终状态分析，不编造快照里没有的事实。"
)


def build_messages(final_state: dict, strategy_history: list[str]) -> list[dict]:
    import json as _json
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",
         "content": (f"最终状态：{_json.dumps(final_state, ensure_ascii=False)}\n"
                     f"本局用过的策略序列：{strategy_history}")},
    ]
