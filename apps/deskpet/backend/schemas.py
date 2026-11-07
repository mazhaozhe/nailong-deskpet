"""apps.deskpet.backend.schemas —— 前后端接口契约（请求/响应 schema）

前端只调自家业务接口，永不直连模型 API（brief 第七节硬约束）。
本文件只定义 schema，路由签名见 apps/deskpet/backend/api.py，实现待后续部分填。

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

from apps.deskpet.backend.models import (
    CatchupSnapshot,
    ChatReply,
    ChecklistItem,
    GrowthState,
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
# 三态开关（阶段 3 新增，纯追加——上面所有已定响应字段一字未动）
# ===========================================================================
class ModelSwitchRequest(BaseModel):
    """设置三态开关。全局生效：贴通知/任务问答/闲聊全归它管。"""
    state: Literal["off", "auto", "on"] = Field(
        ..., description="off 关（纯规则零调用）/ auto 自动（默认，探测到 "
        "Key 才启用）/ on 开（强制模型，失败给明确原因）")


# ===========================================================================
# 自由闲聊（纯追加——上面所有已定响应字段一字未动）
# ===========================================================================
# 覆盖依据：11/7 10:49 用户明确要求「要会真正的闲聊」，覆盖交接卡 §2.7
# 「不做通用问答」。本会话边界不变：单次 gateway.invoke()、固定 schema、
# 工具表 0、不联网、不注册工具；完整 Agent（含工具与联网）属会话 4。
#
# 短期记忆二选一的取舍：**以后端会话表为准**（api.py 里 _chat_sessions）。
# 理由：桌宠窗口会被 reload（换 Key 重启后端也会重新 loadURL），前端内存里的
# 消息会一起没；把最近几轮放后端进程内存里，至少「同一次运行」内上下文不断。
# req.history 只在该 session_id 第一次出现（后端刚重启、还没有记录）时用来
# 播种——它是前端气泡里已经显示过的那几条，属于兜底，不是第二套真源。
_MAX_TURNS = 6            # 短期记忆最多 6 轮（12 条 user/assistant）
_MAX_TURN_CHARS = 400     # 单条上限（含标点）；超长直接 422，不静默截断进记忆
_MAX_HISTORY_ITEMS = 12   # 一次请求最多带 12 条（6 轮），按轮截断
_MAX_MESSAGE_CHARS = 500  # 单条用户输入上限；空消息由路由走规则回复，不 500


class ChatTurn(BaseModel):
    """一轮历史的单条消息（仅用于 req.history 播种，限条数限字符）。"""
    role: Literal["user", "assistant"] = Field(
        ..., description="user 用户 / assistant 桌宠（历史里不出现 system）")
    content: str = Field(..., min_length=1, max_length=_MAX_TURN_CHARS)


class ChatRequest(BaseModel):
    """自由闲聊请求。空 message 合法（路由走规则回复，零模型调用）。"""
    message: str = Field(
        "", max_length=_MAX_MESSAGE_CHARS,
        description="用户这一句；空串/纯空白不报错，走规则回复")
    session_id: Optional[str] = Field(
        None, max_length=64,
        description="前端一次会话一个 id；不传则退化成「每条都无上下文」")
    history: list[ChatTurn] = Field(
        default_factory=list, max_length=_MAX_HISTORY_ITEMS,
        description="可选：前端已有气泡记录，仅在该 session_id 首次出现时用于播种")


class ChatResponse(BaseModel):
    """自由闲聊响应。

    `source` 与 `degraded` 必须如实：模型回的话 source=model/degraded=false；
    规则兜底 source=rule；模型不可用或被降级链兜住时 degraded=true +
    degrade_reason（前端据此标「规则版」，绝不把兜底伪装成模型回复）。
    """
    data: ChatReply
    degraded: bool = False
    degrade_reason: str = ""
    source: Literal["model", "rule"] = "model"
    source_detail: str = Field(
        "", description="来源补充说明（如「简单问候走规则回复，未调用模型」）")
