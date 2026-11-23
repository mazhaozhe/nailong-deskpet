import io
import json
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.deskpet.backend import storage
from apps.deskpet.backend.app import create_app
from apps.deskpet.backend.llm import Outcome, gateway


@pytest.fixture
def document_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKPET_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "notice.db")
    with TestClient(create_app(), headers={"X-User-Id": str(uuid4())}) as client:
        client.post("/api/settings/model-switch", json={"state": "off"}).raise_for_status()
        yield client


def parse_document(client, name, data, mode="standard"):
    response = client.post(
        "/api/notice/document", params={"filename": name, "mode": mode}, content=data,
        headers={"Content-Type": "application/octet-stream"},
    )
    response.raise_for_status()
    return [json.loads(line) for line in response.iter_lines()]


def test_word_table_keeps_source_and_requires_confirmation(document_client):
    from docx import Document

    document = Document()
    document.add_heading("期末项目")
    document.add_paragraph("请提交项目报告。")
    table = document.add_table(rows=1, cols=1)
    table.cell(0, 0).text = "上传演示录屏"
    stream = io.BytesIO()
    document.save(stream)
    events = parse_document(document_client, "期末.docx", stream.getvalue())
    assert events[0]["type"] == "progress"
    result = events[-1]
    assert result["type"] == "result"
    assert result["mode"] == "rules"
    assert any("上传演示录屏" in item["text"] and "表格" in item["source"]
               for item in result["data"]["items"])
    assert document_client.get("/api/todos").json()["data"] == []


def test_agent_fallback_is_labeled_when_model_unavailable(document_client):
    document_client.post("/api/agent/mode", json={"enabled": True}).raise_for_status()
    result = parse_document(document_client, "通知.txt", "请上传附件。".encode(), mode="agent")[-1]
    assert result["type"] == "result"
    assert result["mode"] == "rules"
    assert result["degraded"] is True and result["degrade_reason"]
    assert result["data"]["items"]


@pytest.mark.parametrize("mode,expected", [("standard", "model"), ("agent", "agent")])
def test_document_mode_keeps_sources_after_model_processing(document_client, monkeypatch, mode, expected):
    from docx import Document

    document_client.post("/api/agent/mode", json={"enabled": mode == "agent"}).raise_for_status()
    document = Document()
    document.add_paragraph("请提交实验报告。")
    document.add_paragraph("请上传实验报告。")
    stream = io.BytesIO()
    document.save(stream)

    async def invoke(**kwargs):
        items = json.loads(kwargs["messages"][-1]["content"])["items"]
        ids = [item["id"] for item in items]
        data = kwargs["schema"](groups=[ids]) if mode == "agent" else kwargs["schema"](ids=ids)
        return Outcome(data, False, "")

    monkeypatch.setattr(gateway, "invoke", invoke)
    result = parse_document(document_client, "交付任务.docx", stream.getvalue(), mode)[-1]
    assert result["type"] == "result" and result["mode"] == expected
    items = result["data"]["items"]
    source_count = 1 if mode == "agent" else 2
    assert len(items) == source_count
    assert len({source for item in items for source in item["sources"]}) == 2


def test_document_deadlines_are_confirmed_per_item(document_client):
    response = document_client.post("/api/notice/confirm", json={"items": [
        {"text": "上传报告", "deadline_hint": "2026-12-03T18:00:00+08:00"},
        {"text": "上传演示", "deadline_hint": "2026-12-04T18:00:00+08:00"},
        {"text": "补齐姓名", "needs_review": True},
    ]})
    response.raise_for_status()
    assert [item["deadline"] for item in response.json()["created"]] == [
        "2026-12-03T18:00:00+08:00", "2026-12-04T18:00:00+08:00", None,
    ]


def test_invalid_document_type_is_rejected(document_client):
    response = document_client.post("/api/notice/document", params={"filename": "文件.exe"}, content=b"ignored")
    assert response.status_code == 415


def test_mixed_pdf_keeps_text_and_ocr_sources_in_both_modes(document_client, monkeypatch):
    from pypdf import PdfWriter

    from tests.backend.test_notice_document_extraction import add_text_page, image_pdf, windows_font

    writer = PdfWriter()
    for _ in range(30):
        writer.add_blank_page(width=500, height=500)
    mixed = add_text_page(writer, "Submit project brief")
    mixed.merge_page(image_pdf("请上传演示录屏", windows_font("msyh.ttc")))
    stream = io.BytesIO()
    writer.write(stream)

    events = parse_document(document_client, "混合通知.pdf", stream.getvalue())
    rules = events[-1]
    assert rules["type"] == "result" and rules["mode"] == "rules"

    async def review(**kwargs):
        assert kwargs["route"] == "notice/document/agent-review"
        items = json.loads(kwargs["messages"][-1]["content"])["items"]
        assert {item["source"] for item in items} == {"第 31 页", "第 31 页 · 图片"}
        return Outcome(kwargs["schema"](groups=[]), False, "")

    document_client.post("/api/agent/mode", json={"enabled": True}).raise_for_status()
    monkeypatch.setattr(gateway, "invoke", review)
    agent = parse_document(document_client, "混合通知.pdf", stream.getvalue(), mode="agent")[-1]
    assert agent["type"] == "result" and agent["mode"] == "agent"
    for result in (rules, agent):
        items = {item["text"]: item for item in result["data"]["items"]}
        assert items["Submit project brief"]["source"] == "第 31 页"
        assert items["请上传演示录屏"]["source"] == "第 31 页 · 图片"
        assert items["请上传演示录屏"]["needs_review"]
    assert document_client.get("/api/todos").json()["data"] == []
