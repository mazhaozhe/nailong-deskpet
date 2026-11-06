"""apps.deskpet.backend.capabilities —— 能力注册表（五个预留口子之③）

v6.1 §4.4：本期哪怕只登记五项，每项也必须声明
    requires_network / requires_key / degraded_fallback / enabled
四字段——三态开关与配额查询统一查这张表，禁止在各路由里各写一份 if。

将来接「查资料 / 联网检索」（本期不实现）时的扩展方式：
    新能力先在本表登记（requires_network=True），检索实现必须隔离在
    apps/deskpet/backend/retrieval.py 独立纯函数，由代码在固定时机触发；
    绝不经本表放行任何工具，也绝不 import apps.agent.tools.* / infrastructure.retrieval.rag
    （apps/agent/tools/__init__.py 会连带 apps.agent.tools.coding/run_shell 进进程，重演 P0-5）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Capability:
    """一项模型能力的声明。"""
    name: str
    requires_network: bool          # 需要外网（DeepSeek API）
    requires_key: bool             # 需要 Key（离线兜底 ollama-local 不需要）
    degraded_fallback: str          # 降级策略标识（规则版/静态兜底，答辩可讲）
    enabled: bool                   # 三态开关之外的能力级停用位
    description: str = ""
    per_session_cap: Optional[int] = None  # 单局调用硬上限（游戏大脑 ≤3）


REGISTRY: dict[str, Capability] = {
    "parse_notice": Capability(
        name="parse_notice", requires_network=True, requires_key=True,
        degraded_fallback="notice_rules", enabled=True,
        description="贴通知 → 抽要求清单（deepseek-flash；use_v4_pro 走 v4-pro）"),
    "task_qa": Capability(
        name="task_qa", requires_network=True, requires_key=True,
        degraded_fallback="static_answer", enabled=True,
        description="任务内一问一答（只聊当前任务）"),
    "from_voice": Capability(
        name="from_voice", requires_network=True, requires_key=True,
        degraded_fallback="voice_rules", enabled=True,
        description="一句话变待办（无 Key 时规则兜底，不留 500）"),
    "game_strategy": Capability(
        name="game_strategy", requires_network=True, requires_key=True,
        degraded_fallback="default_strategy", enabled=True,
        description="游戏策略层（固定 JSON，不进实时执行层）",
        per_session_cap=3),
    "game_review": Capability(
        name="game_review", requires_network=True, requires_key=True,
        degraded_fallback="static_review", enabled=True,
        description="游戏复盘层（每局结束一次）",
        per_session_cap=3),
    "game_commentary": Capability(
        name="game_commentary", requires_network=True, requires_key=True,
        degraded_fallback="static_commentary", enabled=True,
        description="游戏解说层（一句话气泡台词，不碰操作不进执行层）",
        per_session_cap=3),
}


def get(name: str) -> Optional[Capability]:
    """按名查能力；未登记返回 None（gateway 据此拒绝调用）。"""
    return REGISTRY.get(name)


def all_capabilities() -> list[Capability]:
    return list(REGISTRY.values())


def describe() -> list[dict]:
    """注册表安全视图（调试 / 配额查询接口用）。"""
    return [{
        "name": c.name,
        "requires_network": c.requires_network,
        "requires_key": c.requires_key,
        "degraded_fallback": c.degraded_fallback,
        "enabled": c.enabled,
        "description": c.description,
        "per_session_cap": c.per_session_cap,
    } for c in REGISTRY.values()]
