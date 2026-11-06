"""deskpet.notice_rules —— 规则兜底（通知拆分 + 口语待办）

阶段 3 起是「降级链终点」：模型可用时 notice/parse 走 LlmGateway，
模型不可用 / 校验不过 / 超成本上限时回落到这里的规则版——
路由路径、请求体、响应 schema 都不动，只换 degraded/degrade_reason。

分类规则（验收标准写死，不画大饼）：
    支持 4 个类别：deliverable / format / deadline / other
    关键词表见 RULES。结果仍是契约里的 ChecklistItem 列表，字段类型不改。
"""

from __future__ import annotations

import re
from datetime import datetime

from deskpet.models import ChecklistItem, NoticeParseResult, VoiceParseResult


# 关键词 → 类别（顺序敏感：先匹配的优先）
RULES: list[tuple[str, str]] = [
    ("提交|交|上传|发送|发布|上交", "deliverable"),
    ("模板|格式|排版|字数|页数|大小|pdf|docx|pptx|markdown", "format"),
    ("deadline|截止|截至|之前|前交|日内|号前|点前", "deadline"),
    ("合照|照片|风采|队员|团队|成员", "format"),   # 作业素材归 format
]

# 截止时间提取（简单规则：MM/DD HH:MM 或 MM月DD日 等）
_DEADLINE_PATTERNS = [
    # 11/8 23:59 / 11/8 23:59:00
    (re.compile(r"(\d{1,2})/(\d{1,2})\s+(\d{1,2}):(\d{2})"), "md_hm"),
    # 11月8日 23:59
    (re.compile(r"(\d{1,2})月(\d{1,2})日\s+(\d{1,2}):(\d{2})"), "md_hm"),
    # 11/8 / 11月8日（无时间）
    (re.compile(r"(\d{1,2})/(\d{1,2})"), "md"),
    (re.compile(r"(\d{1,2})月(\d{1,2})日"), "md"),
]


def _classify_line(line: str) -> str:
    for pattern, category in RULES:
        if re.search(pattern, line):
            return category
    return "other"


def _extract_deadline_hint(text: str) -> datetime | None:
    """从通知里抽截止时间。简单规则，阶段 3 由模型替代。"""
    now = datetime.now()
    for rx, kind in _DEADLINE_PATTERNS:
        m = rx.search(text)
        if not m:
            continue
        month = int(m.group(1))
        day = int(m.group(2))
        hour = int(m.group(3)) if kind == "md_hm" else 23
        minute = int(m.group(4)) if kind == "md_hm" else 59
        try:
            return now.replace(month=month, day=day, hour=hour,
                               minute=minute, second=0, microsecond=0)
        except ValueError:
            continue
    return None


def parse_notice_by_rules(notice_text: str) -> NoticeParseResult:
    """规则版通知拆分。按标点/换行切句，逐句分类。"""
    # 按换行、句号、分号、顿号切
    raw_lines = re.split(r"[\n。；;，,、]+", notice_text)
    items: list[ChecklistItem] = []
    seen = set()
    for raw in raw_lines:
        line = raw.strip().strip("：:【】").strip()
        if not line or len(line) < 2:
            continue
        if line in seen:
            continue
        seen.add(line)
        category = _classify_line(line)
        items.append(ChecklistItem(
            text=line, category=category, matched=False))

    if not items:
        items.append(ChecklistItem(
            text=notice_text[:50], category="other", matched=False))

    deadline = _extract_deadline_hint(notice_text)
    summary = "、".join(i.text for i in items[:3])
    if len(items) > 3:
        summary += f" 等 {len(items)} 条"

    return NoticeParseResult(
        items=items, summary=summary, deadline_hint=deadline)


def parse_voice_by_rules(text: str) -> VoiceParseResult:
    """一句话变待办的规则兜底（from_voice 降级链终点，不许留 500）。

    本期不做语音识别：text 即转写文本或用户直接敲的一句话。
    标题=原句去多余空白（截 200 字）；截止时间复用日期关键词规则；
    置信度给低分 0.6，让前端提示用户确认（模型版的对照值）。
    """
    title = re.sub(r"\s+", " ", text).strip()[:200] or text[:200]
    deadline = _extract_deadline_hint(text)
    return VoiceParseResult(title=title, deadline=deadline, confidence=0.6)