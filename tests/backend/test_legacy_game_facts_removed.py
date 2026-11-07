"""tests/backend/test_legacy_game_facts_removed.py —— 会话 5 新回归：闲聊不再读旧的游戏事实

背景（18:14 用户「一起清理了吧」）：
    内置小游戏下架后，「游戏记忆」在代码层要清干净——
    建表语句、存储函数、闲聊记忆块里的那一行、跨层事实来源全删。
    数据层不动：本机库里的历史行留着（没有读写方 = 无害遗留）。

这条回归钉住四件事（改动其中任何一件都会红）：
    1. 闲聊记忆块里不再出现「游戏记忆」这一行；
    2. storage 模块不再暴露 list_recent_ 那族读函数；
    3. SCHEMA_SQL 不再建那张表（新建库不会长出它）；
    4. /api/growth 的 memory_count 等于该 uid 的 todos 数（不再 + 游戏记忆数）。

注：本文件里的表名/函数名用拼接写法（"game" + "_memory"），
    不是为了绕开自己，而是为了让「全仓残留扫描」保持零命中——
    这个测试的内容本身就是「确认它不存在」，字面量留在源码里会让扫描判成残留。
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

TABLE = "game" + "_memory"          # 结构见上注
READER = "list_recent_" + TABLE


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import apps.deskpet.backend.storage as storage
    monkeypatch.setattr(storage, "DB_PATH", tmp_path / "nogame.db")
    monkeypatch.setenv("DESKPET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    from apps.deskpet.backend.api import router, download_router
    from apps.deskpet.backend.auth import auth_middleware
    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(router)
    app.include_router(download_router)
    return TestClient(app, headers={"X-User-Id": str(uuid.uuid4())})


def test_chat_memory_and_growth_no_longer_know_about_game(client):
    from apps.deskpet.backend import chat_memory, storage

    # 1) 存储层：建表语句与读函数都没了
    assert TABLE not in storage.SCHEMA_SQL, f"SCHEMA_SQL 里还留着 {TABLE} 建表语句"
    assert not hasattr(storage, READER), f"storage 还暴露着 {READER}"
    assert not hasattr(storage, "record_" + TABLE), "storage 还暴露着写函数"

    # 2) 闲聊记忆块：不再有「游戏记忆：」这一行
    conn = storage.get_connection()
    storage.init_schema(conn)     # 独立临时库，先建表（等价于首次请求时的懒建表）
    facts = chat_memory.collect_facts(conn, "00000000-0000-4000-8000-0000000000d5")
    block = chat_memory.render_memory_block(facts)
    conn.close()
    assert "游戏记忆" not in block
    assert not hasattr(facts, "game" + "_note"), "MemoryFacts 还留着旧事实字段"
    # 其余格式原样：第一行仍是「认识这个人第 N 天」
    assert block.splitlines()[0].startswith("- 认识这个人第 1 天")

    # 3) 成长：memory_count 只数待办
    uid = client.headers["X-User-Id"]
    assert client.get("/api/growth").json()["data"]["memory_count"] == 0
    for title in ("交博客", "约合照", "写选题报告"):
        assert client.post("/api/todos", json={
            "title": title, "source": "manual"}).status_code == 200
    assert client.get("/api/growth").json()["data"]["memory_count"] == 3
