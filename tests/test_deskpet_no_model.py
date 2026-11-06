"""tests/test_deskpet_no_model.py —— 关态防回归测试（硬闸门 3，v6.1 口径）

阶段 3 起语义升级：不再断言「阶段 2 不碰模型」，而是断言
「开关=关（或自动+无 Key）时零模型层 import、零调用」——
关态懒加载铁闸（deskpet/llm.py 顶层零 langchain 系 import）的证据链。

证据链（P0-4 返工后保留）：
  1. sys.modules 断言（子进程隔离，跑完含导出 docx 的主流程后检查）：
     agent.models / tools.coding / langchain chat model 基类都不应进进程。
     「导出之后」时点也要干净——方案 A（deskpet/docx_gen 纯函数）保证。
  2. 真 monkeypatch 拦截：patch 掉 langchain chat model 的 invoke/ainvoke
     入口，被调即抛 AssertionError。附 patch 生效自检——故意调一次被
     patch 的函数确认抛错，排除「patch 了从未被调用的路径」的假绿。
  3. 主流程跑完 model_counter.total() == 0。

测试环境口径（附录A-2）：清 KEY + 显式设「关」，两者都做。

运行：python -m pytest tests/test_deskpet_no_model.py -q
"""

import json
import os
import subprocess
import sys

import pytest

# 子进程里跑的主流程脚本：清 KEY + 设开关=关 → 建待办 → 补报 → 贴通知 →
# 确认落待办 → 勾选 → 导出 docx（关键时点：导出最容易把 tools.coding/
# langchain 拖进进程）→ 游戏策略/复盘降级路由也打一遍 → 打印 sys.modules。
_SUBPROCESS_CODE = r"""
import sys, json, os, tempfile
sys.path.insert(0, ".")
os.environ["DESKPET_DATA_DIR"] = tempfile.mkdtemp(prefix="deskpet-sub-")
os.environ.pop("DEEPSEEK_API_KEY", None)   # 关态口径：清 KEY（附录A-2）
from fastapi import FastAPI
from fastapi.testclient import TestClient
from deskpet.api import router, download_router
from deskpet.auth import auth_middleware
from deskpet.games.builtin.mini_snake import register
from deskpet import settings

settings.set_switch(settings.SWITCH_OFF)   # 关态口径：显式设「关」

app = FastAPI()
app.middleware("http")(auth_middleware)
app.include_router(router)
app.include_router(download_router)
register()

c = TestClient(app, headers={"X-User-Id":
    "00000000-0000-4000-8000-0000000000aa"})
r = c.post("/api/todos", json={"title": "交博客", "source": "manual"})
assert r.status_code == 200, r.text
tid = r.json()["data"]["id"]
r = c.get("/api/catchup"); assert r.status_code == 200
r = c.post("/api/notice/parse", json={
    "notice_text": "11/8 23:59 前提交博客，含合照", "use_v4_pro": False})
assert r.status_code == 200, r.text
assert r.json()["degraded"] is True          # 关态：规则版降级
r = c.post("/api/todo/from-voice", json={"text": "后天下午三点开会"})
assert r.status_code == 200, r.text          # 关态降级不 500
assert r.json()["degraded"] is True
r = c.post("/api/task/qa", json={
    "todo_id": tid, "question": "还差什么？"})
assert r.status_code == 200, r.text          # 关态静态兜底不 500
r = c.post("/api/notice/confirm", json={"items": [
    {"text": "提交博客", "category": "deliverable", "matched": False}]})
assert r.status_code == 200, r.text
r = c.patch(f"/api/todos/{tid}", json={"status": "done"})
assert r.status_code == 200
r = c.post("/api/export/docx", json={"todo_ids": [tid], "title": "清单"})
assert r.status_code == 200, r.text
r = c.get(r.json()["download_url"]); assert r.status_code == 200

print(json.dumps({
    "agent_models": [m for m in sys.modules if m == "agent.models"],
    "memory_cache": [m for m in sys.modules if m == "memory.cache"],
    "tools_coding": [m for m in sys.modules if m == "tools.coding"],
    "tools_pkg_init": [m for m in sys.modules if m == "tools"],
    "lc_chat": [m for m in sys.modules
                if m.startswith("langchain")
                and ("language_models" in m or "chat_models" in m)],
}))
"""


def test_import_and_full_flow_keep_model_layer_out():
    """关态主流程（含导出 docx 与降级路由之后）sys.modules 不出现模型层。

    子进程隔离：不受同 pytest 进程其它测试的 import 污染
    （test_deskpet_llm.py 会故意 import 模型层——那是「开/自动有 Key」档）。
    """
    r = subprocess.run([sys.executable, "-c", _SUBPROCESS_CODE],
                       capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(
                           os.path.abspath(__file__))),
                       timeout=120)
    assert r.returncode == 0, f"子进程失败:\n{r.stdout}\n{r.stderr}"
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert not out["agent_models"], (
        f"agent.models 被拉进进程：{out['agent_models']}")
    assert not out["memory_cache"], (
        f"memory.cache 被拉进进程：{out['memory_cache']}——关态绝不能启用缓存")
    assert not out["tools_coding"], (
        f"tools.coding(run_shell 所在模块) 被拉进进程：{out['tools_coding']}——"
        f"违反方案 A 隔离导入口径")
    assert not out["tools_pkg_init"], (
        f"tools 包根(__init__) 被拉进进程：{out['tools_pkg_init']}——"
        f"它 import coding，说明有代码绕开了 docx_gen 纯函数")
    assert not out["lc_chat"], (
        f"语言模型基类被拉进进程：{out['lc_chat']}")


def test_model_counter_is_zero_after_import():
    """干净子进程里 import 后（未发生任何模型调用时）计数必须为 0。

    P1 返修（阶段 4）：旧写法先 reset() 再断言 0，断言恒真、测不出
    「import 本身产生调用」。改用子进程隔离（照抄同文件
    test_import_and_full_flow_keep_model_layer_out 的写法）：
    干净子进程里 import deskpet.api 后直接断言 total()==0，
    不用 reset 掩盖。测试意图不变：import 不应产生任何模型调用。
    """
    code = r"""
import sys, json, os, tempfile
sys.path.insert(0, ".")
os.environ["DESKPET_DATA_DIR"] = tempfile.mkdtemp(prefix="deskpet-sub-")
os.environ.pop("DEEPSEEK_API_KEY", None)
import deskpet.api
from deskpet import model_counter
print(json.dumps({"total": model_counter.total(),
                  "by_route": model_counter.by_route()}))
"""
    r = subprocess.run([sys.executable, "-c", code],
                       capture_output=True, text=True,
                       cwd=os.path.dirname(os.path.dirname(
                           os.path.abspath(__file__))),
                       timeout=120)
    assert r.returncode == 0, f"子进程失败:\n{r.stdout}\n{r.stderr}"
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["total"] == 0, (
        f"import 本身产生了模型调用：{out['by_route']}——关态不应调任何模型")


def test_main_flow_model_call_blocked(monkeypatch, tmp_path):
    """真拦截版主流程测试（P0-4，阶段 3 升级为「关态零调用」语义）。

    monkeypatch 拦住 langchain chat model 的 invoke/ainvoke——
    with_structured_output(...).ainvoke() 内部最终也走这条入口。
    一旦关态主流程任何一处碰了模型，测试当场炸，不是事后看计数。
    测试环境口径（附录A-2）：清 KEY + 显式设「关」，patch 断言保留。
    """
    # 导出物落 tmp（不写真实用户目录）
    monkeypatch.setenv("DESKPET_DATA_DIR", str(tmp_path / "data"))
    # 清 KEY（附录A-2：测试环境必须清 KEY）
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    from langchain_core.language_models.chat_models import BaseChatModel

    def _boom(*args, **kwargs):
        raise AssertionError("检测到模型调用！关态主流程不得触碰模型层")

    monkeypatch.setattr(BaseChatModel, "ainvoke", _boom, raising=True)
    monkeypatch.setattr(BaseChatModel, "invoke", _boom, raising=True)

    # —— patch 生效自检：故意调一次被 patch 的入口，确认真的会炸。
    # 没有这段，patch 打在从未被调用的路径上 = 恒绿空测试。
    with pytest.raises(AssertionError, match="检测到模型调用"):
        BaseChatModel.ainvoke(object(), "hello")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from deskpet.api import router, download_router
    from deskpet.auth import auth_middleware
    from deskpet import model_counter, settings
    from deskpet.games.builtin.mini_snake import register

    # 显式设「关」（附录A-2：清 KEY 与设关两者都做）
    settings.set_switch(settings.SWITCH_OFF)
    model_counter.reset()

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(router)
    app.include_router(download_router)
    register()

    headers = {"X-User-Id": "00000000-0000-4000-8000-000000000002"}
    client = TestClient(app, raise_server_exceptions=False, headers=headers)

    # 主流程：建待办 → 补报 → 贴通知抽清单 → 确认落待办 → 勾选完成 → 导出
    r = client.post("/api/todos", json={
        "title": "交软件工程博客",
        "deadline": "2026-11-08T23:59:00",
        "gap": "还差合照和队员风采", "source": "manual"})
    assert r.status_code == 200, f"建待办失败: {r.status_code} {r.text}"
    todo_id = r.json()["data"]["id"]

    r = client.get("/api/catchup")
    assert r.status_code == 200, f"补报失败: {r.status_code}"

    notice = ("【作业要求】11/8 23:59 前提交博客。内容含：队员风采、"
              "团队首次合照、选题报告。格式：固定模板。")
    r = client.post("/api/notice/parse", json={
        "notice_text": notice, "use_v4_pro": False})
    assert r.status_code == 200, f"通知解析失败: {r.status_code}"
    assert r.json()["degraded"] is True  # 关态：规则版降级
    assert "关" in r.json().get("degrade_reason", "")

    # 阶段 3 新路由在关态下同样降级、绝不 500
    r = client.post("/api/todo/from-voice", json={"text": "后天下午三点开会"})
    assert r.status_code == 200 and r.json()["degraded"] is True
    r = client.post("/api/task/qa", json={
        "todo_id": todo_id, "question": "还差什么？"})
    assert r.status_code == 200 and r.json()["degraded"] is True
    r = client.post("/api/game/strategy", json={
        "session_id": "sess-blocked",
        "state_snapshot": {"score": 10, "hp": 3}})
    assert r.status_code == 200 and r.json()["degraded"] is True
    assert r.json()["data"]["strategy_name"] == "balanced"  # 默认规则兜底
    r = client.post("/api/game/review", json={
        "session_id": "sess-blocked", "final_state": {"result": "lose"},
        "strategy_history": ["balanced"]})
    assert r.status_code == 200 and r.json()["degraded"] is True

    # P0-2 / Step 0-1：deadline 走前端单通道 —— 前端把 parse 抽出的
    # deadline_hint 存住，confirm 时显式回传 deadline_override（index.html
    # 的 confirmChecklist() 即此行为）。后端不再有进程内 hint 兜底。
    hint = client.post("/api/notice/parse", json={
        "notice_text": notice, "use_v4_pro": False}).json()["data"]["deadline_hint"]
    r = client.post("/api/notice/confirm", json={
        "items": [{"text": "提交博客", "category": "deliverable",
                   "matched": False}],
        "deadline_override": hint})
    assert r.status_code == 200, f"确认失败: {r.status_code}"
    created = r.json()["created"]
    assert created, "确认后应至少落一条待办"
    assert created[0]["deadline"] is not None, (
        "P0-2：前端回传的 deadline_override 必须落到待办，不应为 None")

    r = client.patch(f"/api/todos/{todo_id}", json={"status": "done"})
    assert r.status_code == 200, f"勾选失败: {r.status_code}"

    r = client.post("/api/export/docx", json={
        "todo_ids": [todo_id], "title": "待办交付清单"})
    assert r.status_code == 200, f"导出失败: {r.status_code} {r.text}"

    # 成长 + 模拟 + 清除（P0-3 新入口）
    r = client.get("/api/growth")
    assert r.status_code == 200
    r = client.post("/api/growth/simulate", json={"target_day": 30})
    assert r.status_code == 200
    r = client.post("/api/growth/simulate/clear")
    assert r.status_code == 200

    # 游戏开局（规则版）
    r = client.post("/api/game/start", json={
        "game_id": "mini-snake", "brain_level": 2})
    assert r.status_code == 200

    # 双保险：关态主流程跑完计数也必须为 0
    assert model_counter.total() == 0, (
        f"关态主流程跑完模型调用计数非 0：{model_counter.total()}，"
        f"by_route={model_counter.by_route()}")
