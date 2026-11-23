from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from apps.deskpet.backend import clock, intimacy, storage
from apps.deskpet.backend.models import TodoStatus


BEIJING = timezone(timedelta(hours=8))
UID = "storage-test"


@pytest.fixture
def now(monkeypatch):
    value = [datetime(2026, 11, 23, 12, tzinfo=BEIJING)]
    monkeypatch.setattr(clock, "real_now", lambda: value[0])
    return value


@pytest.fixture
def db(tmp_path, now):
    with closing(storage.get_connection(tmp_path / "deskpet.db")) as conn:
        storage.init_schema(conn)
        yield conn


def todo(conn, *, user=UID, deadline=None):
    return storage.create_todo(
        conn, user, title="翻译实验报告", deadline=deadline, gap="", source="manual",
    )


def complete(conn, item):
    return storage.update_todo(conn, item.user_id, item.id, status=TodoStatus.DONE)


def test_install_date_anchors_matching_user_without_replacing_old_start(db, now, monkeypatch):
    monkeypatch.setenv("DESKPET_LOCAL_UID", UID)
    monkeypatch.setenv("DESKPET_INSTALLED_AT", "2026-11-22T16:00:00Z")
    now[0] += timedelta(days=3)
    assert storage.get_growth(db, "other-user").days_known == 1
    first = storage.get_growth(db, UID)
    assert first.days_known == 4
    assert first.intimacy == 75
    assert first.unlocked_tiers == [40, 60, 75]
    monkeypatch.setenv("DESKPET_INSTALLED_AT", "2026-11-26T00:00:00Z")
    assert storage.get_growth(db, UID).first_seen == first.first_seen


@pytest.mark.parametrize(("mode", "first_full_day"), [("chat", 20), ("todo", 14), ("both", 8)])
def test_each_activity_can_reach_exactly_100(db, now, mode, first_full_day):
    for day in range(1, first_full_day + 1):
        if mode in {"chat", "both"}:
            for i in range(4):
                storage.record_chat_reply(db, UID, f"消息 {i}", f"chat-{day}-{i}")
        if mode in {"todo", "both"}:
            for _ in range(3):
                complete(db, todo(db))
        current = storage.get_growth(db, UID)
        assert current.today.chat_base == (2 if mode in {"chat", "both"} else 0)
        assert current.today.todo_base == (3 if mode in {"todo", "both"} else 0)
        assert (current.intimacy == 100) == (day == first_full_day)
        now[0] += timedelta(days=1)
    assert current.anniversary.days_known == first_full_day
    assert current.unlocked_tiers == [40, 60, 75, 85, 95, 100]


def test_chat_request_and_normalized_message_deduplication_survive_day_change(db, now):
    assert storage.record_chat_reply(db, UID, " ＨＥＬＬＯ  世界 ", "first").intimacy == 76
    assert storage.record_chat_reply(db, UID, "hello 世界", "second").intimacy == 76
    assert storage.record_chat_reply(db, UID, "问候", "third").intimacy == 76.6
    now[0] += timedelta(days=1)
    assert storage.record_chat_reply(db, UID, "hello 世界", "first").intimacy == 76.6
    assert storage.record_chat_reply(db, UID, "hello 世界", "fresh").intimacy == 77.6
    with pytest.raises(ValueError):
        storage.record_chat_reply(db, UID, "另一条消息", "first")
    assert "hello 世界" not in "\n".join(db.iterdump())


def test_direct_late_completion_consumes_due_gates_and_only_one_reward(db, now):
    item = todo(db, deadline=now[0] + timedelta(hours=1))
    now[0] += timedelta(hours=73)
    assert complete(db, item).status == TodoStatus.DONE
    growth = storage.get_growth(db, UID)
    assert growth.intimacy == 74.5
    assert growth.today.penalty_base == 1
    assert growth.today.todo_base == .5
    storage.update_todo(db, UID, item.id, status=TodoStatus.PENDING)
    now[0] += timedelta(days=8)
    assert storage.sync_growth(db, UID).intimacy == 74.5
    complete(db, item)
    assert storage.get_growth(db, UID).intimacy == 74.5


def test_offline_excess_is_settled_before_edits_and_never_replayed(db, now):
    first = todo(db, deadline=now[0] + timedelta(hours=1))
    todo(db, deadline=now[0] + timedelta(hours=2))
    todo(db)
    now[0] += timedelta(days=8)
    storage.update_todo(db, UID, first.id, deadline=now[0] + timedelta(days=10))
    growth = storage.get_growth(db, UID)
    assert growth.intimacy == 74
    assert growth.today.penalty_base == 1
    assert [reason.kind for reason in growth.today.reasons] == ["overdue_24h"]
    now[0] += timedelta(days=1)
    assert storage.sync_growth(db, UID).intimacy == 74


def test_no_deadline_penalty_fires_at_seven_days_once(db, now):
    todo(db)
    now[0] += timedelta(days=7, microseconds=-1)
    assert storage.sync_growth(db, UID).intimacy == 75
    now[0] += timedelta(microseconds=1)
    assert storage.sync_growth(db, UID).intimacy == 74.7
    assert storage.sync_growth(db, UID).intimacy == 74.7


def test_reading_real_growth_does_not_settle_overdue_or_create_cards(db, now):
    todo(db, deadline=now[0])
    storage.get_growth(db, UID)
    now[0] += timedelta(days=1)
    growth = storage.get_growth(db, UID)
    assert growth.days_known == 2
    assert growth.anniversary is None and growth.intimacy == 75
    assert storage.sync_growth(db, UID).intimacy == 74.5


def test_concurrent_completion_and_action_claim_are_once(db, tmp_path):
    item = todo(db)

    def perform(_):
        conn = storage.get_connection(tmp_path / "deskpet.db")
        try:
            storage.init_schema(conn)
            complete(conn, item)
            state = storage.sync_growth(conn, UID, panel_open=True)
            for event in state.action_events:
                if event.kind == "welcome":
                    return storage.claim_growth_action(conn, UID, event.id)
            return False
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(perform, range(2))) == [False, True]
    assert storage.get_growth(db, UID).intimacy == 76.5


def test_migration_backs_up_original_rows_and_skips_only_old_gates(tmp_path, now):
    path = tmp_path / "legacy.db"
    conn = storage.get_connection(path)
    try:
        conn.executescript(storage.SCHEMA_SQL)
        started = now[0] - timedelta(days=20)
        conn.execute("INSERT INTO users VALUES (?, ?, ?)", (UID, started.isoformat(), started.isoformat()))
        conn.execute("INSERT INTO growth VALUES (?, ?, ?, ?)", (UID, 8, 100, started.isoformat()))
        cutover = now[0]
        now[0] -= timedelta(days=2)
        pending = todo(conn, deadline=cutover - timedelta(hours=25))
        old_done = todo(conn)
        now[0] = cutover
        conn.execute("UPDATE todos SET status = 'done' WHERE id = ?", (old_done.id,))
        conn.commit()
        storage.init_schema(conn)
        storage.init_schema(conn)
        assert storage.get_growth(conn, UID).intimacy == 83
        assert storage.get_growth(conn, UID).first_seen == started
        assert storage.sync_growth(conn, UID).intimacy == 83
        backup, = tmp_path.glob("*.backup*")
        with sqlite3.connect(backup) as saved:
            assert saved.execute("SELECT intimacy FROM growth WHERE user_id=?", (UID,)).fetchone() == (8,)
            assert saved.execute("SELECT COUNT(*) FROM todos").fetchone() == (2,)
        now[0] += timedelta(hours=47)
        assert storage.sync_growth(conn, UID).intimacy == 82.5
        storage.update_todo(conn, UID, old_done.id, status=TodoStatus.PENDING)
        complete(conn, old_done)
        assert storage.get_growth(conn, UID).intimacy == 82.5
        assert storage.get_todo(conn, UID, pending.id).status == TodoStatus.PENDING
    finally:
        conn.close()


def test_rescheduling_cannot_revive_a_waived_legacy_gate(tmp_path, now):
    with closing(storage.get_connection(tmp_path / "legacy.db")) as conn:
        conn.executescript(storage.SCHEMA_SQL)
        cutover = now[0]
        now[0] -= timedelta(days=2)
        pending = todo(conn, deadline=cutover - timedelta(hours=25))
        now[0] = cutover
        storage.init_schema(conn)
        storage.update_todo(conn, UID, pending.id, deadline=now[0] + timedelta(hours=1))
        now[0] += timedelta(hours=73)
        growth = storage.sync_growth(conn, UID)
        assert growth.intimacy == 74.5
        assert [reason.kind for reason in growth.today.reasons] == ["overdue_72h"]


def test_backdated_deadline_can_first_accrue_a_penalty_when_the_todo_exists(db, now):
    todo(db, deadline=now[0] - timedelta(hours=25))
    growth = storage.sync_growth(db, UID)
    assert growth.intimacy == 74.5
    assert [reason.kind for reason in growth.today.reasons] == ["overdue_24h"]


@pytest.mark.parametrize(("score", "expected"), [(59, 58.65), (60.4, 59.79)])
def test_daily_penalty_splits_at_sixty(db, now, score, expected):
    item = todo(db, deadline=now[0] + timedelta(hours=1))
    storage.get_growth(db, UID)
    db.execute("UPDATE intimacy_state SET score=? WHERE user_id=?", (round(score * 10_000), UID))
    db.commit()
    now[0] += timedelta(hours=73)
    growth = storage.sync_growth(db, UID)
    assert growth.intimacy == expected
    assert growth.today.penalty_base == 1
    assert storage.get_todo(db, UID, item.id).status == TodoStatus.PENDING


def test_daily_awards_only_slow_above_ninety_five(db, now):
    storage.get_growth(db, UID)
    db.execute("UPDATE intimacy_state SET score=940000 WHERE user_id=?", (UID,))
    db.commit()
    for index in range(3):
        storage.record_chat_reply(db, UID, f"聊天{index}", f"request-{index}")
    for _ in range(2):
        complete(db, todo(db))
    growth = storage.get_growth(db, UID)
    assert growth.intimacy == 96
    assert growth.today.chat_base + growth.today.todo_base == 5
    assert growth.today.gained == 2


def test_first_full_score_can_merge_with_last_todo_and_cannot_repeat(db, now):
    display = storage.get_growth(db, UID)
    db.execute("UPDATE intimacy_state SET score=999999 WHERE user_id=?", (UID,))
    db.commit()
    last = todo(db)
    complete(db, last)
    state = storage.get_growth(db, UID)
    [event] = state.action_events
    assert event.kind == "anniversary" and event.also_list_clear
    assert state.anniversary.days_known == display.days_known

    now[0] += timedelta(days=1)
    overdue = todo(db, deadline=now[0] - timedelta(days=1))
    assert storage.sync_growth(db, UID).intimacy == 99.5
    assert not storage.claim_growth_action(db, UID, event.id)
    for index in range(3):
        current = storage.record_chat_reply(db, UID, f"回到满分 {index}", f"new-{index}")
    assert current.intimacy == 100 and current.anniversary == state.anniversary
    assert current.action_events == []
    assert storage.get_todo(db, UID, overdue.id).status == TodoStatus.PENDING


def test_old_version_completion_is_remembered_when_unchecked_after_reupgrade(db, now):
    item = todo(db)
    storage.get_growth(db, UID)
    with db:
        db.execute("UPDATE todos SET status='done', done_at=? WHERE id=?", (now[0].isoformat(), item.id))

    storage.update_todo(db, UID, item.id, status=TodoStatus.PENDING)
    storage.update_todo(db, UID, item.id, status=TodoStatus.DONE)
    assert storage.get_growth(db, UID).intimacy == 75


def test_claiming_existing_animation_does_not_reconcile_score(db, now):
    todo(db, deadline=now[0])
    now[0] += timedelta(days=1) - timedelta(seconds=1)
    growth = storage.sync_growth(db, UID, panel_open=True)
    [event] = growth.action_events
    now[0] += timedelta(seconds=1)

    assert storage.claim_growth_action(db, UID, event.id)
    assert storage.get_growth(db, UID).intimacy == 75
    assert storage.sync_growth(db, UID).intimacy == 74.5


def test_todo_and_growth_rollback_together(db, monkeypatch):
    item = todo(db)
    apply_award = intimacy.complete_todo

    def fail_after_award(*args):
        apply_award(*args)
        raise RuntimeError("failure after award")

    monkeypatch.setattr(intimacy, "complete_todo", fail_after_award)
    with pytest.raises(RuntimeError, match="failure after award"):
        complete(db, item)
    assert storage.get_todo(db, UID, item.id).status == TodoStatus.PENDING
    assert storage.get_growth(db, UID).intimacy == 75
