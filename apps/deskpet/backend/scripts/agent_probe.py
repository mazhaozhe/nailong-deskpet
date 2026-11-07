#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""agent_probe.py —— 会话 4 · C 轻量审批链路桩探针（可复现，无需真 Key）

跑法（项目根目录）：
    .venv/Scripts/python.exe apps/deskpet/backend/scripts/agent_probe.py

覆盖（输出紧凑 JSON，退出码 0 = 全过）：
    1. off_state_no_imports   关态下 import app + 打过 /api/agent/* 全部路由后，
                              sys.modules 仍零 langchain_core / apps.agent.tools*
    2. no_tools_import        静态扫 backend/**/*.py 无静态 tools import
    3. tool_table_confirmed   桩模型下 _ensure_built 后，实际传给 create_agent
                              的工具名与预期全量一致、无未知工具（日志+JSON 双证）
    4. reject_not_executed    write_file 被拒 → 文件确实没写（fs 证据，非文案）
    5. approve_executed       write_file 获批 → 文件以精确内容写出
    6. approval_id_one_shot   同一 approval_id 二次提交 → 明确报错
    7. decisions_by_index     batched 两个 gated 调用：approve+reject 按下标生效；
                              数量不匹配 → 明确报错；只读工具自动执行不进审批
    8. timeout_default_reject 审批超时（TTL 改旧）→ rejected_timeout + 文件不存在
    9. run_shell_also_gated   run_shell 同样触发审批（三闸门之一）
    9b run_shell_executes     run_shell 批准后真执行并拿到 stdout（async 工具回归：
                              同步 invoke 跑 async 工具会 NotImplementedError）
    9c open_url_*             open_url 进审批 + https-only 校验（打桩浏览器，不真开）
   10. audit_log_written      agent_audit.jsonl 有工具名/参数/决定记录

桩模型：StubModel 直接吐 AIMessage(tool_calls=...)，monkeypatch
langchain_deepseek.ChatDeepSeek——真模型的 tool binding 成功率另见
agent_probe_real.py（本脚本【不】证明真端点行为）。
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # scripts→backend→deskpet→apps→仓库根


class StubModel:  # 占位，真正类在 main() 里定义（要挂在 langchain 基类上）
    pass


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="deskpet-agent-probe-")
    os.environ["DESKPET_DATA_DIR"] = tmp
    os.environ["DEEPSEEK_API_KEY"] = "sk-stub-probe-not-real-0000"
    sys.path.insert(0, str(ROOT))

    checks: dict[str, bool] = {}
    evidence: dict = {"data_dir": tmp}

    # ---------- 2. 静态 no_tools_import ----------
    bad = []
    for p in (ROOT / "apps" / "deskpet" / "backend").rglob("*.py"):
        for i, line in enumerate(
                p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if re.match(r"^\s*(from|import)\s+"
                        r"(apps\.agent\.tools|infrastructure\.retrieval\.rag)\b",
                        line):
                bad.append(f"{p.relative_to(ROOT)}:{i}")
    checks["no_tools_import"] = not bad
    evidence["no_tools_import_hits"] = bad[:5]

    # ---------- 桩模型 ----------
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage
    from langchain_core.outputs import ChatGeneration, ChatResult

    class StubChatModel(BaseChatModel):
        responses: list = []
        i: int = 0

        def bind_tools(self, tools, **kw):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kw):
            idx = min(self.i, len(self.responses) - 1)
            self.i += 1
            return ChatResult(generations=[
                ChatGeneration(message=self.responses[idx])])

        @property
        def _llm_type(self):
            return "stub"

    import langchain_deepseek

    def install(responses):
        stub = StubChatModel(responses=responses)
        langchain_deepseek.ChatDeepSeek = lambda **kw: stub
        rt._agent = None
        return stub

    def tool_call(name, args, cid):
        return AIMessage(content="", tool_calls=[
            {"name": name, "args": args, "id": cid, "type": "tool_call"}])

    # ---------- 1. 关态零加载（子进程隔离：干净进程里 import app + 打
    #              /api/agent/* 全路由后查 sys.modules；本进程后面要桩测，
    #              langchain 必然进进程，不能在本进程查） ----------
    sub_code = r"""
import json, os, sys, tempfile, uuid
os.environ["DESKPET_DATA_DIR"] = tempfile.mkdtemp(prefix="deskpet-agent-probe-sub-")
os.environ.pop("DEEPSEEK_API_KEY", None)   # 关态口径：无 Key + 模式不开
sys.path.insert(0, os.environ["PROBE_ROOT"])
from fastapi.testclient import TestClient
from apps.deskpet.backend.app import app
c = TestClient(app, headers={"X-User-Id": str(uuid.uuid4())})
c.post("/api/agent/mode", json={"enabled": True})
c.get("/api/agent/status")
c.post("/api/agent/chat", json={"message": "在吗", "session_id": "s0"})
c.post("/api/agent/approve", json={"approval_id": "deadbeefdeadbeef", "decisions": [{"type": "approve"}]})
c.post("/api/agent/session/reset", json={"session_id": "s0"})
c.post("/api/agent/mode", json={"enabled": False})
banned = [m for m in sys.modules
          if m == "langchain_core" or m.startswith("apps.agent.tools")
          or m in ("infrastructure.cache", "infrastructure.llm.models", "langchain_community")]
print(json.dumps({"banned": banned}))
"""
    import subprocess
    env = dict(os.environ, PROBE_ROOT=str(ROOT))
    sr = subprocess.run([sys.executable, "-c", sub_code],
                        capture_output=True, text=True, env=env, timeout=120)
    try:
        sub_banned = json.loads(sr.stdout.strip().splitlines()[-1])["banned"]
    except Exception:
        sub_banned = [f"subprocess failed: {sr.stderr[-300:]}"]
    checks["off_state_no_imports"] = not sub_banned
    evidence["off_state_banned_modules"] = sub_banned

    # ---------- 3~9. 桩链路（直接驱动 runtime） ----------
    from apps.deskpet.backend import agent_runtime as ar
    ar.AgentRuntime.set_mode(True)
    rt = ar.get_runtime()

    async def run_all():
        # 3. 工具表核对
        install([AIMessage(content="你好呀")])
        r = await rt.chat("u-probe", "s-tools", "你好")
        checks["tool_table_confirmed"] = (
            r["status"] == "ok"
            and set(rt._tool_names) >= {"read_file", "write_file", "edit_file",
                                        "list_dir", "run_shell", "gen_docx",
                                        "gen_xlsx", "gen_pptx", "search_web"}
            and not (set(rt._tool_names)
                     - {"read_file", "write_file", "edit_file", "list_dir",
                        "run_shell", "gen_docx", "gen_xlsx", "gen_pptx",
                        "search_web", "open_url"}))
        evidence["tool_table"] = list(rt._tool_names)

        # 4. 拒绝 → 不执行
        target = Path(tmp) / "reject_me.txt"
        install([tool_call("write_file",
                           {"path": str(target), "content": "pwned"}, "c1"),
                 AIMessage(content="好的，不写了。")])
        r1 = await rt.chat("u-probe", "s-reject", "帮我写个文件")
        assert r1["status"] == "pending_approval", r1
        evidence["approval_description_sample"] = r1["action_requests"][0]["description"]
        r2 = await rt.approve("u-probe", r1["approval_id"], ["reject"])
        checks["reject_not_executed"] = (
            not target.exists() and r2["status"] == "ok")

        # 5. 批准 → 精确执行
        target2 = Path(tmp) / "approve_me.txt"
        install([tool_call("write_file",
                           {"path": str(target2), "content": "hello"}, "c2"),
                 AIMessage(content="写好了。")])
        b1 = await rt.chat("u-probe", "s-approve", "帮我写个文件")
        b2 = await rt.approve("u-probe", b1["approval_id"], ["approve"])
        checks["approve_executed"] = (
            b2["status"] == "ok" and target2.exists()
            and target2.read_text(encoding="utf-8") == "hello")

        # 6. approval_id 一次性
        c1 = await rt.approve("u-probe", b1["approval_id"], ["approve"])
        checks["approval_id_one_shot"] = "不存在或已被消费" in c1.get("degrade_reason", "")

        # 7. batched：只读自动执行 + 两个 gated 的按下标对应
        seed = Path(tmp) / "seed.txt"
        seed.write_text("seed-content", encoding="utf-8")
        install([tool_call("write_file",
                           {"path": str(Path(tmp) / "d.txt"), "content": "x"}, "c3"),
                 tool_call("read_file", {"path": str(seed)}, "c4"),
                 AIMessage(content="干完了。")])
        d1 = await rt.chat("u-probe", "s-batch", "写并读")
        gated = [a["name"] for a in d1["action_requests"]]
        checks["readonly_auto_executes"] = gated == ["write_file"]
        # batched 下 action_requests 只含 gated 工具 → decisions 数量=1；
        # read_file 已自动执行，不占审批位（C 轻量语义）
        d2 = await rt.approve("u-probe", d1["approval_id"], ["approve"])
        checks["readonly_auto_executes"] = (
            checks["readonly_auto_executes"] and d2["status"] == "ok"
            and (Path(tmp) / "d.txt").exists()
            and "read_file" in d2.get("tools_used", []))
        evidence["batched_gated_only"] = gated
        evidence["batched_tools_used"] = d2.get("tools_used", [])

        # 7b. 两个 gated（write_file + edit_file）打包成一个 interrupt：
        #     decisions 数量必须一一对应（1 个 ≠ 2 个 → 明确报错），
        #     随后 approve+reject 按下标各自生效（写执行、改不执行）。
        install([AIMessage(content="", tool_calls=[
                    {"name": "write_file",
                     "args": {"path": str(Path(tmp) / "m.txt"), "content": "x"},
                     "id": "c5", "type": "tool_call"},
                    {"name": "edit_file",
                     "args": {"path": str(Path(tmp) / "d.txt"),
                              "old_text": "x", "new_text": "y"},
                     "id": "c6", "type": "tool_call"}]),
                 AIMessage(content="ok")])
        m1 = await rt.chat("u-probe", "s-batch2", "写并改")
        two_gated = [a["name"] for a in m1["action_requests"]]
        evidence["batched_two_gated"] = two_gated
        m2m = await rt.approve("u-probe", m1["approval_id"], ["approve"])
        mismatch = ("一一对应" in m2m.get("degrade_reason", "")
                    and m2m["status"] == "degraded")
        m2 = await rt.approve("u-probe", m1["approval_id"], ["approve", "reject"])
        checks["decisions_by_index"] = (
            mismatch and m2["status"] == "ok"
            and (Path(tmp) / "m.txt").exists()
            and (Path(tmp) / "d.txt").read_text(encoding="utf-8") == "x")

        # 8. 超时默认拒绝
        install([tool_call("write_file",
                           {"path": str(Path(tmp) / "e.txt"), "content": "x"}, "c5"),
                 AIMessage(content="ok")])
        e1 = await rt.chat("u-probe", "s-timeout", "写文件")
        rt._pending[e1["approval_id"]]["created_mono"] -= 999
        e2 = await rt.approve("u-probe", e1["approval_id"], ["approve"])
        checks["timeout_default_reject"] = (
            e2["status"] == "rejected_timeout"
            and not (Path(tmp) / "e.txt").exists())

        # 9. run_shell 同样进审批
        install([tool_call("run_shell", {"command": "echo probe-ok"}, "c6"),
                 AIMessage(content="跑完了。")])
        f1 = await rt.chat("u-probe", "s-shell", "跑个命令")
        checks["run_shell_also_gated"] = (
            f1["status"] == "pending_approval"
            and f1["action_requests"][0]["name"] == "run_shell")
        evidence["run_shell_description"] = f1["action_requests"][0]["description"]
        # 拒绝收尾（不真跑命令）
        await rt.approve("u-probe", f1["approval_id"], ["reject"])

        # 9b. 批准后必须**真执行**——async 工具回归锚点。
        # 曾经的 bug：整图用同步 invoke 跑，run_shell 是 async 工具，
        # 一执行就 NotImplementedError: StructuredTool does not support
        # sync invocation（用户「打开哔哩哔哩」就是这么失败的）。
        install([tool_call("run_shell", {"command": "echo probe-async-ok"}, "c7"),
                 AIMessage(content="跑完了。")])
        g1 = await rt.chat("u-probe", "s-shell-run", "跑个命令")
        g2 = await rt.approve("u-probe", g1["approval_id"], ["approve"])
        shell_ok = False
        audit_path = Path(tmp) / "agent_audit.jsonl"
        if audit_path.exists():
            for line in audit_path.read_text(encoding="utf-8").splitlines():
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if (ev.get("event") == "tool_call" and ev.get("tool") == "run_shell"
                        and ev.get("status") == "ok"
                        and "probe-async-ok" in str(ev.get("result_head", ""))):
                    shell_ok = True
        checks["run_shell_executes_when_approved"] = (
            g1["status"] == "pending_approval"
            and g2["status"] == "ok"
            and "run_shell" in g2.get("tools_used", [])
            and shell_ok)
        evidence["run_shell_tools_used"] = g2.get("tools_used", [])
        evidence["run_shell_stdout_seen"] = shell_ok

        # 9c. open_url：进审批 + https-only 校验（打桩 webbrowser，不真开浏览器）
        import webbrowser
        opened: list[str] = []
        orig_open = webbrowser.open
        webbrowser.open = lambda url, new=0, autoraise=True: (
            opened.append(url), True)[1]
        try:
            install([tool_call("open_url",
                               {"url": "https://www.bilibili.com"}, "c8"),
                     AIMessage(content="打开了。")])
            h1 = await rt.chat("u-probe", "s-open", "打开哔哩哔哩")
            h2 = await rt.approve("u-probe", h1.get("approval_id", ""), ["approve"])
            checks["open_url_opens_https"] = (
                h1["status"] == "pending_approval"
                and h1["action_requests"][0]["name"] == "open_url"
                and h2["status"] == "ok"
                and opened == ["https://www.bilibili.com"])

            opened.clear()
            install([tool_call("open_url",
                               {"url": "file:///C:/Windows/System32/calc.exe"}, "c9"),
                     AIMessage(content="这个打不开。")])
            k1 = await rt.chat("u-probe", "s-open-bad", "打开本地文件")
            k2 = await rt.approve("u-probe", k1.get("approval_id", ""), ["approve"])
            checks["open_url_rejects_non_https"] = (
                k2["status"] == "ok" and opened == [])
            evidence["open_url_browser_calls"] = opened
        finally:
            webbrowser.open = orig_open

    asyncio.run(run_all())

    # 10. 审计日志
    audit = Path(tmp) / "agent_audit.jsonl"
    events = []
    if audit.exists():
        for line in audit.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    checks["audit_log_written"] = any(
        e.get("event") == "approval_decided" and "decisions" in e
        for e in events) and any(e.get("event") == "agent_tools_confirmed"
                                 for e in events)
    evidence["audit_event_kinds"] = sorted({e.get("event") for e in events})

    ar.AgentRuntime.set_mode(False)   # 探针收尾：不留第四态开着的设置

    result = {"ok": all(checks.values()), "checks": checks,
              "failures": [k for k, v in checks.items() if not v],
              "evidence": evidence}
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
