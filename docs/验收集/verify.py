#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""桌宠项目 · 检测方固化验收集（只读资产，写代码模型不许修改）

用法（在项目根目录运行）：
    .venv/Scripts/python.exe docs/验收集/verify.py --level quick
    .venv/Scripts/python.exe docs/验收集/verify.py --level full --verbose

quick：冒烟 + 断网清 KEY 主流程 + 导出链路 + 关态导入链 + 越界静态检查（不跑全套 pytest）
full ：quick + 全套 pytest + 真实 uvicorn HTTP（trust_env=False 绕开本机 HTTP_PROXY 劫持）
输出紧凑 JSON，交付时原样贴回；退出码 0 = 全过，1 = 有失败项。
"""
import argparse
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

BANNED_MODULES = (
    "agent.models", "tools", "tools.coding",
    "langchain_core", "langchain_community", "memory.cache",
)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--level", choices=("quick", "full"), default="quick")
    ap.add_argument("--root", default=None, help="项目根目录，默认当前工作目录")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    result = {"level": args.level, "root": str(root), "ok": False,
              "failures": [], "checks": {}, "metrics": {}}
    if not (root / "deskpet" / "app.py").is_file():
        result["failures"].append("未找到 deskpet/app.py：请在项目根目录运行，或传 --root")
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 1

    checks, failures, metrics = {}, [], {}

    def chk(name, cond, detail=None):
        checks[name] = bool(cond)
        if not cond:
            failures.append(name + (": " + str(detail) if detail else ""))

    tmp = Path(tempfile.mkdtemp(prefix="deskpet_verify_"))
    os.chdir(root)
    sys.path.insert(0, str(root))
    os.environ["DESKPET_DATA_DIR"] = str(tmp / "data")
    for k in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY", "DESKPET_ACCESS_TOKEN"):
        os.environ[k] = ""
    metrics["data_dir"] = str(tmp / "data")

    # 断网：只拦外网，放行 localhost / socketpair
    _orig_connect = socket.socket.connect

    def guarded(self, address):
        host = address[0] if isinstance(address, tuple) else str(address)
        if not isinstance(address, tuple) or host in ("127.0.0.1", "::1", "localhost"):
            return _orig_connect(self, address)
        raise AssertionError("外网连接被拦截: %r" % (address,))

    socket.socket.connect = guarded

    try:
        from fastapi.testclient import TestClient
        from deskpet.app import app
        from deskpet import paths
        import deskpet.api as api

        A, B = str(uuid.uuid4()), str(uuid.uuid4())
        ca = TestClient(app, headers={"X-User-Id": A})
        cb = TestClient(app, headers={"X-User-Id": B})
        ca.post("/api/growth/simulate/clear")

        # --- Step 0-1 陈旧 hint / 正向链路 / 跨用户 ---
        ca.post("/api/notice/parse", json={
            "notice_text": "【作业要求】11/8 23:59 前提交博客，含合照。", "use_v4_pro": False})
        p2 = ca.post("/api/notice/parse", json={
            "notice_text": "请补一张团队合照。", "use_v4_pro": False})
        hint2 = p2.json()["data"]["deadline_hint"]
        stale = ca.post("/api/notice/confirm", json={
            "items": [{"text": "补合照", "category": "format", "matched": False}]})
        stale_dl = stale.json()["created"][0]["deadline"]
        chk("stale_hint_null", hint2 is None and stale_dl is None,
            "hint2=%r deadline=%r" % (hint2, stale_dl))
        chk("no_global_hint_dict", not hasattr(api, "_last_deadline_hint"))

        p3 = ca.post("/api/notice/parse", json={
            "notice_text": "【作业要求】11/8 23:59 前提交博客，含合照。", "use_v4_pro": False})
        h3 = p3.json()["data"]["deadline_hint"]
        c_ok = ca.post("/api/notice/confirm", json={
            "items": [{"text": "提交博客", "category": "deliverable", "matched": False}],
            "deadline_override": h3})
        created = c_ok.json()["created"]
        dl = created[0]["deadline"] if created else None
        metrics["forward_deadline"] = dl
        chk("forward_deadline_exact", dl == "2026-11-08T23:59:00+08:00", dl)
        tid = created[0]["id"] if created else None

        ca.post("/api/growth/simulate", json={"target_day": 9})
        overdue = [t["title"] for t in ca.get("/api/catchup").json()["data"]["overdue"]]
        chk("overdue_named", "提交博客" in overdue, overdue)
        ca.post("/api/growth/simulate/clear")

        cb.post("/api/notice/confirm", json={
            "items": [{"text": "B的清单", "category": "deliverable", "matched": False}]})
        chk("cross_user_clean", cb.get("/api/growth").json()["data"]["days_known"] == 0)

        # --- Step 0-2 导出链路 ---
        if tid:
            ca.patch("/api/todos/" + tid, json={"status": "done"})
            ex = ca.post("/api/export/docx", json={
                "todo_ids": [tid], "title": "待办交付清单"})
            exj = ex.json()
            chk("expires_at_present", "expires_at" in exj, sorted(exj.keys()))
            url = exj.get("download_url", "")
            fname = url.rsplit("/", 1)[-1]
            rd = ca.get(url)
            chk("download_ok", rd.status_code == 200 and rd.content[:2] == b"PK",
                "status=%s bytes=%s" % (rd.status_code, len(rd.content)))
            metrics["download_bytes"] = len(rd.content)
            chk("static_bypass_404", ca.get("/static/exports/" + fname).status_code == 404)
            chk("static_dir_404", ca.get("/static/exports/").status_code == 404)
            chk("exports_listing_404", ca.get("/exports/").status_code == 404)
            chk("traversal_404", ca.get("/exports/..%2f..%2fetc%2fpasswd").status_code == 404)
            chk("fake_name_404",
                ca.get("/exports/deskpet_export_zzzzzzzz.docx").status_code == 404)
            chk("exports_outside_static", "static" not in str(paths.get_exports_dir()))

        # --- 关态硬闸门 ---
        mc = ca.get("/api/debug/model-calls").json()
        metrics["model_calls"] = mc
        chk("model_calls_zero", mc.get("total") == 0, mc)
        dirty = [m for m in BANNED_MODULES if m in sys.modules]
        chk("off_state_no_imports", not dirty, dirty)

        # --- 越界静态检查 ---
        bad_imports = []
        for p in (root / "deskpet").rglob("*.py"):
            for i, line in enumerate(p.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
                if re.match(r"^\s*(from|import)\s+(tools|memory\.rag)\b", line):
                    bad_imports.append("%s:%d" % (p.relative_to(root), i))
        chk("no_tools_import", not bad_imports, bad_imports[:5])
        allow = (root / "deskpet" / "tools_allowlist.py").read_text(encoding="utf-8", errors="ignore")
        chk("search_web_still_forbidden", "search_web" in allow and "FORBIDDEN_TOOLS" in allow)
        left = [str(p.relative_to(root)) for p in (root / "deskpet").rglob("*.docx")]
        chk("no_docx_in_source", not left, left[:5])
        chk("schemas_expires_at_untouched",
            "expires_at" in (root / "deskpet" / "schemas.py").read_text(encoding="utf-8", errors="ignore"))

        # --- 冒烟 ---
        py = root / ".venv" / "Scripts" / "python.exe"
        r = subprocess.run([str(py), "deskpet/scripts/smoke_routes.py"],
                           capture_output=True, text=True, timeout=240)
        tail = (r.stdout or "")[-4000:]
        n_pass = len(re.findall(r"\bPASS\b", tail))
        n_fail_lines = [l for l in tail.splitlines() if l.strip().upper().startswith("FAIL")]
        metrics["smoke"] = {"returncode": r.returncode, "PASS_lines": n_pass,
                            "FAIL_lines": len(n_fail_lines),
                            "tail": tail.strip().splitlines()[-1:]}
        chk("smoke_green", r.returncode == 0 and n_pass >= 1 and not n_fail_lines,
            metrics["smoke"])

        # --- full：全套 pytest + 真实 uvicorn HTTP ---
        if args.level == "full":
            t = subprocess.run([str(py), "-m", "pytest", "-q"],
                               capture_output=True, text=True, timeout=600)
            ptxt = (t.stdout or "")[-3000:]
            m = re.search(r"(\d+) passed", ptxt)
            metrics["pytest"] = {"returncode": t.returncode,
                                 "passed": int(m.group(1)) if m else None,
                                 "tail": ptxt.strip().splitlines()[-1:]}
            chk("pytest_green", t.returncode == 0 and m is not None, metrics["pytest"])
            metrics["http"] = http_probe(root, str(tmp / "http_data"))
            chk("http_real_green", metrics["http"].get("ok"), metrics["http"])
    except Exception as e:
        failures.append("probe_exception: %s: %s" % (type(e).__name__, e))
    finally:
        socket.socket.connect = _orig_connect

    result["checks"] = checks if args.verbose else {"total": len(checks),
                                                    "passed": sum(checks.values())}
    result["failures"] = failures
    result["metrics"] = metrics
    result["ok"] = not failures
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["ok"] else 1


def http_probe(root, data_dir):
    """真实 uvicorn 服务；trust_env=False 绕开本机 HTTP_PROXY 劫持。"""
    import time
    import httpx
    env = os.environ.copy()
    env["DESKPET_DATA_DIR"] = data_dir
    env["PYTHONPATH"] = str(root)
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "deskpet.app:app", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "error"],
        cwd=str(root), env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    base = "http://127.0.0.1:%d" % port
    out = {}
    try:
        for _ in range(40):
            try:
                httpx.get(base + "/api/growth", timeout=0.5, trust_env=False)
                break
            except Exception:
                time.sleep(0.25)
        c = httpx.Client(base_url=base, headers={"X-User-Id": str(uuid.uuid4())},
                         timeout=10, trust_env=False)
        tid = c.post("/api/todos", json={"title": "交博客", "source": "manual"}).json()["data"]["id"]
        ex = c.post("/api/export/docx", json={"todo_ids": [tid], "title": "真实HTTP清单"}).json()
        url = ex["download_url"]
        fname = url.rsplit("/", 1)[-1]
        d = c.get(url)
        out = {"download": d.status_code, "pk": d.content[:2] == b"PK",
               "static_bypass": c.get("/static/exports/" + fname).status_code,
               "traversal": c.get("/exports/..%2f..%2fetc%2fpasswd").status_code,
               "expires_at": "expires_at" in ex,
               "root_page": c.get("/").status_code}
        out["ok"] = (out["download"] == 200 and out["pk"] and out["static_bypass"] == 404
                     and out["traversal"] == 404 and out["expires_at"] and out["root_page"] == 200)
    except Exception as e:
        out = {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
    return out


if __name__ == "__main__":
    sys.exit(main())
