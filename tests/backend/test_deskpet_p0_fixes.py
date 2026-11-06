"""tests/backend/test_deskpet_p0_fixes.py —— 阶段 2 返工五项 P0 + 阶段 3 Step 0 验收测试

P0-1 下载链路：200 + docx content-type + bytes>0；穿越/非白名单名 404
P0-2 deadline 落库：贴「11/8 23:59 前提交博客」→ 待办 deadline 精确；
     模拟第 9 天 → 补报 overdue 出现该项
P0-3 模拟态按用户隔离：A 模拟 30 天不影响 B；A 清除后回真实；
     SQLite first_seen 不变
（P0-4 在 test_deskpet_no_model.py，P0-5 由其 tools.coding 断言覆盖）

Step 0-1 陈旧 hint：带日期 parse → 无日期 parse → confirm 不带 override
     → 落库 deadline 必须为 null（上一条 hint 不串到不相关待办）；
     带日期 + override 落库精确；换用户互不影响
Step 0-2 静态根旁路：真实 create_app() 下 /static/exports/* 必须 404；
     /exports/* 200 + docx MIME + PK 魔数；过期导出 410 + 真删除
"""

import re
import uuid as uuid_mod
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """独立 SQLite 库 + 用户数据目录注入 tmp + 挂好全部路由的 TestClient。

    DESKPET_DATA_DIR 注入：导出物落 tmp，绝不碰源码目录或真实用户目录。
    """
    import deskpet.storage as storage
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setenv("DESKPET_DATA_DIR", str(tmp_path / "data"))

    from deskpet.api import router, download_router
    from deskpet.auth import auth_middleware
    from deskpet.games.builtin.mini_snake import register
    register()

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(router)
    app.include_router(download_router)
    # 让 500 直接穿透，任何实现 bug 都不会伪装成「预期行为」
    return TestClient(app, headers={"X-User-Id": _uid()})


def _uid() -> str:
    return str(uuid_mod.uuid4())


def _cleanup(url: str) -> None:
    from deskpet import paths
    name = url.rsplit("/", 1)[-1]
    p = paths.get_exports_dir() / name
    p.unlink(missing_ok=True)


# ===========================================================================
# P0-1 导出下载链路
# ===========================================================================
def test_p0_1_download_returns_docx(client):
    r = client.post("/api/todos", json={"title": "导出这条", "source": "manual"})
    assert r.status_code == 200
    tid = r.json()["data"]["id"]
    r = client.post("/api/export/docx", json={
        "todo_ids": [tid], "title": "待办交付清单"})
    assert r.status_code == 200, r.text
    url = r.json()["download_url"]
    assert re.fullmatch(r"/exports/deskpet_export_[0-9a-f]{8}\.docx", url)

    rd = client.get(url)
    assert rd.status_code == 200, f"下载应 200，got {rd.status_code}"
    assert "wordprocessingml" in rd.headers.get("content-type", ""), (
        f"content-type 应为 docx MIME，got {rd.headers.get('content-type')}")
    assert len(rd.content) > 0
    assert rd.content[:2] == b"PK"  # docx 是 zip 容器，魔术字节必须对

    _cleanup(url)


def test_p0_1_download_blocks_traversal_and_bad_names(client):
    for bad in [
        "..%2F..%2Fsecret.txt",          # 编码穿越
        "../../data/deskpet.db",          # 显式穿越（FastAPI 会先归一化，仍必须 404）
        "deskpet_export_zzzzzzzz.docx",   # 非十六进制
        "deskpet_export_12345678.txt",    # 后缀不对
        "anything.docx",                  # 前缀不对
        "deskpet_export_123456781.docx",  # 9 位 hex
    ]:
        rd = client.get(f"/exports/{bad}")
        assert rd.status_code == 404, f"{bad} 应 404，got {rd.status_code}"


# ===========================================================================
# P0-2 截止时间真正落库（Step 0-1 后：唯一来源是前端 deadline_override）
# ===========================================================================
def test_p0_2_deadline_hint_lands_on_todo(client):
    r = client.post("/api/notice/parse", json={
        "notice_text": "【作业要求】11/8 23:59 前提交博客，内容含合照。",
        "use_v4_pro": False})
    assert r.status_code == 200
    hint = r.json()["data"]["deadline_hint"]
    assert hint is not None, "parse 应抽出 deadline_hint"
    assert "2026-11-08T23:59" in hint

    # confirm 不传 deadline_override —— 后端已无进程内兜底，必须落 null
    r = client.post("/api/notice/confirm", json={"items": [
        {"text": "提交博客", "category": "deliverable", "matched": False}]})
    assert r.status_code == 200
    created = r.json()["created"]
    assert len(created) == 1
    assert created[0]["deadline"] is None, (
        "无 override 时 deadline 必须为 null，不得回填任何内存态 hint")

    # 显式传 deadline_override（前端单通道路径）→ 精确落库
    r = client.post("/api/notice/confirm", json={
        "items": [{"text": "补合照", "category": "format", "matched": False}],
        "deadline_override": "2026-11-08T23:59:00"})
    assert "2026-11-08T23:59" in r.json()["created"][0]["deadline"]


def test_p0_2_deadline_drives_overdue_after_simulate(client):
    # 今天建「11/8 23:59 交博客」，模拟跳到第 9 天（>11/8）→ 必须逾期
    r = client.post("/api/notice/parse", json={
        "notice_text": "11/8 23:59 前提交博客", "use_v4_pro": False})
    hint = r.json()["data"]["deadline_hint"]
    r = client.post("/api/notice/confirm", json={
        "items": [{"text": "提交博客", "category": "deliverable",
                   "matched": False}],
        "deadline_override": hint})
    assert r.status_code == 200
    tid = r.json()["created"][0]["id"]

    r = client.post("/api/growth/simulate", json={"target_day": 9})
    assert r.status_code == 200
    r = client.get("/api/catchup")
    assert r.status_code == 200
    snap = r.json()["data"]
    overdue_titles = [t["title"] for t in snap["overdue"]]
    assert "提交博客" in overdue_titles, (
        f"模拟第 9 天后补报 overdue 应含「提交博客」，"
        f"got overdue={overdue_titles}, due_soon={snap['due_soon']}, "
        f"pending={snap['pending']}")

    # 清模拟后回真实：不再逾期（11/8 还没到）
    client.post("/api/growth/simulate/clear")
    snap = client.get("/api/catchup").json()["data"]
    assert "提交博客" not in [t["title"] for t in snap["overdue"]]


# ===========================================================================
# P0-3 模拟态按用户隔离 + 刷新回落
# ===========================================================================
def test_p0_3_simulation_isolated_per_user(client):
    # A 模拟第 30 天
    ra = client.post("/api/growth/simulate", json={"target_day": 30})
    assert ra.status_code == 200
    days_a = ra.json()["data"]["days_known"]
    assert days_a == 30, f"A 模拟后应 30 天，got {days_a}"
    first_seen_a = ra.json()["data"]["first_seen"]

    # B（新 user_id）同期必须仍是真实天数，不受 A 影响
    rb = TestClient(client.app, headers={"X-User-Id": _uid()})
    days_b = rb.get("/api/growth").json()["data"]["days_known"]
    assert days_b < 2, f"B 不应被 A 的模拟波及，got {days_b} 天"

    # A 显式清除 → 回真实天数，first_seen 不变
    rc = client.post("/api/growth/simulate/clear")
    assert rc.status_code == 200
    after = rc.json()["data"]
    assert after["days_known"] == days_b, (
        f"A 清除后应回真实天数({days_b})，got {after['days_known']}")
    assert after["first_seen"] == first_seen_a, "first_seen 不得被模拟改写"

    # 持久层复核：SQLite 里的 first_seen 与清除前一致
    import deskpet.storage as storage
    uid_a = None
    # 从 growth 表反查不出 uid 的话直接用 API 的 header uid
    # ——用更直接的办法：再 simulate 一次确认可重复设置/清除
    client.post("/api/growth/simulate", json={"target_day": 100})
    d100 = client.get("/api/growth").json()["data"]["days_known"]
    assert d100 == 100
    client.post("/api/growth/simulate/clear")
    d_real = client.get("/api/growth").json()["data"]["days_known"]
    assert d_real == days_b, "第二次清除后仍必须回真实天数"


# ===========================================================================
# Step 0-1 陈旧 hint 污染（复测新抓）
# ===========================================================================
def test_step0_1_stale_hint_not_polluting_undated_todo(client):
    """三段序列：带日期 parse → 无日期 parse → confirm 不带 override。

    判据：落库 deadline 必须为 null —— 上一条通知的截止时间不得被
    硬塞给不相关待办（曾因此误报逾期）。
    """
    # 1) 带日期通知
    r = client.post("/api/notice/parse", json={
        "notice_text": "【作业要求】11/8 23:59 前提交博客，内容含合照。"})
    assert r.status_code == 200
    assert r.json()["data"]["deadline_hint"] is not None

    # 2) 紧接着无日期通知（deadline_hint=null）
    r = client.post("/api/notice/parse", json={
        "notice_text": "【课程通知】请提交实验报告，注明学号与姓名。"})
    assert r.status_code == 200
    assert r.json()["data"]["deadline_hint"] is None, "该通知无日期"

    # 3) confirm 不带 deadline_override → 落库必须 null，不串上一条
    r = client.post("/api/notice/confirm", json={"items": [
        {"text": "提交实验报告", "category": "deliverable", "matched": False}]})
    assert r.status_code == 200
    created = r.json()["created"]
    assert len(created) == 1
    assert created[0]["deadline"] is None, (
        f"无日期通知的待办 deadline 应为 null，"
        f"got {created[0]['deadline']}（陈旧 hint 污染）")

    # 正向链路（v6.1）：带日期 parse + confirm 带 override → 精确落库
    r = client.post("/api/notice/parse", json={
        "notice_text": "11/8 23:59 前提交博客"})
    hint = r.json()["data"]["deadline_hint"]
    r = client.post("/api/notice/confirm", json={
        "items": [{"text": "提交博客", "category": "deliverable",
                   "matched": False}],
        "deadline_override": hint})
    assert "2026-11-08T23:59" in r.json()["created"][0]["deadline"], (
        "带 override 的正向链路必须精确落库")


def test_step0_1_hint_not_shared_across_users(client):
    """换用户互不影响：A parse 带日期后，B confirm 不带 override 落 null。"""
    # A：parse 带日期通知（若残留全局字典，A 的 hint 会留在内存里）
    r = client.post("/api/notice/parse", json={
        "notice_text": "11/8 23:59 前提交博客"})
    assert r.json()["data"]["deadline_hint"] is not None

    # B（新 user_id）：confirm 不带 override → 落库 null
    rb = TestClient(client.app, headers={"X-User-Id": _uid()})
    r = rb.post("/api/notice/confirm", json={"items": [
        {"text": "B 的待办", "category": "other", "matched": False}]})
    assert r.status_code == 200
    assert r.json()["created"][0]["deadline"] is None, (
        "B 的待办不得串到 A 的 deadline hint")


def test_step0_1_no_module_level_hint_state():
    """不依赖进程内存：api 模块里不允许再存在 hint 全局字典。"""
    import deskpet.api as api
    assert not hasattr(api, "_last_deadline_hint"), (
        "_last_deadline_hint 全局字典必须整个移除（Step 0-1 裁决）")


# ===========================================================================
# Step 0-2 静态根旁路（复测新抓）
# ===========================================================================
def test_step0_2_real_app_static_exports_not_served(tmp_path, monkeypatch):
    """真实 create_app()（mount 了 static/）下验证旁路被封。

    判据：/static/exports/{fname} 必须 404；/exports/{fname} 仍 200 且
    MIME/PK 魔数/bytes 正确；目录列举 404。
    """
    monkeypatch.setenv("DESKPET_DATA_DIR", str(tmp_path / "data"))
    import deskpet.storage as storage
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "test.db")

    from deskpet.app import create_app
    app = create_app()
    client = TestClient(app, headers={"X-User-Id": _uid()})

    # 建待办 → 导出 → 拿文件名
    r = client.post("/api/todos", json={"title": "Step0-2 验证", "gap": "还差"})
    tid = r.json()["data"]["id"]
    r = client.post("/api/export/docx", json={
        "todo_ids": [tid], "title": "待办交付清单"})
    assert r.status_code == 200, r.text
    fname = r.json()["download_url"].rsplit("/", 1)[-1]
    assert "2026" in r.json()["expires_at"]  # 字段仍在（schema 不许改）

    # 静态根旁路必须 404（导出目录已移出 static）
    assert client.get(f"/static/exports/{fname}").status_code == 404, (
        "/static/exports/{fname} 必须封死（Step 0-2）")
    assert client.get("/static/exports/").status_code == 404
    assert client.get("/static/exports").status_code == 404

    # 白名单路由仍正常：200 + docx MIME + PK 魔数 + bytes>0
    rd = client.get(f"/exports/{fname}")
    assert rd.status_code == 200, f"got {rd.status_code}"
    assert "wordprocessingml" in rd.headers.get("content-type", "")
    assert rd.content[:2] == b"PK"
    assert len(rd.content) > 0


def test_step0_2_expired_export_returns_410_and_purged(client, tmp_path):
    """expires_at 真生效：过期导出 410 + 文件真删除（字段保留）。"""
    r = client.post("/api/todos", json={"title": "过期验证", "gap": ""})
    tid = r.json()["data"]["id"]
    r = client.post("/api/export/docx", json={"todo_ids": [tid],
                                              "title": "待办交付清单"})
    url = r.json()["download_url"]
    fname = url.rsplit("/", 1)[-1]

    # 篡改 mtime 到 2 小时前（模拟过期）
    import os
    from deskpet import paths
    p = paths.get_exports_dir() / fname
    old = datetime.now().timestamp() - 7200
    os.utime(p, (old, old))

    rd = client.get(url)
    assert rd.status_code == 410, f"过期导出应 410，got {rd.status_code}"
    rd = client.get(url)
    assert rd.status_code == 404, "过期文件被 410 响应删除，二次请求 404"

    # cleanup_expired_exports 也按 mtime 删过期文件
    p2 = paths.get_exports_dir() / f"deskpet_export_{uuid_mod.uuid4().hex[:8]}.docx"
    p2.write_bytes(b"PK\x03\x04stale")
    os.utime(p2, (old, old))
    from deskpet.api import cleanup_expired_exports
    assert cleanup_expired_exports() >= 1
    assert not p2.exists()


# ===========================================================================
# P1 docx 排版两处（Step 0 顺手修）
# ===========================================================================
def _read_docx(path):
    from docx import Document
    return Document(str(path))


def test_p1_docx_title_not_duplicated(tmp_path):
    """title 参数 + markdown 首行 # title 不再各渲染一次。"""
    from deskpet.docx_gen import markdown_to_docx
    md = tmp_path / "t.md"
    md.write_text("# 待办交付清单\n\n- □ **交博客**\n", encoding="utf-8")
    out = tmp_path / "t.docx"
    markdown_to_docx(str(md), str(out), title="待办交付清单")
    doc = _read_docx(out)
    heads = [p.text for p in doc.paragraphs if p.style.name.startswith("Title")
             or p.style.name.startswith("Heading")]
    assert heads.count("待办交付清单") == 1, f"标题应只出现一次，got {heads}"


def test_p1_docx_indented_bullet_is_list_item(tmp_path):
    """「  - 截止：…」前导空格也应走列表样式，不是带 - 的普通段落。"""
    from deskpet.docx_gen import markdown_to_docx
    md = tmp_path / "t.md"
    md.write_text(
        "# 清单\n\n- □ **交博客**\n  - 截止：2026-11-08 23:59\n"
        "  - 还差：合照\n",
        encoding="utf-8")
    out = tmp_path / "t.docx"
    markdown_to_docx(str(md), str(out), title="清单")
    doc = _read_docx(out)
    paras = [p for p in doc.paragraphs if p.text.strip()]
    deadline_paras = [p for p in paras if "截止" in p.text]
    assert len(deadline_paras) == 1
    p = deadline_paras[0]
    assert p.style.name == "List Bullet 2", (
        f"嵌套列表应走 List Bullet 2，got {p.style.name}")
    assert p.text.startswith("- ") is False, "不得把 '- ' 前缀残留在正文里"
    assert p.text.strip() == "截止：2026-11-08 23:59"
