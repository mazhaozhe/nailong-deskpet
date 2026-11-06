"""deskpet.models —— 数据模型（Pydantic）

全部本地数据、按用户隔离，不依赖模型。
字段定义阶段，不写存储实现。

对应 brief 第四节「不用模型」的部位：
    待办状态（什么事·截止时间·还差什么）
    回来补报快照
    认识天数 / 记忆累积 / 亲密度心情
    游戏记忆（输在哪 / 下次怎么改 / 用了哪套策略）

以及「要模型」部位返回的固定 JSON schema（校验重试用）：
    通知抽清单结果
    一句话变待办结果
    游戏策略 / 复盘
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ===========================================================================
# 待办状态（不用模型 · 本地 JSON/SQLite · 按用户隔离）
# ===========================================================================
class TodoStatus(str, Enum):
    """待办状态。漏了不扣分（brief 第六节：不惩罚）。"""
    PENDING = "pending"        # 待办，未完成
    DONE = "done"              # 已勾选完成


class TodoItem(BaseModel):
    """一条待办：什么事 · 截止时间 · 还差什么。

    `gap`（还差什么）是主线灵魂字段——回来补报与逐条对照卡都靠它。
    初始可空，用户/补报流程后续填充。

    `user_id` 来源：前端 localStorage UUID（见 deskpet/auth.py），不是访问口令。
    口令管「能不能进门」，UUID 管「你是谁」，两层分开。
    """
    id: str = Field(..., description="待办唯一 id（UUID/雪花，前端不感知生成方式）")
    user_id: str = Field(..., description="所属用户（localStorage UUID，按用户隔离）")
    title: str = Field(..., description="什么事（一句话标题）")
    deadline: Optional[datetime] = Field(
        None, description="截止时间；无截止的待办可为空（但主线鼓励填）")
    gap: str = Field("", description="还差什么（逐条交付清单摘要；主线灵魂字段）")
    status: TodoStatus = Field(TodoStatus.PENDING, description="勾选状态")
    source: str = Field(
        "manual",
        description="来源：manual 手建 / notice 贴通知抽出来 / voice 一句话变的")
    created_at: datetime = Field(..., description="创建时间")
    updated_at: datetime = Field(..., description="最近更新时间")
    done_at: Optional[datetime] = Field(None, description="完成时间（勾选时写）")


class ChecklistItem(BaseModel):
    """从通知抽出的逐条交付项（贴通知→抽要求清单的产物）。

    与 TodoItem 区别：ChecklistItem 是「通知原文里要求了什么」的客观拆解，
    用户确认后才落成 TodoItem。它是模型输出 schema，也是前端对照卡的数据。
    """
    text: str = Field(..., description="一条要求/交付项原文摘述")
    category: str = Field(
        "deliverable",
        description="分类：deliverable 交付物 / format 格式 / deadline 截止 / other")
    matched: bool = Field(
        False, description="前端对照卡用：用户是否已勾选对应已完成")


# ===========================================================================
# 回来补报快照（不用模型 · 当前时间比 deadline 快照重算 · 不做后台定时任务）
# ===========================================================================
class CatchupSnapshot(BaseModel):
    """打开页面时的「回来补报还差什么」快照。

    主交付是 Electron 透明置顶小窗 + 托盘常驻（v6.1 单机 exe 口径），
    补报用「打开页面时按快照重算」，托盘常驻提醒由阶段 5 的系统通知补齐。
    全部由当前时间 vs 各待办 deadline 计算得出，确定性、可复现。
    """
    user_id: str
    snapshot_at: datetime = Field(..., description="快照时间（=打开页面时刻）")
    days_since_first: int = Field(
        ..., description="认识你的第 N 天（今天 − 首次使用日期）")
    overdue: list[TodoItem] = Field(
        default_factory=list, description="已逾期未完成（红线，最先报）")
    due_soon: list[TodoItem] = Field(
        default_factory=list, description="临近截止（如 24/48h 内）未完成")
    pending: list[TodoItem] = Field(
        default_factory=list, description="其它未完成待办")
    done_since_last: list[TodoItem] = Field(
        default_factory=list, description="上次打开至今新完成的（正向反馈）")
    greeting: str = Field(
        "",
        description="补报开场白。由 PersonaStage 模板表确定性生成，绝不调模型；"
                    "引用 growth.stage 但不依赖模型服务，阶段 2 断网验收要求"
                    "模型调用数 = 0。若日后加模型润色，必须做成可选项且失败回落模板。")


# ===========================================================================
# 成长状态（不用模型 · 本地数据+事件累积 · 按用户隔离 · 模型不可用照常累积）
# ===========================================================================
class PersonaStage(str, Enum):
    """形象阶段（资源档，换阶段不动逻辑）。2-3 个表情阶段与状态绑定。

    brief 第六节：不做喂食、不做皮肤商城、不做抽卡。
    """
    NEW = "new"              # 初识（第 1~6 天）
    FAMILIAR = "familiar"    # 熟悉（第 7~29 天）
    CLOSE = "close"          # 老友（第 30 天起）


class GrowthState(BaseModel):
    """成长 = 记得的事越来越多，不是等级经验。

    统一口径用「认识你的第 N 天」，禁用「第 N 天/存活天数」游戏化说法。
    亲密度/心情只由真实行为驱动（按时完成 +1，漏了不扣，不惩罚）。
    """
    user_id: str
    first_seen: datetime = Field(..., description="首次使用日期（持久化）")
    days_known: int = Field(..., description="认识你的第 N 天（今天 − first_seen）")
    memory_count: int = Field(
        ..., description="记忆条数（累积的 ddl/项目/卡点/游戏复盘总数）")
    intimacy: int = Field(
        0, ge=0, description="亲密度（按时完成 +1，漏了不扣）")
    mood: int = Field(
        100, ge=0, le=100, description="心情 0~100，仅展示用")
    stage: PersonaStage = Field(
        PersonaStage.NEW, description="形象阶段（由 days_known 映射，资源档）")
    # 模拟展示用：临时覆盖「当前日期」以演示跨天成长，不写脏真实数据
    simulated_now: Optional[datetime] = Field(
        None, description="模拟第 N 天时临时覆盖的当前日期；None=用真实今天")


# ===========================================================================
# 游戏记忆（不用模型累积 · 模型只在复盘层写入 · 不做全量回放/用户画像）
# ===========================================================================
class GameMemory(BaseModel):
    """游戏复盘结论。brief 第五节：只存三类字段，不做全量回放、不做用户画像。

    - lost_at：输在哪
    - next_change：下次怎么改
    - strategy_used：用了哪套策略（策略名 + 参数）
    """
    game_id: str
    user_id: str
    played_at: datetime
    result: str = Field(..., description="win / lose / draw")
    lost_at: str = Field("", description="输在哪（复盘层输出）")
    next_change: str = Field("", description="下次怎么改（复盘层输出）")
    strategy_used: str = Field("", description="用了哪套策略（策略名+参数 JSON 摘要）")


# ===========================================================================
# 模型输出固定 JSON schema（要模型 · 校验重试 · 失败回落规则兜底）
# ===========================================================================
class NoticeParseResult(BaseModel):
    """贴通知 → 抽要求清单的固定输出 schema。

    deepseek-flash + 本 schema + 校验重试；长通知复杂拆解走 v4-pro 手动开关。
    校验不过回落到静态文案/规则拆分（降级可运行）。
    """
    items: list[ChecklistItem] = Field(
        ..., description="逐条交付清单（要求/格式/截止等分类）")
    summary: str = Field("", description="一句话总结这条通知要交什么")
    deadline_hint: Optional[datetime] = Field(
        None, description="若通知里提到截止时间，解析出来")


class VoiceParseResult(BaseModel):
    """一句话变待办的固定输出 schema。

    deepseek-flash 口语解析，保留规则兜底（解析失败/校验不过→规则拆日期关键词）。
    """
    title: str = Field(..., description="待办标题（规范化后）")
    deadline: Optional[datetime] = Field(None, description="解析出的截止时间")
    confidence: float = Field(
        1.0, ge=0, le=1, description="置信度；低于阈值时前端提示用户确认")


class GameStrategy(BaseModel):
    """游戏策略层输出 schema（开局/每 N 秒一次，不碰实时执行）。

    模型只出策略 JSON，不出单步按键；白名单动作表决定可行性；
    校验不过回落默认规则。单局模型调用 ≤3 次。
    """
    strategy_name: str = Field(..., description="策略名（如 aggressive/defensive）")
    params: dict = Field(
        default_factory=dict, description="策略参数（如 {aggression: 0.7}）")
    reason: str = Field("", description="为什么选这套（解说用，不进执行）")


class GameReview(BaseModel):
    """游戏复盘层输出 schema（每局结束一次）。

    输出写入 GameMemory，下一局策略层可读历史 GameMemory 做「会进步」。
    """
    lost_at: str = Field("", description="输在哪")
    next_change: str = Field("", description="下次怎么改")
    takeaway: str = Field("", description="一句话复盘结论（解说层用）")


class GameCommentary(BaseModel):
    """游戏解说层输出 schema（阶段 4 纯追加）：一句气泡台词 + 情绪标签。

    解说层只写台词：不碰操作、不出按键、不进实时执行层；
    失败回落静态兜底台词（degraded=true），单局模型调用 ≤3 共用计数。
    """
    line: str = Field(..., description="一句气泡台词（不碰操作、不出按键）")
    mood: str = Field(
        "calm",
        description="情绪标签：cheer/calm/worried/proud/encourage")


class TaskQAAnswer(BaseModel):
    """任务内一问一答的固定输出 schema（只聊当前任务，不做百科/检索）。

    deepseek-flash + 本 schema + 校验重试；失败回落静态兜底文案
    （页面不空白、有提示）。answer 是给用户看的回答正文——这是
    「场景编排的固定回执」，不是模型自由发挥的自由文本。
    """
    answer: str = Field(..., description="针对提问的回答（≤200 字，只围绕当前任务）")
    suggestions: list[str] = Field(
        default_factory=list, description="下一步行动建议（0~3 条）")
