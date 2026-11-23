"""Turn a notice's local text and OCR evidence into a reviewable checklist."""

from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from typing import BinaryIO, Iterator, Literal

from anyio import CancelScope
from starlette.concurrency import iterate_in_threadpool, run_in_threadpool

from apps.deskpet.backend import clock, notice_rules
from apps.deskpet.backend.models import ChecklistItem, NoticeParseResult
from apps.deskpet.backend.notice_document_agent import (
    DocumentCandidate,
    ReviewedCandidate,
    merge_document_locally,
    review_document,
    select_document,
)
from apps.deskpet.backend.notice_documents import DocumentPage, DocumentReadError, read_document
from apps.deskpet.backend.schemas import NoticeParseResponse

logger = logging.getLogger(__name__)

DocumentMode = Literal["standard", "agent"]
_DEADLINE_WORDS = re.compile(r"截止|截至|之前|以前|前交|日内|号前|点前|deadline|due\b", re.I)


def _append_candidates(items: list[DocumentCandidate], text: str, source: str, *, ocr: bool = False) -> None:
    if not text.strip():
        return
    for item in notice_rules.parse_notice_by_rules(text, extract_deadline=False).items:
        deadline = notice_rules.extract_deadline_hint(item.text) if _DEADLINE_WORDS.search(item.text) else None
        items.append(DocumentCandidate(
            id=str(len(items) + 1), text=item.text, category=item.category, source=source,
            evidence=item.text, deadline=deadline, ocr=ocr,
        ))


def _checklist(items: list[ReviewedCandidate]) -> NoticeParseResult:
    checklist = [ChecklistItem(
        text=item.candidate.text, category=item.candidate.category, source=item.candidate.source,
        sources=list(dict.fromkeys(entry.source for entry in item.sources)),
        needs_review=item.needs_review, deadline_hint=item.deadline,
    ) for item in items]
    summary = "、".join(item.text for item in checklist[:3])
    if len(checklist) > 3:
        summary += f" 等 {len(checklist)} 条"
    return NoticeParseResult(items=checklist, summary=summary)


async def _finish_notice(items: list[DocumentCandidate], mode: DocumentMode, agent_enabled: bool,
                         warnings: list[str]) -> NoticeParseResponse:
    if not items:
        return NoticeParseResponse(data=_checklist([]), mode="rules", warnings=[*warnings, "没有读到可拆解的文字。"])

    now_iso = clock.to_iso(clock.real_now())
    if mode == "agent" and agent_enabled:
        review = await review_document(items, now_iso=now_iso)
        if any(item.deadline_conflict for item in review.items):
            warnings.append("有重复事项的截止时间不一致，请对照原文核对。")
        return NoticeParseResponse(data=_checklist(review.items), mode="agent" if review.used_agent else "rules",
                                   degraded=bool(review.degrade_reason), degrade_reason=review.degrade_reason,
                                   warnings=[*warnings, *review.warnings])

    if mode == "agent":
        reason = "Agent 模式未开启，已使用本地规则"
        return NoticeParseResponse(data=_checklist(merge_document_locally(items)), mode="rules",
                                   degraded=True, degrade_reason=reason, warnings=warnings)

    selected, used_model, reason = await select_document(items, now_iso=now_iso)
    if any(item.deadline_conflict for item in selected):
        warnings.append("有重复事项的截止时间不一致，请对照原文核对。")
    return NoticeParseResponse(data=_checklist(selected), mode="model" if used_model else "rules",
                               degraded=bool(reason), degrade_reason=reason, warnings=warnings)


async def review_pasted_notice(text: str, *, agent_enabled: bool) -> NoticeParseResponse:
    items: list[DocumentCandidate] = []
    _append_candidates(items, text, "粘贴文本")
    return await _finish_notice(items, "agent", agent_enabled, [])


def _pages(file: BinaryIO, filename: str) -> Iterator[tuple[int, DocumentPage | None]]:
    with read_document(file, filename) as (total, entries):
        yield total, None
        for entry in entries:
            yield total, entry


def _line(event_type: str, **data) -> str:
    return json.dumps({"type": event_type, **data}, ensure_ascii=False) + "\n"


async def document_events(file: BinaryIO, filename: str, mode: DocumentMode, *, agent_enabled: bool):
    items: list[DocumentCandidate] = []
    warning_locations: dict[str, list[str]] = defaultdict(list)
    processed = total = 0
    pages = _pages(file, filename)
    try:
        async for total, page in iterate_in_threadpool(pages):
            if page is None:
                yield _line("progress", stage="extract", processed=0, total=total)
                continue
            _append_candidates(items, page.text, page.location)
            _append_candidates(items, page.ocr_text, f"{page.location} · 图片", ocr=True)
            for warning in set(page.warnings):
                warning_locations[warning].append(page.location)
            processed += 1
            yield _line("progress", stage="extract", processed=processed, total=total, location=page.location)

        warnings = []
        for warning, locations in warning_locations.items():
            affected = "、".join(locations[:3])
            if len(locations) > 3:
                affected += f"等 {len(locations)} 处"
            warnings.append(f"{affected}：{warning}")

        yield _line("progress", stage="review", processed=processed, total=total)
        result = await _finish_notice(items, mode, agent_enabled, warnings)
        yield _line("result", **result.model_dump(mode="json"))
    except DocumentReadError as exc:
        yield _line("error", message=str(exc))
    except Exception:
        logger.exception("Notice document processing failed")
        yield _line("error", message="文档处理失败，请确认文件完整后重试。")
    finally:
        with CancelScope(shield=True):
            await run_in_threadpool(pages.close)
            file.close()
