"""tests/backend/test_deskpet_llm.py —— 阶段 3 模型层测试（自动+假 Key 档，不接真实 API）

附录 B 口径：「自动 + 有 Key」一律用假 Key 经注入环境变量
DEEPSEEK_API_KEY（= apps.deskpet.backend.llm.KEY_ENV，来自 config/models.json 的
api_key_env）注入来验，不接真实 API、不发真实请求——ModelManager
被替换成 FakeManager，FakeModel 只回固定 schema 结果 / 定向异常。

关态零 import（sys.modules 闸门）由 test_deskpet_no_model.py 的
子进程档负责（隔离，不受本文件的模型层 import 污染）。

运行：python -m pytest tests/backend/test_deskpet_llm.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FAKE_KEY = "sk-fake-abcd1234"


# ---------------------------------------------------------------------------
# Fakes：假模型层（绝不发真实请求）
# ---------------------------------------------------------------------------
class _FakeAIMessage:
    def __init__(self, usage=None):
        self.usage_metadata = usage


class _FakeStructured:
    def __init__(self, model, schema):
        self.model, self.schema = model, schema

    async def ainvoke(self, messages):
        return self.model._structured_once(self.schema)


class FakeModel:
    """behavior 字典（按需组合）：
        raise:   抛出的异常（通信类错误）
        invalid: True → parsed=None + parsing_error（校验失败）
        usage:   usage_metadata dict 或 None（用量未知档）
        parsed_for: {schema 类名: schema 实例} —— 同路由不同 schema 分发
    """

    def __init__(self, behavior):
        self.behavior = behavior
        self.calls = 0
        self.last_kw = None      # 记录 with_structured_output 的构造参数

    def with_structured_output(self, schema, include_raw=False, **kw):
        assert include_raw is True, "gateway 必须用 include_raw 取 usage_metadata"
        self.last_kw = kw
        return _FakeStructured(self, schema)

    async def ainvoke(self, messages):   # key/check 连通性检查走这条
        self.calls += 1
        b = self.behavior
        if b.get("raise") is not None:
            raise b["raise"]
        return _FakeAIMessage(b.get("usage"))

    def _structured_once(self, schema):
        self.calls += 1
        b = self.behavior
        if b.get("raise") is not None:
            raise b["raise"]
        if b.get("invalid"):
            return {"raw": _FakeAIMessage(b.get("usage")), "parsed": None,
                    "parsing_error": ValueError("非法 JSON/字段缺失")}
        if b.get("invalid_recoverable") is not None:
            # 真端点实测形态：tool call 参数串尾部多一个 `}`，LangChain 判 invalid
            return {"raw": b["invalid_recoverable"], "parsed": None,
                    "parsing_error": ValueError("Extra data: line 1 column 464")}
        parsed = b.get("parsed_for", {}).get(schema.__name__)
        assert parsed is not None, f"FakeModel 未给 {schema.__name__} 备结果"
        return {"raw": _FakeAIMessage(b.get("usage")),
                "parsed": parsed, "parsing_error": None}


class FakeManager:
    def __init__(self, specs):
        self.specs = specs          # {model_id: FakeModel}
        self.current_id = None
        self.switched_to = []

    def init_default(self, mid):
        self.current_id = mid

    def switch(self, mid):
        self.switched_to.append(mid)
        self.current_id = mid

    @property
    def current_model(self):
        return self.specs[self.current_id]


def _patch_model_layer(monkeypatch, specs):
    """把 infrastructure.llm.models.ModelManager 换成返回 FakeManager 的工厂。"""
    mgr = FakeManager(specs)
    import infrastructure.llm.models as am
    monkeypatch.setattr(am, "ModelManager", lambda catalog_path=None: mgr)
    return mgr


# ---------------------------------------------------------------------------
# 公共 fixture / helper
# ---------------------------------------------------------------------------
@pytest.fixture()
def env(tmp_path, monkeypatch):
    """独立数据目录 + 清 KEY + gateway/计数器复位（每个测试干净开局）。"""
    monkeypatch.setenv("DESKPET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from apps.deskpet.backend import llm, model_counter
    llm.gateway.reset()
    model_counter.reset()
    return tmp_path


def _client():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from apps.deskpet.backend.api import router, download_router
    from apps.deskpet.backend.auth import auth_middleware
    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(router)
    app.include_router(download_router)
    return TestClient(
        app, raise_server_exceptions=False,
        headers={"X-User-Id": "00000000-0000-4000-8000-0000000000bb"})


def _inject_key(monkeypatch, key=FAKE_KEY):
    monkeypatch.setenv("DEEPSEEK_API_KEY", key)


def _write_settings(data_dir, **kv):
    from apps.deskpet.backend import paths
    p = paths.get_settings_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(kv, ensure_ascii=False), encoding="utf-8")


def _cost_entries():
    from apps.deskpet.backend import cost
    return cost.recent_entries(limit=100)


def _primary_specs(primary_behavior, fallback_behavior=None):
    """主力 + ollama 兜底两档的标准 specs（目录 id 与 config/models.json 一致）。"""
    from apps.deskpet.backend import llm
    return {
        llm.PRIMARY_ID: FakeModel(primary_behavior),
        llm.MANUAL_ID: FakeModel(primary_behavior),
        llm.LOCAL_FALLBACK_ID: FakeModel(fallback_behavior or {}),
    }


# ---------------------------------------------------------------------------
# 1. 三态开关 + 自动态探测
# ---------------------------------------------------------------------------
def test_switch_default_auto_and_roundtrip(env):
    c = _client()
    r = c.get("/api/settings/model-switch")
    assert r.status_code == 200 and r.json()["state"] == "auto"
    r = c.post("/api/settings/model-switch", json={"state": "off"})
    assert r.status_code == 200 and r.json()["state"] == "off"
    r = c.get("/api/settings/model-switch")
    assert r.json()["state"] == "off"
    r = c.post("/api/settings/model-switch", json={"state": "half"})
    assert r.status_code == 422  # Literal 校验拦截
    r = c.post("/api/settings/model-switch", json={"state": "on"})
    assert r.status_code == 200 and r.json()["state"] == "on"


def test_auto_without_key_silently_degrades(env):
    """自动 + 无 Key：静默规则版，零调用。"""
    c = _client()
    r = c.post("/api/notice/parse", json={
        "notice_text": "11/8 23:59 前提交博客，含合照", "use_v4_pro": False})
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True
    assert "自动档未探测到 Key" in body["degrade_reason"]
    from apps.deskpet.backend import model_counter
    assert model_counter.total() == 0


def test_on_without_key_reports_clear_reason(env):
    """开 + 无 Key：明确原因，不假装成功，也不偷偷调模型。"""
    _write_settings(env, model_switch="on")
    c = _client()
    r = c.post("/api/notice/parse", json={
        "notice_text": "11/8 23:59 前提交博客", "use_v4_pro": False})
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True
    assert "开关=开但未配置 Key" in body["degrade_reason"]
    from apps.deskpet.backend import model_counter
    assert model_counter.total() == 0


# ---------------------------------------------------------------------------
# 2. 自动 + 假 Key：固定 JSON schema 输出 + 计数 + 缓存懒加载
# ---------------------------------------------------------------------------
def _notice_parsed():
    from apps.deskpet.backend.models import ChecklistItem, NoticeParseResult
    return NoticeParseResult(
        items=[ChecklistItem(text="提交博客", category="deliverable"),
               ChecklistItem(text="含合照", category="format")],
        summary="11/8 前交博客", deadline_hint=None)


def test_auto_with_fake_key_uses_model(env, monkeypatch):
    """自动 + 假 Key（附录 B）：走模型，degraded=False，计数 1，
    缓存以注入路径懒加载启用（enable_llm_cache 传 db_path）。"""
    from apps.deskpet.backend import llm, model_counter, paths
    _inject_key(monkeypatch)
    _patch_model_layer(monkeypatch, _primary_specs({
        "parsed_for": {"NoticeParseResult": _notice_parsed()},
        "usage": {"input_tokens": 1000, "output_tokens": 1000}}))
    c = _client()
    r = c.post("/api/notice/parse", json={
        "notice_text": "11/8 23:59 前提交博客，含合照", "use_v4_pro": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["degraded"] is False
    assert body["degrade_reason"] == ""
    assert body["data"]["items"][0]["text"] == "提交博客"
    assert model_counter.total() == 1
    assert llm.gateway._cache_enabled is True
    assert paths.get_cache_db_path().parent.exists()   # 路径注入生效
    # 成本如实入账：flash pricing 1.0/4.0 元每 Mtok → 1000+1000 tok = 0.005 元
    entries = _cost_entries()
    assert len(entries) == 1
    assert entries[0]["input_tokens"] == 1000
    assert entries[0]["cost_yuan"] == pytest.approx(0.005)


def test_use_v4_pro_switches_to_manual_model(env, monkeypatch):
    """use_v4_pro=True → manager 切到 deepseek-reasoner（v4-pro 手动档）。"""
    from apps.deskpet.backend import llm
    _inject_key(monkeypatch)
    mgr = _patch_model_layer(monkeypatch, _primary_specs({
        "parsed_for": {"NoticeParseResult": _notice_parsed()},
        "usage": {"input_tokens": 10, "output_tokens": 10}}))
    c = _client()
    r = c.post("/api/notice/parse", json={
        "notice_text": "长通知复杂拆解", "use_v4_pro": True})
    assert r.status_code == 200 and r.json()["degraded"] is False
    assert llm.MANUAL_ID in mgr.switched_to


def test_usage_unknown_recorded_not_fabricated(env, monkeypatch):
    """usage_metadata 拿不到 → 如实标「用量未知」，绝不编数字凑成本。"""
    from apps.deskpet.backend import cost
    _inject_key(monkeypatch)
    _patch_model_layer(monkeypatch, _primary_specs(
        {"parsed_for": {"NoticeParseResult": _notice_parsed()},
         "usage": None}))
    c = _client()
    r = c.post("/api/notice/parse", json={
        "notice_text": "11/8 前提交博客", "use_v4_pro": False})
    assert r.json()["degraded"] is False
    entries = _cost_entries()
    assert len(entries) == 1
    assert entries[0]["cost_yuan"] is None
    assert "用量未知" in entries[0]["note"]
    assert cost.today_cost_yuan() == 0.0   # 未编造成本 → 不累计


# ---------------------------------------------------------------------------
# 3. 错误归类（401 / 余额 / 网络分别报）+ 降级链
# ---------------------------------------------------------------------------
def test_classify_error_three_kinds():
    from apps.deskpet.backend.llm import classify_error
    assert classify_error(RuntimeError("401 Unauthorized"))[0] == "key_invalid"
    assert classify_error(RuntimeError("402 Insufficient balance"))[0] == "balance"
    assert classify_error(RuntimeError("余额不足"))[0] == "balance"
    assert classify_error(
        ConnectionError("Connection refused"))[0] == "network"
    assert classify_error(TimeoutError("request timeout"))[0] == "network"


def test_on_state_401_then_ollama_fallback_then_rules(env, monkeypatch):
    """开 + Key 无效：401 如实报 → ollama-local 兜底也失败 → 回规则版，
    degrade_reason 必须把两级原因说清楚，不假装成功。"""
    from apps.deskpet.backend import llm
    _inject_key(monkeypatch)
    _write_settings(env, model_switch="on")
    _patch_model_layer(monkeypatch, _primary_specs(
        {"raise": RuntimeError("401 Unauthorized")},
        fallback_behavior={"raise": ConnectionError("Connection refused")}))
    c = _client()
    r = c.post("/api/notice/parse", json={
        "notice_text": "11/8 前提交博客", "use_v4_pro": False})
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True
    assert "401" in body["degrade_reason"]
    assert "ollama" in body["degrade_reason"]
    assert body["data"]["items"], "规则版兜底仍有产出"
    # 通信类错误不消耗校验重试：主力 1 次 + ollama 兜底 1 次
    from apps.deskpet.backend import model_counter
    assert model_counter.by_route()["notice/parse"] == 1
    assert model_counter.by_route()["notice/parse#ollama-fallback"] == 1


def test_validation_retry_then_fallback_to_rule_reply(env, monkeypatch):
    """非法 JSON：Pydantic 校验重试（≤2 次）→ ollama → 规则兜底（页面不空白）。

    会话 5 前这条打的是已下架的游戏策略接口；现在换成 /api/chat ——
    走的是同一条 gateway 重试/降级链，
    断言口径不变：主力重试 2 次 + ollama 兜底 1 次 + 最终规则回复。
    """
    _inject_key(monkeypatch)
    _patch_model_layer(monkeypatch, _primary_specs({"invalid": True}))
    c = _client()
    r = c.post("/api/chat", json={
        "message": "这一句够长，不会被当成打招呼", "session_id": "sess-invalid"})
    assert r.status_code == 200
    body = r.json()
    assert body["degraded"] is True
    assert body["source"] == "rule"                      # 规则兜底，不空白
    assert body["data"]["reply"].strip()
    assert "校验失败" in body["degrade_reason"]
    # 主力重试 2 次（方案 B ≤2 收敛）+ ollama 1 次
    from apps.deskpet.backend import model_counter
    assert model_counter.by_route()["chat"] == 2
    assert model_counter.by_route()["chat#ollama-fallback"] == 1


# ---------------------------------------------------------------------------
# 4. 成本闸：单次 / 每日上限，超限自动降级并显示原因
# ---------------------------------------------------------------------------
def test_daily_cap_blocks_before_call(env, monkeypatch):
    """今日成本已达每日上限 → 调用前拦下（不烧钱），原因带数字。"""
    from apps.deskpet.backend import cost, model_counter
    _inject_key(monkeypatch)
    # 先垫今日账本：flash 1000+1000 tok = 0.005 元 ≥ 上限 0.001 元
    cost.record_usage("seed", "deepseek-chat",
                      {"input_tokens": 1000, "output_tokens": 1000},
                      {"input_per_mtok": 1.0, "output_per_mtok": 4.0})
    _write_settings(env, model_switch="auto",
                    cost_caps={"per_call_yuan": 0.5, "daily_yuan": 0.001})
    _patch_model_layer(monkeypatch, _primary_specs(
        {"parsed_for": {"NoticeParseResult": _notice_parsed()}}))
    c = _client()
    r = c.post("/api/notice/parse", json={
        "notice_text": "11/8 前提交博客", "use_v4_pro": False})
    assert r.json()["degraded"] is True
    assert "每日上限" in r.json()["degrade_reason"]
    assert "0.001" in r.json()["degrade_reason"]
    # 只垫的那 1 次入账，被封在闸外：本路由零调用
    assert model_counter.total() == 0


def test_per_call_cap_degrades_after_huge_usage(env, monkeypatch):
    """单次调用成本超上限（1M+1M tok ≈ 5 元 > 0.5 元）→ 如实入账并降级。"""
    _inject_key(monkeypatch)
    _patch_model_layer(monkeypatch, _primary_specs({
        "parsed_for": {"NoticeParseResult": _notice_parsed()},
        "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000}}))
    c = _client()
    r = c.post("/api/notice/parse", json={
        "notice_text": "11/8 前提交博客", "use_v4_pro": False})
    body = r.json()
    assert body["degraded"] is True
    assert "单次成本" in body["degrade_reason"]
    entries = _cost_entries()
    assert entries[0]["cost_yuan"] == pytest.approx(5.0)  # 1.0 + 4.0 元/Mtok


# ---------------------------------------------------------------------------
# 5. Key 管理本地接口：脱敏 / 删除立刻回规则版 / 连通性分类
# ---------------------------------------------------------------------------
def test_key_status_masked_and_delete_takes_effect(env, monkeypatch):
    """status 只回「已配置」+ 尾四位，明文不外泄；DELETE 立刻回规则版。"""
    _inject_key(monkeypatch, key="sk-real-secret-9999")
    c = _client()
    r = c.get("/api/key/status")
    assert r.status_code == 200
    body = r.json()
    assert body["configured"] is True
    assert body["masked_tail"] == "9999"
    assert "sk-real-secret-9999" not in json.dumps(body)  # 明文绝不外泄
    r = c.delete("/api/key")
    assert r.status_code == 200 and r.json()["deleted"] is True
    r = c.get("/api/key/status")
    assert r.json()["configured"] is False
    # 删完立刻回规则版
    r = c.post("/api/notice/parse", json={
        "notice_text": "11/8 前提交博客", "use_v4_pro": False})
    assert r.json()["degraded"] is True
    assert "自动档未探测到 Key" in r.json()["degrade_reason"]


@pytest.mark.parametrize("exc,expect_status", [
    (RuntimeError("401 Unauthorized api key"), "key_invalid"),
    (RuntimeError("402 Insufficient balance"), "balance"),
    (ConnectionError("Connection refused"), "network"),
])
def test_key_check_classifies_errors(env, monkeypatch, exc, expect_status):
    """一键连通性检查：401/余额/网络分别返回，不吞异常。"""
    _inject_key(monkeypatch)
    _patch_model_layer(monkeypatch, _primary_specs({"raise": exc}))
    c = _client()
    r = c.post("/api/key/check")
    assert r.status_code == 200
    assert r.json()["status"] == expect_status
    assert r.json()["ok"] is False


def test_key_check_ok_when_model_replies(env, monkeypatch):
    _inject_key(monkeypatch)
    _patch_model_layer(monkeypatch, _primary_specs(
        {"parsed_for": {"NoticeParseResult": _notice_parsed()},
         "usage": {"input_tokens": 1, "output_tokens": 1}}))
    c = _client()
    r = c.post("/api/key/check")
    assert r.json()["status"] == "ok" and r.json()["ok"] is True
    entries = _cost_entries()
    assert entries and entries[0]["route"] == "key/check"


def test_key_check_not_configured_without_key(env):
    """无 Key：连通性检查直接回 not_configured，不发任何请求。"""
    c = _client()
    r = c.post("/api/key/check")
    assert r.json()["status"] == "not_configured"


# ---------------------------------------------------------------------------
# 6. 能力注册表（口子③）+ 静态懒加载铁闸自检
# ---------------------------------------------------------------------------
def test_capabilities_registry_declares_four_fields():
    """五项能力每项都声明 requires_network/requires_key/
    degraded_fallback/enabled 四字段；主交互不设单会话配额。"""
    from apps.deskpet.backend import capabilities
    assert len(capabilities.all_capabilities()) >= 5
    for cap in capabilities.all_capabilities():
        assert isinstance(cap.requires_network, bool)
        assert isinstance(cap.requires_key, bool)
        assert isinstance(cap.degraded_fallback, str) and cap.degraded_fallback
        assert isinstance(cap.enabled, bool)
    assert capabilities.get("free_chat").per_session_cap is None
    assert capabilities.get("agent_chat").per_session_cap is None
    assert capabilities.get("nonexistent") is None


def test_capabilities_endpoint_lists_quota(env):
    c = _client()
    r = c.get("/api/capabilities")
    assert r.status_code == 200
    body = r.json()
    assert body["switch"] == "auto"
    assert len(body["capabilities"]) >= 5
    assert body["quota"]["per_call_cap_yuan"] == 0.5
    assert body["quota"]["daily_cap_yuan"] == 2.0
    assert body["quota"]["today_cost_yuan"] == 0.0


def test_llm_module_top_level_stays_lazy():
    """懒加载铁闸静态自检：apps/deskpet/backend/llm.py 顶层不得出现
    infrastructure.llm.models / langchain / infrastructure.cache / tools 的 import
    （它们只允许出现在函数体内的懒加载点）。"""
    src = Path(__file__).resolve().parents[2].joinpath(
        "apps", "deskpet", "backend", "llm.py").read_text(encoding="utf-8")
    for line in src.splitlines():
        if line.startswith(("import ", "from ")):   # 顶层（无缩进）
            low = line.lower()
            assert "infrastructure.llm.models" not in low, f"顶层 import 打穿关态闸门：{line}"
            assert "langchain" not in low, f"顶层 import 打穿关态闸门：{line}"
            assert "infrastructure.cache" not in low, f"顶层 import 打穿关态闸门：{line}"
            assert not low.startswith(("from apps.agent.tools", "import apps.agent.tools")), \
                f"顶层 import 禁用工具包：{line}"


# ---------------------------------------------------------------------------
# 7. from-voice / task-qa：模型档与降级档
# ---------------------------------------------------------------------------
def test_from_voice_and_task_qa_model_and_fallback(env, monkeypatch):
    """自动+假 Key：from-voice/task-qa 走模型出固定 JSON；
    切「关」后：from-voice 规则兜底、task-qa 静态兜底，绝不 500。"""
    from apps.deskpet.backend.models import TaskQAAnswer, VoiceParseResult
    _inject_key(monkeypatch)
    _patch_model_layer(monkeypatch, _primary_specs({
        "parsed_for": {
            "VoiceParseResult": VoiceParseResult(
                title="开会", deadline=None, confidence=0.9),
            "TaskQAAnswer": TaskQAAnswer(answer="先把合照约上",
                                         suggestions=["今晚群里约"]),
        }}))
    c = _client()
    # 建一条待办供 task/qa 锁定
    r = c.post("/api/todos", json={"title": "交博客", "gap": "还差合照",
                                   "source": "manual"})
    tid = r.json()["data"]["id"]

    r = c.post("/api/todo/from-voice", json={"text": "后天下午三点提醒我开会"})
    assert r.status_code == 200 and r.json()["degraded"] is False
    assert r.json()["data"]["title"] == "开会"

    r = c.post("/api/task/qa", json={"todo_id": tid, "question": "还差什么？"})
    assert r.status_code == 200
    assert r.json() == {"type": "done", "content": "先把合照约上",
                        "suggestions": ["今晚群里约"], "degraded": False}

    # 任务不存在：type=error，不 500、不空白
    r = c.post("/api/task/qa", json={"todo_id": "no-such", "question": "?"})
    assert r.status_code == 200 and r.json()["type"] == "error"

    # 切「关」：from-voice 规则兜底（抽到日期）、task-qa 静态兜底
    r = c.post("/api/settings/model-switch", json={"state": "off"})
    assert r.status_code == 200
    r = c.post("/api/todo/from-voice",
               json={"text": "11/8 23:59 前提醒我交博客"})
    body = r.json()
    assert r.status_code == 200 and body["degraded"] is True
    assert body["degrade_reason"].startswith("模型开关=关")
    assert body["data"]["deadline"] is not None   # 规则版抽到 11/8 23:59
    r = c.post("/api/task/qa", json={"todo_id": tid, "question": "还差什么？"})
    assert r.status_code == 200
    assert r.json()["type"] == "degrade"
    assert "还差合照" in r.json()["content"]      # 静态兜底带任务上下文
    from apps.deskpet.backend import model_counter
    assert model_counter.total() == 2   # 关掉之后零新增调用


# ---------------------------------------------------------------------------
# 真 Key 才暴露的两种形态（桩不校验请求/容忍畸形，桩测全绿也发现不了）
# ---------------------------------------------------------------------------
def test_structured_call_passes_auto_tool_choice():
    """必须以 tool_choice="auto" 调用结构化输出。

    DeepSeek 思考模式对「强制绑定某个函数」的 tool_choice 直接回
    400「Thinking mode does not support this tool_choice」；LangChain 默认
    正是发强制绑定形态。这条守住修复不被回退（真 Key 实测：改回默认即 400）。
    """
    from apps.deskpet.backend.llm import _structured_call
    from apps.deskpet.backend.models import NoticeParseResult

    class _Recorder:
        def __init__(self):
            self.kw = None

        def with_structured_output(self, schema, include_raw=False, **kw):
            self.kw = kw
            return self

    m = _Recorder()
    _structured_call(m, NoticeParseResult, [])
    assert m.kw.get("tool_choice") == "auto"


def test_structured_call_falls_back_when_kw_unsupported():
    """不认 tool_choice 关键字的模型包装不该因此报错（回退默认写法）。"""
    from apps.deskpet.backend.llm import _structured_call
    from apps.deskpet.backend.models import NoticeParseResult

    class _Strict:
        def with_structured_output(self, schema, include_raw=False):
            return "ok"

    assert _structured_call(_Strict(), NoticeParseResult, []) == "ok"


def test_recover_structure_from_trailing_brace():
    """参数串尾部多一个 `}`：内容是对的，要救回来而不是重试降级。"""
    from langchain_core.messages import AIMessage
    from apps.deskpet.backend.llm import _recover_structure
    from apps.deskpet.backend.models import NoticeParseResult

    payload = {"items": [{"text": "交周报", "category": "deliverable",
                          "matched": False}],
               "deadline_hint": None, "summary": "例会"}
    msg = AIMessage(content="", invalid_tool_calls=[{
        "type": "invalid_tool_call", "id": "c1", "name": "NoticeParseResult",
        "args": json.dumps(payload, ensure_ascii=False) + "}",
        "error": "Extra data"}])
    got = _recover_structure({"raw": msg}, NoticeParseResult)
    assert got is not None and got.summary == "例会"


def test_recover_structure_rejects_unrelated_json():
    """字段名全对不上的 JSON 不许被收成「全默认值空对象」（伪装成功）。"""
    from langchain_core.messages import AIMessage
    from apps.deskpet.backend.llm import _recover_structure
    from apps.deskpet.backend.models import ChatReply

    msg = AIMessage(content='{"items": [], "summary": "x"}')
    assert _recover_structure({"raw": msg}, ChatReply) is None


def test_gateway_recovers_invalid_tool_call_without_retry(env, monkeypatch):
    """parsed=None 但参数可救 → degraded=False、只调用 1 次（不重试不降级）。"""
    from langchain_core.messages import AIMessage
    from apps.deskpet.backend import llm

    _inject_key(monkeypatch)
    payload = {"items": [{"text": "交周报", "category": "deliverable",
                          "matched": False}],
               "deadline_hint": None, "summary": "例会"}
    msg = AIMessage(content="", invalid_tool_calls=[{
        "type": "invalid_tool_call", "id": "c1", "name": "NoticeParseResult",
        "args": json.dumps(payload, ensure_ascii=False) + "}", "error": "Extra data"}])
    model = FakeModel({"invalid_recoverable": msg,
                       "usage": {"input_tokens": 10, "output_tokens": 10}})
    _patch_model_layer(monkeypatch, {
        llm.PRIMARY_ID: model, llm.MANUAL_ID: model,
        llm.LOCAL_FALLBACK_ID: FakeModel({})})

    r = _client().post("/api/notice/parse",
                       json={"notice_text": "例会通知", "use_v4_pro": False})
    body = r.json()
    assert r.status_code == 200
    assert body["degraded"] is False and body["degrade_reason"] == ""
    assert body["data"]["summary"] == "例会"
    assert model.calls == 1


def test_gateway_sends_auto_tool_choice_end_to_end(env, monkeypatch):
    """走 HTTP 也带着 tool_choice=auto（防止只在 helper 层对、调用点没接上）。"""
    from apps.deskpet.backend import llm

    _inject_key(monkeypatch)
    model = FakeModel({"parsed_for": {"NoticeParseResult": _notice_parsed()}})
    _patch_model_layer(monkeypatch, {
        llm.PRIMARY_ID: model, llm.MANUAL_ID: model,
        llm.LOCAL_FALLBACK_ID: FakeModel({})})
    _client().post("/api/notice/parse",
                   json={"notice_text": "例会通知", "use_v4_pro": False})
    assert (model.last_kw or {}).get("tool_choice") == "auto"


def test_classify_error_400_is_request_rejected():
    """400 单列一类：不是 Key 问题也不是网络问题，别让用户在 Key 上瞎折腾。"""
    from apps.deskpet.backend.llm import classify_error

    kind, human = classify_error(RuntimeError(
        "Error code: 400 - Thinking mode does not support this tool_choice"))
    assert kind == "request_rejected"
    assert "400" in human


# ---------------------------------------------------------------------------
# 真 Key 才暴露的两种形态（桩不校验请求/容忍畸形，桩测全绿也发现不了）
# ---------------------------------------------------------------------------
def test_structured_call_passes_auto_tool_choice():
    """必须以 tool_choice="auto" 调用结构化输出。

    DeepSeek 思考模式对「强制绑定某个函数」的 tool_choice 直接回
    400「Thinking mode does not support this tool_choice」；LangChain 默认
    正是发强制绑定形态。这条守住修复不被回退（真 Key 实测：改回默认即 400）。
    """
    from apps.deskpet.backend.llm import _structured_call
    from apps.deskpet.backend.models import NoticeParseResult

    class _Recorder:
        def __init__(self):
            self.kw = None

        def with_structured_output(self, schema, include_raw=False, **kw):
            self.kw = kw
            return self

    m = _Recorder()
    _structured_call(m, NoticeParseResult, [])
    assert m.kw.get("tool_choice") == "auto"


def test_structured_call_falls_back_when_kw_unsupported():
    """不认 tool_choice 关键字的模型包装不该因此报错（回退默认写法）。"""
    from apps.deskpet.backend.llm import _structured_call
    from apps.deskpet.backend.models import NoticeParseResult

    class _Strict:
        def with_structured_output(self, schema, include_raw=False):
            return "ok"

    assert _structured_call(_Strict(), NoticeParseResult, []) == "ok"


def test_recover_structure_from_trailing_brace():
    """参数串尾部多一个 `}`：内容是对的，要救回来而不是重试降级。"""
    from langchain_core.messages import AIMessage
    from apps.deskpet.backend.llm import _recover_structure
    from apps.deskpet.backend.models import NoticeParseResult

    payload = {"items": [{"text": "交周报", "category": "deliverable",
                          "matched": False}],
               "deadline_hint": None, "summary": "例会"}
    msg = AIMessage(content="", invalid_tool_calls=[{
        "type": "invalid_tool_call", "id": "c1", "name": "NoticeParseResult",
        "args": json.dumps(payload, ensure_ascii=False) + "}",
        "error": "Extra data"}])
    got = _recover_structure({"raw": msg}, NoticeParseResult)
    assert got is not None and got.summary == "例会"


def test_recover_structure_rejects_unrelated_json():
    """字段名全对不上的 JSON 不许被收成「全默认值空对象」（伪装成功）。"""
    from langchain_core.messages import AIMessage
    from apps.deskpet.backend.llm import _recover_structure
    from apps.deskpet.backend.models import ChatReply

    msg = AIMessage(content='{"items": [], "summary": "x"}')
    assert _recover_structure({"raw": msg}, ChatReply) is None


def test_gateway_recovers_invalid_tool_call_without_retry(env, monkeypatch):
    """parsed=None 但参数可救 → degraded=False、只调用 1 次（不重试不降级）。"""
    from langchain_core.messages import AIMessage
    from apps.deskpet.backend import llm

    _inject_key(monkeypatch)
    payload = {"items": [{"text": "交周报", "category": "deliverable",
                          "matched": False}],
               "deadline_hint": None, "summary": "例会"}
    msg = AIMessage(content="", invalid_tool_calls=[{
        "type": "invalid_tool_call", "id": "c1", "name": "NoticeParseResult",
        "args": json.dumps(payload, ensure_ascii=False) + "}", "error": "Extra data"}])
    model = FakeModel({"invalid_recoverable": msg,
                       "usage": {"input_tokens": 10, "output_tokens": 10}})
    _patch_model_layer(monkeypatch, {
        llm.PRIMARY_ID: model, llm.MANUAL_ID: model,
        llm.LOCAL_FALLBACK_ID: FakeModel({})})

    r = _client().post("/api/notice/parse",
                       json={"notice_text": "例会通知", "use_v4_pro": False})
    body = r.json()
    assert r.status_code == 200
    assert body["degraded"] is False and body["degrade_reason"] == ""
    assert body["data"]["summary"] == "例会"
    assert model.calls == 1


def test_gateway_sends_auto_tool_choice_end_to_end(env, monkeypatch):
    """走 HTTP 也带着 tool_choice=auto（防止只在 helper 层对、调用点没接上）。"""
    from apps.deskpet.backend import llm

    _inject_key(monkeypatch)
    model = FakeModel({"parsed_for": {"NoticeParseResult": _notice_parsed()}})
    _patch_model_layer(monkeypatch, {
        llm.PRIMARY_ID: model, llm.MANUAL_ID: model,
        llm.LOCAL_FALLBACK_ID: FakeModel({})})
    _client().post("/api/notice/parse",
                   json={"notice_text": "例会通知", "use_v4_pro": False})
    assert (model.last_kw or {}).get("tool_choice") == "auto"


def test_classify_error_400_is_request_rejected():
    """400 单列一类：不是 Key 问题也不是网络问题，别让用户在 Key 上瞎折腾。"""
    from apps.deskpet.backend.llm import classify_error

    kind, human = classify_error(RuntimeError(
        "Error code: 400 - Thinking mode does not support this tool_choice"))
    assert kind == "request_rejected"
    assert "400" in human
