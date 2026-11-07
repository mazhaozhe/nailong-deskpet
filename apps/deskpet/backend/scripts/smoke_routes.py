"""apps/deskpet/backend/scripts/smoke_routes.py —— 接口契约冒烟（逐路由期望表）

判据演进：
  - 骨架期：所有路由体是 NotImplementedError，期望 500（可达即通过）
  - 实现期：已实现路由期望 2xx，未实现（接模型）路由仍期望 500
  - 阶段 3 接模型后：from-voice/task/qa 已实现（模型层 + 降级链），
    正常体期望 2xx，逐路由期望表自动区分

冒烟不接真实 API：显式清空注入 Key 的环境变量，让模型层
走「自动 + 无 Key → 降级规则版」档（附录 B：不接真实 API、不发真实请求）。

逐路由期望表（EXPECTED）是核心：它明确每个路由该 2xx 还是该 500，
避免阶段切换时集体误判。每个条目带方法/路径/有效体期望/校验期望。

运行：python -m apps.deskpet.backend.scripts.smoke_routes
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.deskpet.backend.api import router, download_router


def _build_app() -> FastAPI:
    app = FastAPI(title="deskpet-smoke")
    from apps.deskpet.backend.auth import auth_middleware
    app.middleware("http")(auth_middleware)
    app.include_router(router)
    app.include_router(download_router)
    return app


# 固定 user_id，让跨请求的待办能被 PATCH/DELETE/EXPORT 找到
_SMOKE_UID = "00000000-0000-4000-8000-000000000001"
_SMOKE_HEADERS = {"X-User-Id": _SMOKE_UID}


def _expect(label: str, cond: bool, detail: str = "") -> tuple[bool, str]:
    status = "PASS" if cond else "FAIL"
    return cond, f"  [{status}] {label}{(' — ' + detail) if detail else ''}"


# 期望状态码：2xx=已实现，500=NotImplementedError，422=契约校验拦截
OK = "2xx"
NOT_IMPL = 500
VALIDATION = 422


def _check(label: str, resp, expected: str) -> tuple[bool, str]:
    """根据期望类型判断状态码。OK 接受 200/201/202。"""
    if expected == OK:
        cond = 200 <= resp.status_code < 300
    elif expected == NOT_IMPL:
        cond = resp.status_code == 500
    elif expected == VALIDATION:
        cond = resp.status_code == 422
    else:
        cond = resp.status_code == expected
    return _expect(label, cond, f"got {resp.status_code}")


def main() -> int:
    # Step 0-2：导出物落注入的用户数据目录（冒烟不污染源码/真实用户目录），
    # 下载校验完顺手清理，不堆积。清 Key：冒烟只验「无 Key → 降级」档。
    import os
    import tempfile
    if not os.environ.get("DESKPET_DATA_DIR"):
        os.environ["DESKPET_DATA_DIR"] = tempfile.mkdtemp(
            prefix="deskpet-smoke-")
    from apps.deskpet.backend import llm as _llm
    os.environ.pop(_llm.KEY_ENV, None)   # 不接真实 API（附录 B）
    from apps.deskpet.backend import paths

    app = _build_app()
    client = TestClient(app, raise_server_exceptions=False,
                        headers=_SMOKE_HEADERS)
    results: list[tuple[bool, str]] = []

    # ==========================================================
    # 1. 成长
    # ==========================================================
    r = client.get("/api/growth")
    results.append(_check("GET /api/growth", r, OK))

    r = client.post("/api/growth/simulate", json={"target_day": 7})
    results.append(_check("POST /api/growth/simulate target=7", r, OK))
    r = client.post("/api/growth/simulate", json={"target_day": 9999})
    results.append(_check("POST /api/growth/simulate target=9999 校验拦截", r, VALIDATION))
    r = client.post("/api/growth/simulate", json={})
    results.append(_check("POST /api/growth/simulate 空体校验拦截", r, VALIDATION))
    r = client.post("/api/growth/simulate/clear")
    results.append(_check("POST /api/growth/simulate/clear 清除模拟", r, OK))

    # ==========================================================
    # 2. 待办 CRUD
    # ==========================================================
    r = client.get("/api/todos")
    results.append(_check("GET /api/todos", r, OK))

    r = client.post("/api/todos", json={
        "title": "交软件工程博客", "deadline": "2026-11-08T23:59:00",
        "gap": "还差合照和队员风采", "source": "manual"})
    results.append(_check("POST /api/todos 正常体", r, OK))
    r = client.post("/api/todos", json={"title": ""})
    results.append(_check("POST /api/todos 空 title 校验拦截", r, VALIDATION))
    r = client.post("/api/todos", json={"title": "x" * 201})
    results.append(_check("POST /api/todos title>200 校验拦截", r, VALIDATION))

    # PATCH/DELETE 用刚建的待办 id
    r = client.post("/api/todos", json={
        "title": "冒烟-待删", "gap": "", "source": "manual"})
    patch_id = r.json()["data"]["id"] if r.status_code == 200 else "smoke-temp-id"
    r = client.patch(f"/api/todos/{patch_id}", json={"status": "done"})
    results.append(_check("PATCH /api/todos/{id}", r, OK))
    r = client.delete(f"/api/todos/{patch_id}")
    results.append(_check("DELETE /api/todos/{id}", r, OK))

    # ==========================================================
    # 3. 回来补报
    # ==========================================================
    r = client.get("/api/catchup")
    results.append(_check("GET /api/catchup", r, OK))

    # ==========================================================
    # 4. 贴通知 → 抽清单
    # ==========================================================
    notice = ("【作业要求】11/8 23:59 前提交博客。内容含：队员风采、"
              "团队首次合照、选题报告。格式：固定模板。")
    r = client.post("/api/notice/parse", json={
        "notice_text": notice, "use_v4_pro": False})
    results.append(_check("POST /api/notice/parse 正常体", r, OK))
    r = client.post("/api/notice/parse", json={
        "notice_text": notice, "use_v4_pro": True})
    results.append(_check("POST /api/notice/parse use_v4_pro=True", r, OK))
    r = client.post("/api/notice/parse", json={"notice_text": ""})
    results.append(_check("POST /api/notice/parse 空通知校验拦截", r, VALIDATION))

    r = client.post("/api/notice/confirm", json={
        "items": [{"text": "提交博客", "category": "deliverable", "matched": False},
                  {"text": "含队员风采", "category": "format", "matched": False}],
        "deadline_override": "2026-11-08T23:59:00"})
    results.append(_check("POST /api/notice/confirm 正常体", r, OK))

    # ==========================================================
    # 5. 一句话变待办（阶段 3 已实现：模型 + 规则兜底，无 Key 降级 2xx）
    # ==========================================================
    r = client.post("/api/todo/from-voice", json={"text": "后天下午三点提醒我开会"})
    results.append(_check("POST /api/todo/from-voice 正常体（无Key降级）", r, OK))
    r = client.post("/api/todo/from-voice", json={"text": "x" * 501})
    results.append(_check("POST /api/todo/from-voice 超长校验拦截", r, VALIDATION))

    # ==========================================================
    # 6. 任务内一问一答（阶段 3 已实现：模型 + 静态兜底）
    # ==========================================================
    r = client.post("/api/task/qa", json={
        "todo_id": "todo-1", "question": "合照一般怎么拍？"})
    results.append(_check("POST /api/task/qa 正常体（无Key降级）", r, OK))

    # ==========================================================
    # 7. 导出 docx
    # ==========================================================
    # 先建两个待办拿真实 id
    r = client.post("/api/todos", json={
        "title": "冒烟待办A", "gap": "还差A", "source": "manual"})
    id_a = None
    if r.status_code == 200:
        id_a = r.json()["data"]["id"]
    r = client.post("/api/todos", json={
        "title": "冒烟待办B", "gap": "还差B", "source": "manual"})
    id_b = None
    if r.status_code == 200:
        id_b = r.json()["data"]["id"]
    ids = [x for x in (id_a, id_b) if x]
    r = client.post("/api/export/docx", json={
        "todo_ids": ids or ["dummy"], "title": "待办交付清单"})
    results.append(_check("POST /api/export/docx 正常体", r, OK))
    r = client.post("/api/export/docx", json={"todo_ids": []})
    results.append(_check("POST /api/export/docx 空ids校验拦截", r, VALIDATION))

    # P0-1：下载链路——200 + docx content-type + bytes>0
    r2 = client.post("/api/export/docx", json={
        "todo_ids": ids or ["dummy"], "title": "待办交付清单"})
    if r2.status_code == 200:
        url = r2.json()["download_url"]
        rd = client.get(url)
        results.append(_expect("GET download_url 200+docx+bytes>0",
                               200 <= rd.status_code < 300
                               and "wordprocessingml" in rd.headers.get(
                                   "content-type", "")
                               and len(rd.content) > 0,
                               f"got {rd.status_code}, "
                               f"{len(rd.content)} bytes"))
        # 路径穿越 / 非白名单文件名必须 404
        results.append(_check("GET /exports/..%2f..%2fetc%2fpasswd 穿越拦截",
                              client.get("/exports/..%2f..%2fetc%2fpasswd"),
                              404))
        results.append(_check("GET /exports/not_a_valid_name.docx 拦截",
                              client.get("/exports/not_a_valid_name.docx"),
                              404))
        results.append(_check("GET /exports/deskpet_export_zzzz.docx 拦截",
                              client.get("/exports/deskpet_export_zzzzzzzz.docx"),
                              404))
    else:
        results.append(_expect("GET download_url（导出失败跳过）", False,
                               f"export got {r2.status_code}"))

    # ==========================================================
    # 8. 三态开关 / 能力注册表 / Key 管理（阶段 3 新增本地接口）
    # ==========================================================
    r = client.get("/api/settings/model-switch")
    ok_switch_default = _check("GET /api/settings/model-switch", r, OK)
    results.append(ok_switch_default)
    r = client.post("/api/settings/model-switch", json={"state": "off"})
    results.append(_check("POST /api/settings/model-switch off", r, OK))
    r = client.get("/api/settings/model-switch")
    results.append(_expect(
        "GET /api/settings/model-switch 读回 off",
        r.status_code == 200 and r.json().get("state") == "off",
        f"got {r.status_code} {r.json() if r.status_code == 200 else ''}"))
    r = client.post("/api/settings/model-switch", json={"state": "auto"})
    results.append(_check("POST /api/settings/model-switch 恢复 auto", r, OK))
    r = client.post("/api/settings/model-switch", json={"state": "half"})
    results.append(_check("POST /api/settings/model-switch 非法值校验拦截",
                         r, VALIDATION))

    r = client.get("/api/capabilities")
    results.append(_expect(
        "GET /api/capabilities 注册表+开关+配额",
        r.status_code == 200 and len(r.json().get("capabilities", [])) >= 5,
        f"got {r.status_code}"))

    r = client.get("/api/key/status")
    results.append(_expect(
        "GET /api/key/status 无Key→configured=false",
        r.status_code == 200 and r.json().get("configured") is False,
        f"got {r.status_code}"))
    r = client.post("/api/key/check")
    results.append(_expect(
        "POST /api/key/check 无Key→not_configured（不发真实请求）",
        r.status_code == 200 and r.json().get("status") == "not_configured",
        f"got {r.status_code}"))

    # ==========================================================
    # 汇总
    # ==========================================================
    # 清理冒烟产生的全部导出物（注入目录独占，可整体清空，不堆积）
    for p in paths.get_exports_dir().glob("deskpet_export_*.docx"):
        try:
            p.unlink()
        except OSError:
            pass

    passed = sum(1 for ok, _ in results if ok)
    failed = sum(1 for ok, _ in results if not ok)
    print(f"\n{'='*60}")
    print(f"接口契约冒烟：{passed} PASS / {failed} FAIL / 共 {len(results)} 项")
    print(f"{'='*60}")
    for ok, line in results:
        print(line)
        if not ok:
            print(f"      !!! FAIL 见上方")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())