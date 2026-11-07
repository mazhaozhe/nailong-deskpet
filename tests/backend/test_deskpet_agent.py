"""tests/backend/test_deskpet_agent.py —— 会话 4 · 完整 Agent 第四态回归

三条线（与 docs/会话4_验收清单.md 一一对应）：
    1. 路由形态与降级语义：/api/agent/* 五条路由关态/无 Key 下不 500、
       如实降级；mode 是叠加开关，不碰三态（off/auto/on 原样）。
    2. 关态零加载：干净子进程里打完全部 agent 路由后，sys.modules 仍零
       langchain_core / apps.agent.tools*（硬闸门 off_state_no_imports 的
       Agent 版，子进程隔离防同进程 import 污染）。
    3. 桩链路审批正确性：复用 apps/deskpet/backend/scripts/agent_probe.py
       （拒绝不执行/批准真执行/approval_id 一次性/按下标对应/超时拒绝/
       run_shell 同闸/审计日志），子进程跑，ok=true 才算过。
"""

import json
import os
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _sub_env() -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT)
    env.pop("DEEPSEEK_API_KEY", None)
    env["DESKPET_DATA_DIR"] = tempfile.mkdtemp(prefix="deskpet-agent-test-")
    return env


# ---------------------------------------------------------------------------
# 1. 路由形态（本进程即可：无 Key 时 Agent 模块根本不会 import）
# ---------------------------------------------------------------------------

def test_agent_routes_degrade_without_key(monkeypatch, tmp_path):
    monkeypatch.setenv("DESKPET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from fastapi.testclient import TestClient
    from apps.deskpet.backend.app import app

    c = TestClient(app, headers={"X-User-Id": str(uuid.uuid4())})
    # mode：叠加开关，不替换三态
    r = c.post("/api/agent/mode", json={"enabled": True})
    assert r.status_code == 200 and r.json()["agent_mode"] is True
    r = c.get("/api/settings/model-switch")
    assert r.json()["state"] in ("off", "auto", "on")   # 三态语义原样存在
    # status：脱敏、不回 Key
    r = c.get("/api/agent/status")
    assert r.status_code == 200
    body = r.json()
    assert body["key_configured"] is False and body["key_masked_tail"] is None
    assert "DEEPSEEK_API_KEY" not in json.dumps(body)
    # chat：无 Key 如实降级，不 500
    r = c.post("/api/agent/chat", json={"message": "你好", "session_id": "s1"})
    assert r.status_code == 200 and r.json()["status"] == "degraded"
    assert "Key" in r.json()["degrade_reason"]
    # approve：未知 approval_id 明确报错
    r = c.post("/api/agent/approve", json={
        "approval_id": "deadbeefdeadbeef", "decisions": [{"type": "approve"}]})
    assert r.status_code == 200 and "不存在或已被消费" in r.json()["degrade_reason"]
    # reset：清会话
    r = c.post("/api/agent/session/reset", json={"session_id": "s1"})
    assert r.status_code == 200 and r.json()["reset"] is True
    # 收尾关掉，不留开关
    r = c.post("/api/agent/mode", json={"enabled": False})
    assert r.json()["agent_mode"] is False


def test_agent_mode_request_validation():
    """enabled 缺失/非布尔 → 422（契约层拒绝，不静默）。"""
    from fastapi.testclient import TestClient
    from apps.deskpet.backend.app import app
    c = TestClient(app, headers={"X-User-Id": str(uuid.uuid4())})
    assert c.post("/api/agent/mode", json={}).status_code == 422
    assert c.post("/api/agent/mode", json={"enabled": "maybe"}).status_code == 422
    assert c.post("/api/agent/mode", json={"enabled": None}).status_code == 422
    long_id = "x" * 65
    r = c.post("/api/agent/chat", json={"message": "hi", "session_id": long_id})
    assert r.status_code == 422   # session_id 超 64 字符拒绝


# ---------------------------------------------------------------------------
# 2. 关态零加载（子进程隔离）
# ---------------------------------------------------------------------------

_SUB_OFF_STATE = r"""
import json, os, sys, uuid
sys.path.insert(0, os.environ["PYTHONPATH"])
from fastapi.testclient import TestClient
from apps.deskpet.backend.app import app
c = TestClient(app, headers={"X-User-Id": str(uuid.uuid4())})
c.post("/api/agent/mode", json={"enabled": True})
c.get("/api/agent/status")
c.post("/api/agent/chat", json={"message": "在吗", "session_id": "s0"})
c.post("/api/agent/approve", json={"approval_id": "deadbeefdeadbeef",
                                   "decisions": [{"type": "approve"}]})
c.post("/api/agent/session/reset", json={"session_id": "s0"})
c.post("/api/agent/mode", json={"enabled": False})
banned = [m for m in sys.modules
          if m == "langchain_core" or m.startswith("apps.agent.tools")
          or m in ("infrastructure.cache", "infrastructure.llm.models",
                   "langchain_community")]
print(json.dumps({"banned": banned}))
"""


def test_off_state_zero_agent_imports():
    r = subprocess.run([sys.executable, "-c", _SUB_OFF_STATE],
                       capture_output=True, text=True, env=_sub_env(),
                       cwd=str(ROOT), timeout=120)
    assert r.returncode == 0, f"子进程失败:\n{r.stdout}\n{r.stderr}"
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert not out["banned"], (
        f"无 Key/未显式使用时 Agent 模块被拉进进程：{out['banned']}——"
        f"违反第四态叠加开关的懒加载铁闸")


# ---------------------------------------------------------------------------
# 3. 桩链路审批正确性（复用仓库探针 agent_probe.py，子进程隔离）
# ---------------------------------------------------------------------------

def test_agent_stub_probe_all_green():
    probe = ROOT / "apps" / "deskpet" / "backend" / "scripts" / "agent_probe.py"
    r = subprocess.run([sys.executable, str(probe)],
                       capture_output=True, text=True, cwd=str(ROOT),
                       timeout=300)
    assert r.returncode == 0, f"探针失败:\n{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
    out = json.loads(r.stdout)
    assert out["ok"] is True, f"探针有失败项: {out['failures']}"
    assert out["checks"]["reject_not_executed"], "拒绝后工具不得执行"
    assert out["checks"]["approve_executed"], "批准后工具必须真执行"
    assert out["checks"]["decisions_by_index"], "batched 决定必须按下标对应"
    assert out["checks"]["timeout_default_reject"], "超时必须默认拒绝"
