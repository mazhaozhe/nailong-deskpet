from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from apps.deskpet.backend import agent_runtime, clock, storage
from apps.deskpet.backend.app import create_app


@pytest.fixture
def growth_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DESKPET_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "deskpet.db")
    current = [datetime(2026, 11, 23, 15, 59, tzinfo=timezone.utc)]
    monkeypatch.setattr(clock, "real_now", lambda: current[0])
    with TestClient(create_app(), headers={"X-User-Id": str(uuid4())}) as client:
        yield client, current


def test_always_uses_install_date_and_real_reminder_clock(growth_client, monkeypatch):
    client, current = growth_client
    monkeypatch.setenv("DESKPET_LOCAL_UID", client.headers["X-User-Id"])
    monkeypatch.setenv("DESKPET_INSTALLED_AT", (current[0] - timedelta(days=3)).isoformat())
    growth = client.get("/api/growth").json()["data"]
    assert growth["days_known"] == 4
    assert growth["intimacy"] == 75
    client.post("/api/todos", json={
        "title": "提交报告", "deadline": (current[0] + timedelta(days=1)).isoformat(),
    }).raise_for_status()

    current[0] += timedelta(minutes=2)
    growth = client.post("/api/growth/sync", json={}).json()["data"]
    assert growth["days_known"] == 5
    assert client.post("/api/growth/simulate", json={"target_day": 100}).status_code == 404
    assert client.post("/api/growth/simulate/clear").status_code == 404
    catchup = client.get("/api/catchup?view=preview").json()["data"]
    assert catchup["days_since_first"] == 5
    assert catchup["overdue"] == []
    assert client.get("/api/growth").json()["data"]["days_known"] == 5


def test_rule_reply_award_is_idempotent_and_empty_reply_is_excluded(growth_client):
    client, current = growth_client
    request_id = str(uuid4())
    first = client.post("/api/chat", json={"message": "你好", "request_id": request_id}).json()
    assert first["source"] == "rule"
    assert first["growth"]["intimacy"] == 76
    assert client.post("/api/chat", json={"message": " 你好 "}).json()["growth"]["intimacy"] == 76
    assert client.post("/api/chat", json={"message": "  "}).json()["growth"] is None
    current[0] += timedelta(days=1)
    retry = client.post("/api/chat", json={"message": "你好", "request_id": request_id}).json()
    assert retry["growth"]["intimacy"] == 76
    assert client.post("/api/chat", json={"message": "谢谢", "request_id": request_id}).status_code == 409


def test_panel_event_claim_and_task_award_use_authoritative_snapshot(growth_client):
    client, _ = growth_client
    initial = client.post("/api/growth/sync", json={"panel_open": True}).json()["data"]
    [event] = initial["action_events"]
    assert event["kind"] == "welcome"
    claim = f"/api/growth/actions/{event['id']}/claim"
    assert client.post(claim).json() == {"claimed": True}
    assert client.post(claim).json() == {"claimed": False}
    reopen = client.post("/api/growth/sync", json={"panel_open": True}).json()["data"]
    assert reopen["action_events"] == []

    todo = client.post("/api/todos", json={"title": "测试一项待办"}).json()["data"]
    done = client.patch(f"/api/todos/{todo['id']}", json={"status": "done"}).json()
    assert done["data"]["status"] == "done"
    assert done["growth"]["intimacy"] == 76.5


def test_agent_approval_chain_awards_only_the_final_reply(growth_client, monkeypatch):
    client, _ = growth_client
    from langchain_core.messages import AIMessage

    runtime = agent_runtime.AgentRuntime()
    interrupt = SimpleNamespace(value={"action_requests": [{"name": "open_url", "args": {}}]})

    class ModelDouble:
        async def ainvoke(self, payload, config):
            return {"__interrupt__": [interrupt]}

    resumes = iter([{"__interrupt__": [interrupt]}, {"messages": [AIMessage(content="做完了。")]}])

    async def resume(thread_id, decisions):
        return next(resumes)

    monkeypatch.setenv(agent_runtime.KEY_ENV, "test-key")
    monkeypatch.setattr(agent_runtime, "get_runtime", lambda: runtime)
    monkeypatch.setattr(runtime, "get_mode", lambda: True)
    monkeypatch.setattr(runtime, "_agent", ModelDouble())
    monkeypatch.setattr(runtime, "_resume", resume)
    pending = client.post("/api/agent/chat", json={
        "message": "帮我打开页面", "session_id": "growth", "request_id": str(uuid4()),
    }).json()
    for _ in range(2):
        assert pending["status"] == "pending_approval"
        assert client.get("/api/growth").json()["data"]["intimacy"] == 75
        pending = client.post("/api/agent/approve", json={
            "approval_id": pending["approval_id"], "decisions": [{"type": "approve"}],
        }).json()
    assert pending["status"] == "ok"
    assert pending["growth"]["intimacy"] == 76
