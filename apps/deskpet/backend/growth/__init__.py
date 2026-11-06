"""apps.deskpet.backend.growth —— 成长状态 + 补报 + greeting 模板

greeting 由 PersonaStage 模板表确定性生成，绝不调模型。
brief 第六节：漏了不扣（不惩罚），按时完成 +1 亲密度。
"""

from __future__ import annotations

from apps.deskpet.backend.models import CatchupSnapshot, PersonaStage


GREETING_TEMPLATES = {
    PersonaStage.NEW: [
        "来啦。今天是认识你的第 {n} 天。",
        "嗨，第 {n} 天。先看看你的待办——",
    ],
    PersonaStage.FAMILIAR: [
        "第 {n} 天了。我帮你理了一下还差什么——",
        "今天第 {n} 天。补个报：",
    ],
    PersonaStage.CLOSE: [
        "认识你的第 {n} 天，老朋友。还差这些——",
        "第 {n} 天，回来啦。直接看正事：",
    ],
}


def greeting_for(stage: PersonaStage, days: int) -> str:
    """按阶段生成开场白。确定性取第一项，绝不调模型。"""
    templates = GREETING_TEMPLATES.get(stage,
                                        GREETING_TEMPLATES[PersonaStage.NEW])
    return templates[0].format(n=days)


def build_catchup(*, user_id: str, days_known: int,
                  todos: list) -> CatchupSnapshot:
    """按当前时间 vs deadline 重算补报（不做事后定时器）。"""
    from apps.deskpet.backend import clock
    now = clock.now(user_id)  # 传 user_id：补报只吃该用户自己的模拟态（P0-3）
    overdue, due_soon, pending = [], [], []
    DUE_SOON_HOURS = 48

    for t in todos:
        if t.status.value == "done":
            continue
        if t.deadline:
            if t.deadline <= now:
                overdue.append(t)
            elif (t.deadline - now).total_seconds() <= DUE_SOON_HOURS * 3600:
                due_soon.append(t)
            else:
                pending.append(t)
        else:
            pending.append(t)

    return CatchupSnapshot(
        user_id=user_id,
        snapshot_at=now,
        days_since_first=days_known,
        overdue=overdue,
        due_soon=due_soon,
        pending=pending,
        done_since_last=[],
        greeting=greeting_for(clock.stage_for_days(days_known), days_known))