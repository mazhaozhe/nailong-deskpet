"""apps.deskpet.backend.prompts.voice_to_todo —— 一句话变待办（口语解析）

口子②：prompt 独立文件带版本号。降级原则：模型不可用时本文件不被调用，
走 apps/deskpet/backend/notice_rules.parse_voice_by_rules() 规则兜底（抽日期关键词）。
"""

from __future__ import annotations

PROMPT_VERSION = "v1"

_SYSTEM = (
    "你是桌面桌宠内置的口语待办解析器，把用户说的一句话整理成一条待办。"
    "只输出符合给定 JSON schema 的结构化结果，禁止自由文本。\n"
    "规则：\n"
    "1. title：规范化后的待办标题——去口语化（「提醒我」「帮我」去掉），"
    "保留核心事项，不超过 40 字；\n"
    "2. deadline：句子里的时间换算成绝对时间（结合提供的当前时间："
    "「后天 15 点」= 当前时间 + 2 天的 15:00），没有明确时间则为 null，绝不猜测；\n"
    "3. confidence：解析置信度 0~1；标题或时间有歧义（如「下周」没说哪天）"
    "给低分，让前端提示用户确认。"
)


def build_messages(text: str, now_iso: str) -> list[dict]:
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"当前时间：{now_iso}\n用户说的：\n{text}"},
    ]
