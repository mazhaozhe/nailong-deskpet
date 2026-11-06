"""apps.deskpet.backend.prompts.notice_to_checklist —— 贴通知 → 抽要求清单

口子②：prompt 独立文件带版本号，禁止在路由/llm.py 里硬编码字符串。
将来检索结果作为 context 注入 user 消息即可，不改 schema 形状。
降级原则：模型不可用时本文件不被调用，走 apps/deskpet/backend/notice_rules.py 规则兜底。
"""

from __future__ import annotations

PROMPT_VERSION = "v1"

_SYSTEM = (
    "你是桌面桌宠内置的通知解析器，只负责把用户贴进来的课程/作业通知"
    "拆解成结构化的交付清单。只输出符合给定 JSON schema 的结构化结果，"
    "禁止自由文本、禁止编造通知里没有的要求。\n"
    "拆解规则：\n"
    "1. items：每条是通知里的一条独立要求/交付项的原文摘述，不合并、不改写意思；\n"
    "2. category 只能取四值：deliverable（要交什么）、format（格式/模板/素材要求，"
    "含合照、风采等）、deadline（截止时间类要求）、other（其它）；\n"
    "3. deadline_hint：仅当通知明确提到截止时间时，换算成绝对时间"
    "（结合提供的当前时间补全年份与时区），没有则为 null，绝不猜测；\n"
    "4. summary：一句话概括这条通知要交什么。"
)


def build_messages(notice_text: str, now_iso: str) -> list[dict]:
    """构造完整消息。now_iso 由路由传入（当前时间，供相对日期换算）。"""
    return [
        {"role": "system", "content": _SYSTEM},
        {"role": "user",
         "content": f"当前时间：{now_iso}\n通知全文：\n{notice_text}"},
    ]
