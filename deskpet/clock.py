"""deskpet.clock —— datetime 单一入口（补强点 3）

所有 deadline/认识天数/补报快照时间都走 now()，禁止散落 datetime.now()。
模拟跳 N 天只替换 now() 的返回值，真实数据绝不写脏。

datetime 口径（拍板）：
    - 存：ISO 8601 字符串，带本地时区（Asia/Shanghai）
    - 算：只用「日期部分」做差，不含时分秒（避免差一天 bug）
    - now()：本地时间，统一入口
    - 模拟：simulated_now 只覆盖 now()，不进数据库

为什么本地时间：
    「认识第 N 天」和「截止时间」都是学生本地的概念。11/8 23:59 是北京时间 ddl，
    用 UTC 算会差 8 小时，跨天边界就错了。存的时候带时区字符串避免歧义。

模拟态隔离（P0-3 拍板）：
    - 模拟值按 user_id 存进 _simulated 字典，不是模块级单值——
      A 用户模拟第 30 天，B 用户同请求周期仍是真实天数；
    - 清除入口：clear_simulation(user_id)（/growth/simulate/clear 调），
      不传 user_id 则全清（进程重启也自然全清，纯内存不落库）；
    - 前端在页面加载时调一次 /growth/simulate/clear，等效「刷新回真实天数」；
    - 写库（first_seen/done_at/created_at）一律 real_now()，永远真实时刻。
"""

from __future__ import annotations

from datetime import datetime, timezone, timedelta
from typing import Optional

# 本地时区：Asia/Shanghai = UTC+8
_LOCAL_TZ = timezone(timedelta(hours=8))

# 模拟态：user_id -> 模拟的「现在」。空字典 = 所有人都在真实时间。
# 纯内存，不落库；进程重启自动清空。
_simulated: dict[str, datetime] = {}


def now(user_id: Optional[str] = None) -> datetime:
    """统一时间入口。

    带 user_id 且该用户处于模拟态时返回模拟值；否则返回真实本地现在。
    所有「展示/计算」场景必须传 user_id，避免读到别人的模拟态。
    """
    if user_id is not None and user_id in _simulated:
        return _simulated[user_id]
    return datetime.now(_LOCAL_TZ)


def real_now() -> datetime:
    """真实现在，绝不返回模拟值。写库（first_seen/done_at/created_at）专用。"""
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
    d1 = first.astimezone(_LOCAL_TZ).date()
    d2 = current.astimezone(_LOCAL_TZ).date()
    return (d2 - d1).days


def stage_for_days(days: int):
    """根据认识天数映射形象阶段（PersonaStage）。

    new: 1~6 天
    familiar: 7~29 天
    close: 30 天起
    """
    from deskpet.models import PersonaStage
    if days < 7:
        return PersonaStage.NEW
    if days < 30:
        return PersonaStage.FAMILIAR
    return PersonaStage.CLOSE


def set_simulated_now(target_day: int, first_seen: datetime,
                      user_id: str) -> datetime:
    """设置某用户的模拟时间：跳到 first_seen + target_day 天。

    只进 _simulated 字典，不写库——纯内存，进程重启或
    clear_simulation(user_id) 后失效。返回模拟的 datetime。
    """
    _simulated[user_id] = first_seen + timedelta(days=target_day)
    return _simulated[user_id]


def clear_simulation(user_id: Optional[str] = None) -> None:
    """清除模拟覆盖，回到真实时间。

    传 user_id 只清该用户；不传则全清（调试用）。
    前端页面加载时调 /growth/simulate/clear 触发，等效「刷新回真实」。
    """
    if user_id is None:
        _simulated.clear()
    else:
        _simulated.pop(user_id, None)


def is_simulating(user_id: Optional[str] = None) -> bool:
    if user_id is not None:
        return user_id in _simulated
    return bool(_simulated)
