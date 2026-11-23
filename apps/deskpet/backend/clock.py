"""以北京时间计算相识日期，统一提供真实时钟。"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

# 本地时区：Asia/Shanghai = UTC+8
_LOCAL_TZ = timezone(timedelta(hours=8))

def real_now() -> datetime:
    """返回当前北京时间。"""
    return datetime.now(_LOCAL_TZ)


def parse_iso(s: str) -> datetime:
    """解析存储的 ISO 字符串为带时区的 datetime。"""
    return datetime.fromisoformat(s)


def to_iso(dt: datetime) -> str:
    """datetime → ISO 字符串（带时区）。

    naive datetime（如通知里抽出的 deadline_hint）按本地时区补齐，
    这是所有 deadline 落库前的统一关口，禁止绕过。
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_LOCAL_TZ)
    return dt.isoformat()


def days_between(first: datetime, current: datetime) -> int:
    """认识天数：只用日期部分做差，不含时分秒。

    例：first=2026-11-05 23:50, current=2026-11-06 00:10 → 1 天
    （同一天差 20 分钟也算 0，跨过零点算 1）
    """
    return (shanghai_date(current) - shanghai_date(first)).days


def shanghai_date(when: datetime):
    if when.tzinfo is None:
        when = when.replace(tzinfo=_LOCAL_TZ)
    return when.astimezone(_LOCAL_TZ).date()


def known_days(first: datetime, current: datetime) -> int:
    return max(1, days_between(first, current) + 1)


def stage_for_days(days: int):
    """根据认识天数映射形象阶段（PersonaStage）。

    new: 1~6 天
    familiar: 7~29 天
    close: 30 天起
    """
    from apps.deskpet.backend.models import PersonaStage
    if days < 7:
        return PersonaStage.NEW
    if days < 30:
        return PersonaStage.FAMILIAR
    return PersonaStage.CLOSE
