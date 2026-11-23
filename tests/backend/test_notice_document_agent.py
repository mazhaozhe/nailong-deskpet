import asyncio
import json
from datetime import datetime, timezone

from apps.deskpet.backend.llm import Outcome


def test_local_review_keeps_sources_and_flags_conflicting_deadlines():
    from apps.deskpet.backend.notice_document_agent import DocumentCandidate, merge_document_locally

    items = [
        DocumentCandidate("one", "提交实验报告。", "deliverable", "第 1 页",
                          "截止 12/1 23:59", datetime(2026, 12, 1, 23, 59)),
        DocumentCandidate("two", "提交实验报告", "deliverable", "第 31 页 · 图片",
                          "截止 12/2 23:59", datetime(2026, 12, 2, 23, 59), True),
    ]

    [result] = merge_document_locally(items)

    assert result.candidate.text == "提交实验报告。"
    assert [source.source for source in result.sources] == ["第 1 页", "第 31 页 · 图片"]
    assert result.needs_review
    assert result.deadline_conflict
    assert result.deadline is None


def test_local_review_compares_deadlines_as_instants():
    from apps.deskpet.backend.notice_document_agent import DocumentCandidate, merge_document_locally

    items = [
        DocumentCandidate("one", "提交实验报告", "deliverable", "第 1 页",
                          "截止北京时间 12/1 23:59", datetime(2026, 12, 1, 23, 59)),
        DocumentCandidate("two", "提交实验报告", "deliverable", "第 31 页",
                          "23:59", datetime(2026, 12, 1, 15, 59, tzinfo=timezone.utc)),
    ]

    [result] = merge_document_locally(items)

    assert not result.deadline_conflict
    assert result.deadline.isoformat() == "2026-12-01T23:59:00+08:00"


def test_agent_merges_paraphrases_using_only_existing_evidence(monkeypatch):
    from apps.deskpet.backend import notice_document_agent as agent

    items = [
        agent.DocumentCandidate("one", "提交实验报告", "deliverable", "第 1 页", "提交实验报告"),
        agent.DocumentCandidate("two", "请上传实验报告", "deliverable", "第 31 页 · 图片",
                                "请上传实验报告", datetime(2026, 12, 2, 23, 59)),
        agent.DocumentCandidate("three", "拍摄实验演示视频", "deliverable", "第 32 页", "拍摄实验演示视频"),
    ]

    async def invoke(**kwargs):
        assert kwargs["capability"] == "parse_notice"
        assert kwargs["route"] == "notice/document/agent-review"
        content = json.loads(kwargs["messages"][-1]["content"])
        assert [entry["id"] for entry in content["items"]] == ["one", "two", "three"]
        return Outcome(kwargs["schema"](groups=[["one", "two"]]), False, "")

    monkeypatch.setattr(agent.gateway, "invoke", invoke)
    result = asyncio.run(agent.review_document(items, now_iso="2026-11-23T12:00:00+08:00"))

    assert result.used_agent
    assert [item.candidate.id for item in result.items] == ["one", "three"]
    assert [source.id for source in result.items[0].sources] == ["one", "two"]
    assert result.items[0].deadline.isoformat() == "2026-12-02T23:59:00+08:00"


def test_agent_rejects_unknown_ids_and_keeps_local_results(monkeypatch):
    from apps.deskpet.backend import notice_document_agent as agent

    items = [
        agent.DocumentCandidate("one", "提交实验报告", "deliverable", "第 1 页", "提交实验报告"),
        agent.DocumentCandidate("two", "拍摄实验演示视频", "deliverable", "第 31 页", "拍摄实验演示视频"),
    ]

    async def invoke(**kwargs):
        return Outcome(kwargs["schema"](groups=[["one", "two", "invented"]]), False, "")

    monkeypatch.setattr(agent.gateway, "invoke", invoke)
    result = asyncio.run(agent.review_document(items, now_iso="2026-11-23T12:00:00+08:00"))

    assert not result.used_agent
    assert result.degrade_reason
    assert [item.candidate.id for item in result.items] == ["one", "two"]


def test_agent_reports_model_downgrade_without_discarding_checklist(monkeypatch):
    from apps.deskpet.backend import notice_document_agent as agent

    items = [agent.DocumentCandidate("one", "提交实验报告", "deliverable", "第 31 页 · 图片", "提交实验报告")]

    async def invoke(**_kwargs):
        return Outcome(None, True, "未配置 Key")

    monkeypatch.setattr(agent.gateway, "invoke", invoke)
    result = asyncio.run(agent.review_document(items, now_iso="2026-11-23T12:00:00+08:00"))

    assert not result.used_agent
    assert result.degrade_reason == "未配置 Key"
    assert result.items[0].candidate.source == "第 31 页 · 图片"


def test_agent_shows_when_a_fallback_model_successfully_reviews(monkeypatch):
    from apps.deskpet.backend import notice_document_agent as agent

    item = agent.DocumentCandidate("one", "提交实验报告", "deliverable", "第 31 页", "提交实验报告")

    async def invoke(**kwargs):
        return Outcome(kwargs["schema"](groups=[]), True, "已切换到本地 Ollama")

    monkeypatch.setattr(agent.gateway, "invoke", invoke)
    result = asyncio.run(agent.review_document([item], now_iso="2026-11-23T12:00:00+08:00"))

    assert result.used_agent
    assert result.degrade_reason == "已切换到本地 Ollama"
    assert result.items[0].candidate == item
