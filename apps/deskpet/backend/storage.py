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

import sqlite3
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from apps.deskpet.backend import clock
from apps.deskpet.backend.paths import get_data_dir
from apps.deskpet.backend.models import (
    GrowthState, PersonaStage, TodoItem, TodoStatus,
)

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
    conn.executescript(SCHEMA_SQL)
    conn.commit()


# ===========================================================================
# Users
# ===========================================================================
def get_or_create_user(conn: sqlite3.Connection, user_id: str) -> tuple[datetime, bool]:
    """取用户；不存在则建（first_seen 用真实现在）。

    返回 (first_seen_datetime, is_new)。first_seen 永远用真实时间，
    绝不受模拟影响——否则认识天数基准就被写脏。
    """
    row = conn.execute("SELECT first_seen FROM users WHERE user_id=?",
                       (user_id,)).fetchone()
    if row:
        return clock.parse_iso(row["first_seen"]), False
    now = clock.real_now()
    conn.execute(
        "INSERT INTO users(user_id, first_seen, created_at) VALUES(?,?,?)",
        (user_id, clock.to_iso(now), clock.to_iso(now)))
    conn.commit()
    return now, True


def get_first_seen(conn: sqlite3.Connection, user_id: str) -> Optional[datetime]:
    row = conn.execute("SELECT first_seen FROM users WHERE user_id=?",
                       (user_id,)).fetchone()
    if row:
        return clock.parse_iso(row["first_seen"])
    return None


# ===========================================================================
# Todos
# ===========================================================================
def create_todo(conn: sqlite3.Connection, user_id: str, *,
                title: str, deadline: Optional[datetime], gap: str,
                source: str) -> TodoItem:
    get_or_create_user(conn, user_id)  # 确保用户存在（外键约束）
    tid = str(uuid.uuid4())
    now = clock.real_now()  # 写库用真实时间，绕开模拟
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
    conn.commit()
    # 返回重读后的行：deadline 已走 to_iso 补时区，响应与库里口径一致
    # （naive hint 落库补 +08:00 后，confirm 响应不再裸奔 naive 值）
    return get_todo(conn, user_id, tid)


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
    existing = get_todo(conn, user_id, todo_id)
    if not existing:
        return None
    now = clock.real_now()
    sets: list[str] = []
    vals: list[Any] = []
    for k, v in fields.items():
        if v is None:
            continue
        if k == "status":
            sets.append("status=?")
            vals.append(v.value if isinstance(v, TodoStatus) else v)
            if v == TodoStatus.DONE and existing.status != TodoStatus.DONE:
                sets.append("done_at=?")
                vals.append(clock.to_iso(now))
                _bump_intimacy(conn, user_id)  # 按时完成 +1
            elif v == TodoStatus.PENDING:
                sets.append("done_at=?")
                vals.append(None)
        elif k == "deadline":
            sets.append("deadline=?")
            vals.append(clock.to_iso(v) if v else None)
        elif k in ("title", "gap"):
            sets.append(f"{k}=?")
            vals.append(v)
    if not sets:
        return existing
    sets.append("updated_at=?")
    vals.append(clock.to_iso(now))
    vals.extend([user_id, todo_id])
    conn.execute(
        f"UPDATE todos SET {','.join(sets)} WHERE user_id=? AND id=?", vals)
    conn.commit()
    return get_todo(conn, user_id, todo_id)


def delete_todo(conn: sqlite3.Connection, user_id: str, todo_id: str) -> bool:
    cur = conn.execute(
        "DELETE FROM todos WHERE user_id=? AND id=?", (user_id, todo_id))
    conn.commit()
    return cur.rowcount > 0


def _row_to_todo(row: sqlite3.Row) -> TodoItem:
    return TodoItem(
        id=row["id"], user_id=row["user_id"], title=row["title"],
        deadline=clock.parse_iso(row["deadline"]) if row["deadline"] else None,
        gap=row["gap"] or "",
        status=TodoStatus(row["status"]),
        source=row["source"],
        created_at=clock.parse_iso(row["created_at"]),
        updated_at=clock.parse_iso(row["updated_at"]),
        done_at=clock.parse_iso(row["done_at"]) if row["done_at"] else None)


def _bump_intimacy(conn: sqlite3.Connection, user_id: str) -> None:
    """按时完成待办 → 亲密度 +1。漏了不扣（brief 第六节不惩罚）。"""
    row = conn.execute("SELECT * FROM growth WHERE user_id=?", (user_id,)).fetchone()
    now = clock.real_now()
    if row:
        conn.execute(
            "UPDATE growth SET intimacy=intimacy+1, mood=100, updated_at=? "
            "WHERE user_id=?",
            (clock.to_iso(now), user_id))
    else:
        conn.execute(
            "INSERT INTO growth(user_id,intimacy,mood,updated_at) VALUES(?,?,?,?)",
            (user_id, 1, 100, clock.to_iso(now)))
    conn.commit()


# ===========================================================================
# Growth
# ===========================================================================
def get_growth(conn: sqlite3.Connection, user_id: str) -> GrowthState:
    """读成长状态。

    days_known / memory_count / stage 不存表，实时算（避免冗余不一致）：
      days_known = now() 与 first_seen 的日期差（模拟时 now() 返回模拟值）
      memory_count = todos 数（会话 5 下架内置小游戏后只剩这一项）
      stage = days_known 映射
    """
    first_seen, _ = get_or_create_user(conn, user_id)
    cur_now = clock.now(user_id)  # 传 user_id：只读该用户自己的模拟态（P0-3）
    days = clock.days_between(first_seen, cur_now)
    stage = clock.stage_for_days(days)

    todo_count = conn.execute(
        "SELECT COUNT(*) FROM todos WHERE user_id=?", (user_id,)).fetchone()[0]

    row = conn.execute(
        "SELECT * FROM growth WHERE user_id=?", (user_id,)).fetchone()
    if row:
        intimacy = row["intimacy"]
        mood = row["mood"]
    else:
        intimacy = 0
        mood = 100

    return GrowthState(
        user_id=user_id,
        first_seen=first_seen,
        days_known=days,
        memory_count=todo_count,
        intimacy=intimacy,
        mood=mood,
        stage=stage,
        simulated_now=cur_now if clock.is_simulating(user_id) else None)


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