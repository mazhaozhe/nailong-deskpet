"""deskpet.schemas —— 前后端接口契约（请求/响应 schema）

前端只调自家业务接口，永不直连模型 API（brief 第七节硬约束）。
本文件只定义 schema，路由签名见 deskpet/api.py，实现待后续部分填。

设计要点：
    - 场景专用 prompt + 固定 JSON schema + 校验重试，不要自由文本
    - 使用者自填 Key、safeStorage 加密存 userData；三态开关（关/自动/开）
      全局生效；本地单次与每日成本上限（超限自动降级并显示原因）
    - 不做公网三层限流 / 访问口令 / 团队 Key（v6.1 单机 exe 口径，旧口径作废）
    - 服务不可用时降级可运行（缓存 → ollama-local → 规则版/静态兜底）
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from deskpet.models import (
    CatchupSnapshot,
    ChecklistItem,
    GrowthState,
    GameCommentary,
    GameMemory,
    GameReview,
    GameStrategy,
    NoticeParseResult,
    TodoItem,
    TodoStatus,
    VoiceParseResult,
)


# ===========================================================================
# 成长
# ===========================================================================
class GrowthResponse(BaseModel):
    data: GrowthState


class SimulateDaysRequest(BaseModel):
    """模拟第 N 天（按钮/滑杆跳 7/30/100 天）。

    只临时覆盖展示用的「当前日期」，不写脏真实数据。
    前端渲染记忆条数、语气变化与形象阶段。
    """
    target_day: int = Field(..., ge=1, le=1000, description="跳到第几天")
    # 后端据此推算 simulated_now = first_seen + target_day 天，回填 GrowthState


class SimulateDaysResponse(BaseModel):
    data: GrowthState
    note: str = Field("", description="说明这是模拟展示，非真实数据")


# ===========================================================================
# 待办 CRUD
# ===========================================================================
class TodoCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    deadline: Optional[datetime] = None
    gap: str = ""
    source: str = "manual"


class TodoUpdate(BaseModel):
    title: Optional[str] = None
    deadline: Optional[datetime] = None
    gap: Optional[str] = None
    status: Optional[TodoStatus] = None  # 用枚举收紧，拒绝任意字符串
    # 勾选完成时后端自动写 done_at 并触发亲密度 +1


class TodoListResponse(BaseModel):
    data: list[TodoItem]


class TodoResponse(BaseModel):
    data: TodoItem


# ===========================================================================
# 回来补报快照
# ===========================================================================
class CatchupResponse(BaseModel):
    data: CatchupSnapshot


# ===========================================================================
# 贴通知 → 抽要求清单（接模型）
# ===========================================================================
class NoticeParseRequest(BaseModel):
    notice_text: str = Field(..., min_length=1, description="贴进来的通知全文")
    use_v4_pro: bool = Field(
        False,
        description="长通知复杂拆解手动开关；默认 False 走 flash，绝不默认开贵模型")


class NoticeParseResponse(BaseModel):
    data: NoticeParseResult
    degraded: bool = Field(
        False, description="是否降级（模型不可用/校验失败→规则兜底）")
    degrade_reason: str = Field("", description="降级原因（答辩可讲）")


class ChecklistConfirmRequest(BaseModel):
    """用户确认对照卡后，把勾选的 ChecklistItem 落成 TodoItem。"""
    items: list[ChecklistItem]
    deadline_override: Optional[datetime] = None


class ChecklistConfirmResponse(BaseModel):
    created: list[TodoItem]


# ===========================================================================
# 一句话变待办（接模型）
# ===========================================================================
class VoiceTodoRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=500, description="一句话口语输入")


class VoiceTodoResponse(BaseModel):
    data: VoiceParseResult
    degraded: bool = False
    degrade_reason: str = ""


# ===========================================================================
# 任务内一问一答（接模型 · SSE 流式 · 只聊当前任务）
# ===========================================================================
class TaskQARequest(BaseModel):
    todo_id: str = Field(..., description="关联的待办 id，锁定只聊当前任务")
    question: str = Field(..., min_length=1, description="用户针对该任务的提问")


class TaskQAStreamChunk(BaseModel):
    """SSE 流式分块。"""
    type: str = Field(..., description="token / done / error / degrade")
    content: str = ""
    degraded: bool = False


# ===========================================================================
# 导出 docx（不用模型 · gen_docx 固定模板填字段）
# ===========================================================================
class ExportDocxRequest(BaseModel):
    todo_ids: list[str] = Field(..., min_length=1, description="要导出的待办 id 列表")
    title: str = Field("待办交付清单", description="文档标题")


class ExportDocxResponse(BaseModel):
    download_url: str = Field(..., description="临时下载链接（带过期口令）")
    expires_at: datetime


# ===========================================================================
# 游戏大脑
# ===========================================================================
class GameStartRequest(BaseModel):
    game_id: str
    brain_level: int = Field(
        2, ge=1, le=2,
        description="大脑档位：1仅解说 / 2策略+复盘+解说(默认)。"
                    "3逐帧决策仅离线演示，Web 契约不暴露，公网传 3 一律 422")


class GameStartResponse(BaseModel):
    session_id: str
    game_id: str
    brain_level: int
    initial_strategy: Optional[GameStrategy] = None  # 策略层开局输出
    degraded: bool = False
    degrade_reason: str = ""


class GameStrategyRequest(BaseModel):
    """策略层：读结构化状态快照，输出固定 JSON（开局或每 N 秒一次）。"""
    session_id: str
    state_snapshot: dict = Field(..., description="游戏结构化状态快照（DOM/JS 读出）")


class GameStrategyResponse(BaseModel):
    data: GameStrategy
    degraded: bool = False
    degrade_reason: str = ""
    model_calls_this_game: int = Field(
        0, ge=0, description="本局已调用模型次数（硬上限 ≤3）")


class GameReviewRequest(BaseModel):
    """复盘层：每局结束一次。"""
    session_id: str
    final_state: dict
    strategy_history: list[str] = Field(..., description="本局用过的策略序列")


class GameReviewResponse(BaseModel):
    data: GameReview
    degraded: bool = False
    degrade_reason: str = ""


class GameMemoryResponse(BaseModel):
    data: list[GameMemory]


# ===========================================================================
# 三态开关（阶段 3 新增，纯追加——上面所有已定响应字段一字未动）
# ===========================================================================
class ModelSwitchRequest(BaseModel):
    """设置三态开关。全局生效：贴通知/任务问答/游戏策略复盘全归它管。"""
    state: Literal["off", "auto", "on"] = Field(
        ..., description="off 关（纯规则零调用）/ auto 自动（默认，探测到 "
        "Key 才启用）/ on 开（强制模型，失败给明确原因）")


# ===========================================================================
# 游戏解说层（阶段 4 纯追加——上面所有已定响应字段一字未动）
# ===========================================================================
class GameCommentaryRequest(BaseModel):
    """解说层：读状态快照，出一句气泡台词（不碰操作、不出按键）。"""
    session_id: str
    state_snapshot: dict = Field(..., description="游戏结构化状态快照（DOM/JS 读出）")
    strategy_name: str = Field("", description="本局刚选的策略名（解说上下文，可空）")


class GameCommentaryResponse(BaseModel):
    data: GameCommentary
    degraded: bool = False
    degrade_reason: str = ""
