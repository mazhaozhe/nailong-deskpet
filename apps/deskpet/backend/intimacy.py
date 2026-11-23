"""亲密度账本。写入由 storage 的 SQLite 事务统一提交。"""

from __future__ import annotations

import hmac
import secrets
import sqlite3
import unicodedata
import uuid
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

from apps.deskpet.backend import clock


UNIT = 10_000
BASELINE = 75 * UNIT
MAX_SCORE = 100 * UNIT
TIERS = (40, 60, 75, 85, 95, 100)
ACTION_TIERS = {"welcome": 40, "list_clear": 85, "anniversary": 100}
CHAT_AWARDS = (UNIT, 6 * UNIT // 10, 4 * UNIT // 10)
DAILY_LIMITS = {"chat_base": 2 * UNIT, "todo_base": 3 * UNIT, "penalty_base": UNIT}
SHANGHAI = timezone(timedelta(hours=8))
SCHEMA_VERSION = 1

SCHEMA_SQL = """
CREATE TABLE intimacy_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL,
    digest_key BLOB NOT NULL
);
CREATE TABLE intimacy_state (
    user_id TEXT PRIMARY KEY REFERENCES users(user_id),
    score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 1000000),
    cutover_at TEXT NOT NULL,
    revision INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE intimacy_daily (
    user_id TEXT NOT NULL REFERENCES users(user_id),
    date TEXT NOT NULL,
    chat_base INTEGER NOT NULL DEFAULT 0,
    todo_base INTEGER NOT NULL DEFAULT 0,
    penalty_base INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, date)
);
CREATE TABLE intimacy_events (
    user_id TEXT NOT NULL REFERENCES users(user_id),
    event_key TEXT NOT NULL,
    kind TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    settled_date TEXT NOT NULL,
    base INTEGER NOT NULL,
    delta INTEGER NOT NULL,
    PRIMARY KEY (user_id, event_key)
);
CREATE INDEX intimacy_events_daily ON intimacy_events(user_id, settled_date);
CREATE TABLE chat_receipts (
    user_id TEXT NOT NULL REFERENCES users(user_id),
    request_id TEXT NOT NULL,
    digest TEXT NOT NULL,
    PRIMARY KEY (user_id, request_id)
);
CREATE TABLE growth_unlocks (
    user_id TEXT NOT NULL REFERENCES users(user_id),
    tier INTEGER NOT NULL,
    unlocked_at TEXT NOT NULL,
    days_known INTEGER,
    PRIMARY KEY (user_id, tier)
);
CREATE TABLE growth_action_events (
    id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL REFERENCES users(user_id),
    kind TEXT NOT NULL,
    date TEXT NOT NULL,
    visible INTEGER NOT NULL DEFAULT 1,
    claimed_at TEXT,
    also_list_clear INTEGER NOT NULL DEFAULT 0,
    UNIQUE (user_id, kind, date)
);
CREATE INDEX growth_action_events_daily ON growth_action_events(user_id, date);
"""


class ChatRequestConflict(ValueError):
    """同一聊天请求编号被用于不同消息。"""


def schema_is_current(conn: sqlite3.Connection) -> bool:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='intimacy_migrations'"
    ).fetchone()
    if not exists:
        return False
    version = conn.execute("SELECT MAX(version) FROM intimacy_migrations").fetchone()[0]
    if version != SCHEMA_VERSION:
        raise RuntimeError(f"Unsupported intimacy schema version: {version}")
    return True


def backup_legacy_db(conn: sqlite3.Connection) -> None:
    """在调用方持写锁期间，用另一条连接读原库的一致快照。"""
    db_file = conn.execute("PRAGMA database_list").fetchone()[2]
    if not db_file:  # 纯内存库没有可保留的磁盘数据。
        return
    source_path = Path(db_file)
    backup_path = source_path.with_suffix(source_path.suffix + ".backup-v1")
    temporary = backup_path.with_name(f".{backup_path.name}.{uuid.uuid4().hex}")
    try:
        with closing(sqlite3.connect(source_path)) as source, closing(sqlite3.connect(temporary)) as target:
            source.backup(target)
        temporary.replace(backup_path)
    finally:
        temporary.unlink(missing_ok=True)


def migrate(conn: sqlite3.Connection, at: datetime) -> None:
    for statement in SCHEMA_SQL.strip().rstrip(";").split(";"):
        conn.execute(statement)
    stamp = clock.to_iso(at)
    conn.execute(
        "INSERT INTO intimacy_migrations VALUES (?, ?, ?)",
        (SCHEMA_VERSION, stamp, secrets.token_bytes(32)),
    )
    conn.execute(
        """INSERT INTO intimacy_state(user_id, score, cutover_at)
           SELECT u.user_id, ? + ? * MIN(20, MAX(0, COALESCE(g.intimacy, 0))), ?
           FROM users u LEFT JOIN growth g ON u.user_id = g.user_id""",
        (BASELINE, UNIT, stamp),
    )
    conn.executemany(
        """INSERT INTO growth_unlocks(user_id, tier, unlocked_at)
           SELECT user_id, ?, ? FROM intimacy_state WHERE score >= ?""",
        [(tier, stamp, tier * UNIT) for tier in TIERS],
    )
    conn.execute(
        """INSERT INTO intimacy_events
           SELECT user_id, 'todo_done:' || id, 'legacy_complete', COALESCE(done_at, ?), ?, 0, 0
           FROM todos WHERE status = 'done' OR done_at IS NOT NULL""",
        (stamp, date_key(at)),
    )


def state_for(conn: sqlite3.Connection, user_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM intimacy_state WHERE user_id=?", (user_id,)).fetchone()


def ensure_state(conn: sqlite3.Connection, user_id: str, at: datetime) -> sqlite3.Row:
    row = state_for(conn, user_id)
    if row is not None:
        return row
    user = conn.execute("SELECT created_at FROM users WHERE user_id=?", (user_id,)).fetchone()
    migrated = conn.execute(
        "SELECT applied_at FROM intimacy_migrations WHERE version=?", (SCHEMA_VERSION,),
    ).fetchone()
    cutover = max(aware(user["created_at"]), aware(migrated["applied_at"]))
    conn.execute(
        "INSERT INTO intimacy_state(user_id, score, cutover_at) VALUES (?, ?, ?)",
        (user_id, BASELINE, clock.to_iso(cutover)),
    )
    conn.executemany(
        "INSERT INTO growth_unlocks(user_id, tier, unlocked_at) VALUES (?, ?, ?)",
        [(user_id, tier, clock.to_iso(at)) for tier in TIERS if tier * UNIT <= BASELINE],
    )
    return state_for(conn, user_id)


def aware(value: str) -> datetime:
    stamp = clock.parse_iso(value)
    return stamp.replace(tzinfo=SHANGHAI) if stamp.tzinfo is None else stamp


def date_key(at: datetime) -> str:
    return clock.shanghai_date(at).isoformat()


def chat_digest(conn: sqlite3.Connection, user_id: str, message: str) -> str:
    secret = conn.execute(
        "SELECT digest_key FROM intimacy_migrations WHERE version=?", (SCHEMA_VERSION,),
    ).fetchone()[0]
    normalized = " ".join(unicodedata.normalize("NFKC", message).casefold().split())
    return hmac.digest(secret, f"{user_id}\0{normalized}".encode("utf-8"), "sha256").hex()


def seen_chat_request(conn: sqlite3.Connection, user_id: str, request_id: str, digest: str) -> bool:
    row = conn.execute(
        "SELECT digest FROM chat_receipts WHERE user_id=? AND request_id=?",
        (user_id, request_id),
    ).fetchone()
    if row is None:
        return False
    if not hmac.compare_digest(row["digest"], digest):
        raise ChatRequestConflict("聊天请求编号已用于另一条消息")
    return True


def record_chat(conn: sqlite3.Connection, user_id: str, at: datetime, digest: str,
                request_id: str | None) -> None:
    if request_id:
        if seen_chat_request(conn, user_id, request_id, digest):
            return
        conn.execute("INSERT INTO chat_receipts VALUES (?, ?, ?)", (user_id, request_id, digest))

    day = date_key(at)
    count = conn.execute(
        """SELECT COUNT(*) FROM intimacy_events
           WHERE user_id=? AND settled_date=? AND kind='chat' AND base > 0""",
        (user_id, day),
    ).fetchone()[0]
    award = CHAT_AWARDS[count] if count < len(CHAT_AWARDS) else 0
    apply_event(conn, user_id, f"chat:{day}:{digest}", "chat", "chat_base", award, at, at)


def pending_gates(conn: sqlite3.Connection, user_id: str):
    cutover = aware(state_for(conn, user_id)["cutover_at"])
    rows = conn.execute(
        """SELECT t.id, t.deadline, t.created_at,
                  e24.event_key AS gate24, e72.event_key AS gate72, e7.event_key AS gate7
           FROM todos t
           LEFT JOIN intimacy_events done
             ON done.user_id = t.user_id AND done.event_key = 'todo_done:' || t.id
           LEFT JOIN intimacy_events e24
             ON e24.user_id = t.user_id AND e24.event_key = 'overdue_24h:' || t.id
           LEFT JOIN intimacy_events e72
             ON e72.user_id = t.user_id AND e72.event_key = 'overdue_72h:' || t.id
           LEFT JOIN intimacy_events e7
             ON e7.user_id = t.user_id AND e7.event_key = 'pending_7d:' || t.id
           WHERE t.user_id = ? AND t.status = 'pending' AND done.event_key IS NULL""",
        (user_id,),
    )
    for row in rows:
        created = aware(row["created_at"])
        if row["deadline"] is not None:
            starts = aware(row["deadline"])
            candidates = (
                (timedelta(hours=24), "overdue_24h", "gate24", UNIT // 2),
                (timedelta(hours=72), "overdue_72h", "gate72", UNIT // 2),
            )
        else:
            starts = created
            candidates = ((timedelta(days=7), "pending_7d", "gate7", 3 * UNIT // 10),)
        for wait, kind, gate, base in candidates:
            if row[gate] is not None:
                continue
            try:
                due = max(starts + wait, created)
            except OverflowError:  # 日期上限之后的门槛无法到达。
                continue
            # 旧门槛也写零分回执，改期后同一门槛不能重新产生扣分。
            yield due, row["id"], kind, base if due >= cutover else 0


def settle_overdue(conn: sqlite3.Connection, user_id: str, at: datetime) -> None:
    reached = sorted(gate for gate in pending_gates(conn, user_id) if gate[0] <= at)
    for due, todo_id, kind, base in reached:
        apply_event(conn, user_id, f"{kind}:{todo_id}", kind, "penalty_base", base, due, at)


def complete_todo(conn: sqlite3.Connection, user_id: str, todo_id: str,
                  previous_deadline: datetime | None, at: datetime) -> None:
    late = previous_deadline is not None and previous_deadline < at
    kind = "todo_late" if late else "todo_on_time"
    award = UNIT // 2 if late else 3 * UNIT // 2
    anniversary = apply_event(conn, user_id, f"todo_done:{todo_id}", kind, "todo_base", award, at, at)
    score = state_for(conn, user_id)["score"]
    if score < 85 * UNIT:
        return
    remaining = conn.execute(
        "SELECT 1 FROM todos WHERE user_id=? AND status='pending' LIMIT 1", (user_id,),
    ).fetchone()
    if remaining is None:
        cleared = enqueue_action(conn, user_id, "list_clear", at, visible=anniversary is None)
        if cleared and anniversary:
            conn.execute("UPDATE growth_action_events SET also_list_clear=1 WHERE id=?", (anniversary,))


def adjusted_score(score: int, base: int, *, penalty: bool) -> int:
    if penalty:
        normal = min(base, max(0, score - 60 * UNIT))
        return max(0, score - normal - (base - normal) * 35 // 100)
    normal = min(base, max(0, 95 * UNIT - score))
    return min(MAX_SCORE, score + normal + (base - normal) // 4)


def apply_event(conn: sqlite3.Connection, user_id: str, key: str, kind: str, bucket: str,
                base: int, occurred: datetime, settled: datetime) -> str | None:
    day = date_key(settled)
    current = conn.execute(
        "SELECT * FROM intimacy_daily WHERE user_id=? AND date=?", (user_id, day),
    ).fetchone()
    used = current[bucket] if current else 0
    accepted = min(base, DAILY_LIMITS[bucket] - used)
    old_score = state_for(conn, user_id)["score"]
    score = adjusted_score(old_score, accepted, penalty=bucket == "penalty_base")
    inserted = conn.execute(
        """INSERT INTO intimacy_events VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(user_id, event_key) DO NOTHING""",
        (user_id, key, kind, clock.to_iso(occurred), day, accepted, score - old_score),
    ).rowcount
    if not inserted or not accepted:
        return None
    conn.execute(
        f"""INSERT INTO intimacy_daily(user_id, date, {bucket}) VALUES (?, ?, ?)
            ON CONFLICT(user_id, date) DO UPDATE SET {bucket} = {bucket} + excluded.{bucket}""",
        (user_id, day, accepted),
    )
    conn.execute(
        "UPDATE intimacy_state SET score=?, revision=revision+1 WHERE user_id=?", (score, user_id),
    )
    if score < old_score:
        for action, tier in ACTION_TIERS.items():
            if score < tier * UNIT <= old_score:
                conn.execute(
                    """UPDATE growth_action_events SET visible=0
                       WHERE user_id=? AND kind=? AND date=? AND claimed_at IS NULL""",
                    (user_id, action, day),
                )
        return None
    return unlock_crossed_tiers(conn, user_id, old_score, score, settled)


def unlock_crossed_tiers(conn: sqlite3.Connection, user_id: str, previous: int, score: int,
                         at: datetime) -> str | None:
    for tier in TIERS:
        if previous < tier * UNIT <= score:
            days_known = None
            if tier == 100:
                first = conn.execute("SELECT first_seen FROM users WHERE user_id=?", (user_id,)).fetchone()
                days_known = clock.known_days(aware(first["first_seen"]), at)
            inserted = conn.execute(
                """INSERT INTO growth_unlocks VALUES (?, ?, ?, ?)
                   ON CONFLICT(user_id, tier) DO NOTHING""",
                (user_id, tier, clock.to_iso(at), days_known),
            ).rowcount
            if tier == 100 and inserted:
                return enqueue_action(conn, user_id, "anniversary", at)
    return None


def enqueue_action(conn: sqlite3.Connection, user_id: str, kind: str, at: datetime, *,
                   visible: bool = True) -> str | None:
    event_id = uuid.uuid4().hex
    inserted = conn.execute(
        """INSERT INTO growth_action_events(id, user_id, kind, date, visible) VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(user_id, kind, date) DO NOTHING""",
        (event_id, user_id, kind, date_key(at), visible),
    ).rowcount
    if not inserted:
        return None
    conn.execute("UPDATE intimacy_state SET revision=revision+1 WHERE user_id=?", (user_id,))
    return event_id


def claim_action(conn: sqlite3.Connection, user_id: str, event_id: str, at: datetime) -> bool:
    row = conn.execute(
        "SELECT kind FROM growth_action_events WHERE id=? AND user_id=? AND date=? AND visible=1",
        (event_id, user_id, date_key(at)),
    ).fetchone()
    if row is None or state_for(conn, user_id)["score"] < ACTION_TIERS[row["kind"]] * UNIT:
        return False
    claimed = conn.execute(
        "UPDATE growth_action_events SET claimed_at=? WHERE id=? AND claimed_at IS NULL",
        (clock.to_iso(at), event_id),
    ).rowcount == 1
    if claimed:
        conn.execute("UPDATE intimacy_state SET revision=revision+1 WHERE user_id=?", (user_id,))
    return claimed


def snapshot(conn: sqlite3.Connection, user_id: str, at: datetime) -> dict:
    state = state_for(conn, user_id)
    day = clock.shanghai_date(at)
    day_key = day.isoformat()
    score = state["score"]
    daily = conn.execute(
        "SELECT * FROM intimacy_daily WHERE user_id=? AND date=?", (user_id, day_key),
    ).fetchone()
    reasons = conn.execute(
        """SELECT kind, SUM(base) AS base, SUM(delta) AS delta, COUNT(*) AS count
           FROM intimacy_events WHERE user_id=? AND settled_date=? AND base > 0
           GROUP BY kind ORDER BY MIN(rowid)""",
        (user_id, day_key),
    ).fetchall()
    unlocks = conn.execute(
        "SELECT tier, unlocked_at, days_known FROM growth_unlocks WHERE user_id=? ORDER BY tier",
        (user_id,),
    ).fetchall()
    actions = conn.execute(
        """SELECT id, kind, also_list_clear FROM growth_action_events
           WHERE user_id=? AND date=? AND visible=1 AND claimed_at IS NULL ORDER BY rowid""",
        (user_id, day_key),
    )
    midnight = datetime.combine(day + timedelta(days=1), time.min, SHANGHAI)
    next_gate = min((due for due, _, _, base in pending_gates(conn, user_id) if base), default=midnight)
    next_tier = next((tier for tier in TIERS if tier * UNIT > score), None)
    anniversary = next((row for row in unlocks if row["tier"] == 100), None)
    return {
        "intimacy": score / UNIT,
        "active_tier": max((tier for tier in TIERS if tier * UNIT <= score), default=None),
        "next_tier": next_tier,
        "points_to_next_tier": (next_tier * UNIT - score) / UNIT if next_tier is not None else None,
        "unlocked_tiers": [row["tier"] for row in unlocks],
        "anniversary": {"days_known": anniversary["days_known"], "unlocked_at": anniversary["unlocked_at"]}
        if anniversary else None,
        "today": {
            "date": day,
            **{column: (daily[column] / UNIT if daily else 0) for column in DAILY_LIMITS},
            "gained": sum(max(row["delta"], 0) for row in reasons) / UNIT,
            "lost": sum(max(-row["delta"], 0) for row in reasons) / UNIT,
            "reasons": [{"kind": row["kind"], "base": row["base"] / UNIT,
                         "delta": row["delta"] / UNIT, "count": row["count"]} for row in reasons],
        },
        "as_of": at,
        "next_refresh_at": min(midnight, next_gate),
        "revision": state["revision"],
        "action_events": [
            dict(action) for action in actions if score >= ACTION_TIERS[action["kind"]] * UNIT
        ],
    }
