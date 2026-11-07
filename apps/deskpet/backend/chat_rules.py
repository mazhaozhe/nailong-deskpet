"""apps.deskpet.backend.chat_rules —— 闲聊的规则回复（零模型调用）

三处用到，全都不烧模型：
    1. 空消息：用户点了发送但没打字 —— 回一句提示，不是报错、更不是 500；
    2. 纯打招呼 / 纯道谢（「你好」「在吗」「谢谢」）—— 规则回一句，
       顺手带一句真实待办，属于「先垫一句」而不是「假装懂你」；
    3. 模型不可用（关态 / 无 Key / 401 / 断网 / 校验不过）—— 如实说清
       降级原因，再把已知的真实事实说一句，页面不空白（degraded=true 由路由标注）。

红线：这里只拼字符串，不读模型、不联网、不写库；引用的事实全部来自
chat_memory.MemoryFacts（本机真实数据，0 条就如实说 0 条）。
"""

from __future__ import annotations

import re

from apps.deskpet.backend.chat_memory import MemoryFacts, memory_summary
from apps.deskpet.backend.models import ChatReply

# 纯标点 / 空白 / 表情符（不含中日韩文字与字母数字）
_NOISE_RE = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)

# 打招呼、道谢、告别（归一化后精确匹配，或作为 ≤6 字短句的后缀出现）
GREETING_WORDS = (
    "你好", "您好", "嗨", "哈喽", "哈啰", "在吗", "在么", "在不在",
    "hi", "hello", "hey", "yo",
    "早", "早安", "早上好", "中午好", "下午好", "晚上好", "晚安",
    "谢谢", "多谢", "感谢", "辛苦了", "好的", "好", "嗯", "收到",
    "拜拜", "再见", "886", "88",
)

_MAX_SIMPLE_LEN = 6      # 超过这个长度就不当「纯打招呼」，交给模型
_MAX_REASON_CHARS = 60   # 降级原因在气泡里只留一小段，别撑爆布局


def normalize(text: str) -> str:
    """去空白/标点/表情，小写化。用于判断「是不是纯打招呼」。"""
    return _NOISE_RE.sub("", str(text or "")).strip().lower()


def is_empty(text: str) -> bool:
    return normalize(text) == ""


def is_simple(text: str) -> bool:
    """纯打招呼 / 纯道谢 / 纯道别：走规则回复，零模型调用。

    故意只认「短 + 命中词表」：像「你好，我今天要交的博客怎么排」这种
    带正事的句子（长度超限）会照常进模型，不会被规则抢走。
    """
    norm = normalize(text)
    if not norm:
        return True
    if len(norm) > _MAX_SIMPLE_LEN:
        return False
    for word in GREETING_WORDS:
        if norm == word or norm.endswith(word) or norm.startswith(word):
            return True
    return False


def _pick(templates: list[str], text: str) -> str:
    """按输入内容确定性取一条（同一句话同一回复，方便验收复现）。"""
    index = sum(ord(ch) for ch in (text or "x")) % len(templates)
    return templates[index]


def empty_reply() -> ChatReply:
    """空消息：不报错、不调模型，提示一句就好。"""
    return ChatReply(
        reply="嗯？你还没说话呢。想聊什么、或者手上那件事卡在哪，都可以说。",
        mood="calm", thought="", memory_hint="")


def simple_reply(text: str, facts: MemoryFacts) -> ChatReply:
    """纯打招呼：回一句 + 一句真实待办（0 条就如实说没有）。"""
    if facts.pending_count:
        templates = [
            "在呢。{hint}，要不要从它开始？",
            "来啦。{hint}，今天先动哪一件？",
            "嗨，我在这儿。{hint}，慢慢来。",
        ]
        body = _pick(templates, text).format(hint=memory_summary(facts))
        return ChatReply(reply=body, mood="cheer",
                         thought="打个招呼，顺带把真实待办说一句，不展开。",
                         memory_hint="未完成待办")
    templates = [
        "在呢。你的清单还是空的，想加点什么就说。",
        "来啦。今天没有待办，先歇会儿也行。",
        "嗨，我在这儿。清单空着，正好聊聊。",
    ]
    return ChatReply(reply=_pick(templates, text), mood="cheer",
                     thought="打个招呼；清单确实是空的，不编一件出来。",
                     memory_hint="空清单")


def fallback_reply(reason: str, facts: MemoryFacts) -> ChatReply:
    """模型不可用时的规则兜底：说清原因 + 说一句真实事实，绝不假装成功。"""
    short = (str(reason or "").strip() or "模型暂时不可用")[:_MAX_REASON_CHARS]
    if facts.pending_count:
        reply = (f"我这会儿接不上模型 —— {short}。"
                 f"不过我记着{memory_summary(facts)}，要不要先说说它？")
    else:
        reply = (f"我这会儿接不上模型 —— {short}。"
                 "清单还是空的，你说点什么，我都记着。")
    return ChatReply(reply=reply, mood="concerned",
                     thought="模型不可用，按规则回，原因如实说。",
                     memory_hint="未完成待办" if facts.pending_count else "空清单")
