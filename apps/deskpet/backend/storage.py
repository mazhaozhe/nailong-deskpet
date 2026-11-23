"""apps.deskpet.backend.storage —— SQLite 存储层（落地实现）

为什么 SQLite（理由是并发）：
    JSON 并发写会丢数据，要自己造文件锁轮子。
    SQLite WAL 模式：读不阻塞写、写不阻塞读，多请求并发不互锁。
    busy_timeout：遇锁自动等。单文件部署零运维。

隔离口径：
    所有业务表带 user_id 列，按用户隔离。
    user_id 来源 = 前端 localStorage UUID（见 auth.py），不是访问口令
    （v6.1 单机 exe 口径：无公网暴露面，UUID 只管「你是谁」，
    每人一份本地库；访问口令/团队 Key 口径已作废）。
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from apps.deskpet.backend import clock, intimacy
from apps.deskpet.backend.paths import get_data_dir
from apps.deskpet.backend.models import (
    GrowthState, TodoItem, TodoStatus,
)

from apps.deskpet.backend.intimacy import ChatRequestConflict

# 业务库跟导出物 / 缓存 / 设置走同一个用户数据目录（阶段 7 打包收口）：
# 原来写死 Path("data/deskpet.db") 是相对 cwd 的，装到 C:\Program Files 这类
# 只读安装目录时 cwd 写不进，新建待办当场失败。Electron 启动后端时注入
# DESKPET_DATA_DIR = userData/deskpet，这里直接复用同一入口，不再各写一份路径。
DB_PATH = get_data_dir() / "deskpet.db"
BUSY_TIMEOUT_MS = 5000


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id     TEXT PRIMARY KEY,
    first_seen  TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS todos (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(user_id),
    title       TEXT NOT NULL,
    deadline    TEXT,
    gap         TEXT DEFAULT '',
    status      TEXT NOT NULL DEFAULT 'pending',
    source      TEXT NOT NULL DEFAULT 'manual',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    done_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_todos_user_deadline ON todos(user_id, deadline);
CREATE INDEX IF NOT EXISTS idx_todos_user_status   ON todos(user_id, status);

CREATE TABLE IF NOT EXISTS notice_confirmations (
    user_id         TEXT NOT NULL REFERENCES users(user_id),
    confirmation_id TEXT NOT NULL,
    todo_ids         TEXT NOT NULL DEFAULT '[]',
    PRIMARY KEY (user_id, confirmation_id)
);

CREATE TABLE IF NOT EXISTS growth (
    user_id       TEXT PRIMARY KEY REFERENCES users(user_id),
    intimacy      INTEGER NOT NULL DEFAULT 0,
    mood          INTEGER NOT NULL DEFAULT 100,
    updated_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    granted     INTEGER NOT NULL,
    user_id     TEXT,
    ip_hint     TEXT DEFAULT '',
    at          TEXT NOT NULL
);
"""


# ===========================================================================
# 连接
# ===========================================================================
def get_connection(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False：FastAPI 异步框架在不同线程调度请求，
    # 每请求独立连接 + WAL 模式下安全（WAL 允许并发读写）。
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_MS / 1000,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    if intimacy.schema_is_current(conn):
        return
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if intimacy.schema_is_current(conn):
            return
        legacy = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='users'"
        ).fetchone()
        if legacy:
            intimacy.backup_legacy_db(conn)
        for statement in SCHEMA_SQL.strip().rstrip(";").split(";"):
            conn.execute(statement)
        intimacy.migrate(conn, clock.real_now())


# ===========================================================================
# Users
# ===========================================================================
def get_or_create_user(conn: sqlite3.Connection, user_id: str) -> tuple[datetime, bool]:
    """现有用户沿用 first_seen，新用户优先使用本机安装记录。"""
    existing = get_first_seen(conn, user_id)
    if existing is not None:
        return existing, False
    now = clock.real_now()
    first_seen = _installed_at(user_id, now) or now
    inserted = conn.execute(
        "INSERT INTO users(user_id, first_seen, created_at) VALUES(?,?,?) "
        "ON CONFLICT(user_id) DO NOTHING",
        (user_id, clock.to_iso(first_seen), clock.to_iso(now))).rowcount == 1
    conn.commit()
    if inserted:
        return first_seen, True
    first_seen = get_first_seen(conn, user_id)
    assert first_seen is not None
    return first_seen, False


def get_first_seen(conn: sqlite3.Connection, user_id: str) -> Optional[datetime]:
    row = conn.execute("SELECT first_seen FROM users WHERE user_id=?",
                       (user_id,)).fetchone()
    if row:
        return intimacy.aware(row["first_seen"])
    return None


def _installed_at(user_id: str, now: datetime) -> datetime | None:
    if os.environ.get("DESKPET_LOCAL_UID") != user_id:
        return None
    try:
        stamp = clock.parse_iso(os.environ.get("DESKPET_INSTALLED_AT", ""))
    except ValueError:
        return None
    if stamp.tzinfo is None or stamp > now:
        return None
    return stamp


# ===========================================================================
# Todos
# ===========================================================================
def create_todo(conn: sqlite3.Connection, user_id: str, *,
                title: str, deadline: Optional[datetime], gap: str,
                source: str) -> TodoItem:
    get_or_create_user(conn, user_id)  # 确保用户存在（外键约束）
    with conn:
        return _insert_todo(conn, user_id, title=title, deadline=deadline, gap=gap, source=source)


def create_notice_todos(conn: sqlite3.Connection, user_id: str, *,
                        items: list[tuple[str, datetime | None]],
                        confirmation_id: str | None) -> list[TodoItem]:
    if not items and confirmation_id is None:
        return []
    get_or_create_user(conn, user_id)
    with conn:
        if confirmation_id is not None:
            # 与待办写入共用一个事务。并发请求只能有一个成功登记此编号。
            key = (user_id, confirmation_id)
            claimed = conn.execute(
                "INSERT INTO notice_confirmations(user_id, confirmation_id) VALUES(?,?) "
                "ON CONFLICT(user_id, confirmation_id) DO NOTHING", key).rowcount == 1
            if not claimed:
                receipt = conn.execute(
                    "SELECT todo_ids FROM notice_confirmations WHERE user_id=? AND confirmation_id=?",
                    key).fetchone()
                existing = []
                for todo_id in json.loads(receipt["todo_ids"]):
                    todo = get_todo(conn, user_id, todo_id)
                    if todo is not None:
                        existing.append(todo)
                return existing

        created = [
            _insert_todo(conn, user_id, title=title, deadline=deadline, gap=title, source="notice")
            for title, deadline in items
        ]
        if confirmation_id is not None:
            conn.execute(
                "UPDATE notice_confirmations SET todo_ids=? WHERE user_id=? AND confirmation_id=?",
                (json.dumps([todo.id for todo in created]), user_id, confirmation_id))
        return created


def _insert_todo(conn: sqlite3.Connection, user_id: str, *,
                 title: str, deadline: datetime | None, gap: str, source: str) -> TodoItem:
    tid = str(uuid.uuid4())
    now = clock.real_now()
    item = TodoItem(
        id=tid, user_id=user_id, title=title,
        deadline=deadline, gap=gap,
        status=TodoStatus.PENDING, source=source,
        created_at=now, updated_at=now)
    conn.execute(
        "INSERT INTO todos(id,user_id,title,deadline,gap,status,source,"
        "created_at,updated_at,done_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (item.id, item.user_id, item.title,
         clock.to_iso(deadline) if deadline else None, item.gap,
         item.status.value, item.source,
         clock.to_iso(item.created_at), clock.to_iso(item.updated_at), None))
    # 返回重读后的行：deadline 已走 to_iso 补时区，响应与库里口径一致
    # （naive hint 落库补 +08:00 后，confirm 响应不再裸奔 naive 值）
    stored = get_todo(conn, user_id, tid)
    assert stored is not None
    return stored


def list_todos(conn: sqlite3.Connection, user_id: str) -> list[TodoItem]:
    rows = conn.execute(
        "SELECT * FROM todos WHERE user_id=? ORDER BY created_at", (user_id,))
    return [_row_to_todo(r) for r in rows]


def get_todo(conn: sqlite3.Connection, user_id: str,
             todo_id: str) -> Optional[TodoItem]:
    row = conn.execute(
        "SELECT * FROM todos WHERE user_id=? AND id=?", (user_id, todo_id)
    ).fetchone()
    return _row_to_todo(row) if row else None


def update_todo(conn: sqlite3.Connection, user_id: str, todo_id: str,
                **fields) -> Optional[TodoItem]:
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        existing = get_todo(conn, user_id, todo_id)
        if not existing:
            return None
        now = clock.real_now()
        intimacy.ensure_state(conn, user_id, now)
        intimacy.settle_overdue(conn, user_id, now)
        values: dict[str, Any] = {}
        for field in ("title", "gap"):
            if fields.get(field) is not None:
                values[field] = fields[field]
        if "deadline" in fields:
            deadline = fields["deadline"]
            values["deadline"] = clock.to_iso(deadline) if deadline is not None else None
        new_status = fields.get("status")
        completed = new_status == TodoStatus.DONE and existing.status != TodoStatus.DONE
        if new_status is not None:
            values["status"] = TodoStatus(new_status).value
            if completed:
                values["done_at"] = clock.to_iso(now)
            elif new_status == TodoStatus.PENDING:
                if existing.status == TodoStatus.DONE or existing.done_at is not None:
                    # A downgraded client can leave a completion without a new ledger receipt.
                    intimacy.apply_event(conn, user_id, f"todo_done:{todo_id}", "legacy_complete",
                                         "todo_base", 0, existing.done_at or now, now)
                values["done_at"] = None
        if not values:
            return existing
        values["updated_at"] = clock.to_iso(now)
        assignments = ", ".join(f"{field}=?" for field in values)
        conn.execute(
            f"UPDATE todos SET {assignments} WHERE user_id=? AND id=?",
            (*values.values(), user_id, todo_id),
        )
        if completed:
            intimacy.complete_todo(conn, user_id, todo_id, existing.deadline, now)
        return get_todo(conn, user_id, todo_id)


def delete_todo(conn: sqlite3.Connection, user_id: str, todo_id: str) -> bool:
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if get_todo(conn, user_id, todo_id) is None:
            return False
        now = clock.real_now()
        intimacy.ensure_state(conn, user_id, now)
        intimacy.settle_overdue(conn, user_id, now)
        conn.execute("DELETE FROM todos WHERE user_id=? AND id=?", (user_id, todo_id))
        return True


def _row_to_todo(row: sqlite3.Row) -> TodoItem:
    return TodoItem(
        id=row["id"], user_id=row["user_id"], title=row["title"],
        deadline=intimacy.aware(row["deadline"]) if row["deadline"] else None,
        gap=row["gap"] or "",
        status=TodoStatus(row["status"]),
        source=row["source"],
        created_at=intimacy.aware(row["created_at"]),
        updated_at=intimacy.aware(row["updated_at"]),
        done_at=intimacy.aware(row["done_at"]) if row["done_at"] else None)


# ===========================================================================
# Growth
# ===========================================================================
def get_growth(conn: sqlite3.Connection, user_id: str) -> GrowthState:
    """读取当前账本。首次访问时初始化本机身份对应的成长状态。"""
    first_seen, _ = get_or_create_user(conn, user_id)
    with conn:
        initialized = intimacy.state_for(conn, user_id) is not None
        conn.execute("BEGIN" if initialized else "BEGIN IMMEDIATE")
        now = clock.real_now()
        if not initialized:
            intimacy.ensure_state(conn, user_id, now)
        return _growth_snapshot(conn, user_id, first_seen, now)


def _growth_snapshot(conn: sqlite3.Connection, user_id: str, first_seen: datetime,
                     now: datetime) -> GrowthState:
    days = clock.known_days(first_seen, now)
    todo_count = conn.execute("SELECT COUNT(*) FROM todos WHERE user_id=?", (user_id,)).fetchone()[0]
    legacy = conn.execute("SELECT mood FROM growth WHERE user_id=?", (user_id,)).fetchone()
    return GrowthState(
        user_id=user_id,
        first_seen=first_seen,
        days_known=days,
        memory_count=todo_count,
        mood=legacy["mood"] if legacy else 100,
        stage=clock.stage_for_days(days),
        **intimacy.snapshot(conn, user_id, now),
    )


def sync_growth(conn: sqlite3.Connection, user_id: str, *, panel_open: bool = False) -> GrowthState:
    first_seen, _ = get_or_create_user(conn, user_id)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        now = clock.real_now()
        intimacy.ensure_state(conn, user_id, now)
        intimacy.settle_overdue(conn, user_id, now)
        if panel_open:
            score = intimacy.state_for(conn, user_id)["score"]
            intimacy.enqueue_action(conn, user_id, "welcome", now, visible=score >= 40 * intimacy.UNIT)
        return _growth_snapshot(conn, user_id, first_seen, now)


def chat_message_digest(conn: sqlite3.Connection, user_id: str, message: str) -> str:
    return intimacy.chat_digest(conn, user_id, message)


def validate_chat_request(conn: sqlite3.Connection, user_id: str, message: str,
                          request_id: str | None) -> None:
    if request_id:
        digest = chat_message_digest(conn, user_id, message)
        intimacy.seen_chat_request(conn, user_id, request_id, digest)


def record_chat_reply(conn: sqlite3.Connection, user_id: str, message: str | None,
                      request_id: str | None, *, digest: str | None = None) -> GrowthState:
    first_seen, _ = get_or_create_user(conn, user_id)
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        now = clock.real_now()
        intimacy.ensure_state(conn, user_id, now)
        intimacy.settle_overdue(conn, user_id, now)
        if digest is None and message and message.strip():
            digest = chat_message_digest(conn, user_id, message)
        if digest is not None:
            intimacy.record_chat(conn, user_id, now, digest, request_id)
        return _growth_snapshot(conn, user_id, first_seen, now)


def claim_growth_action(conn: sqlite3.Connection, user_id: str, event_id: str) -> bool:
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if intimacy.state_for(conn, user_id) is None:
            return False
        return intimacy.claim_action(conn, user_id, event_id, clock.real_now())


# ===========================================================================
# Access log
# ===========================================================================
def log_access(conn: sqlite3.Connection, *, granted: bool,
               user_id: str = "", ip_hint: str = "") -> None:
    conn.execute(
        "INSERT INTO access_log(granted,user_id,ip_hint,at) VALUES(?,?,?,?)",
        (1 if granted else 0, user_id or None, ip_hint,
         clock.to_iso(clock.real_now())))
    conn.commit()
