"""apps.deskpet.backend.chat_memory —— 闲聊的短期 / 长期记忆（只读，不写库）

一、短期记忆（会话内上下文）
    实现取舍：**以后端会话表为准**，键是 (user_id, session_id)。
    - 为什么不选「前端每轮回传全量 history」：桌宠页面会 reload（保存 Key 后
      主进程会重启后端并 loadURL），前端内存里的消息一起没了；而且前端回传
      等于把「上下文真源」放在渲染进程，谁都能改，模型侧无法自证。
    - req.history 只在一个 session_id **第一次出现**时用来播种（后端刚重启、
      表里还没有记录时），之后一律以本表为准。
    - 限 6 轮（12 条）+ 总字符上限；只在进程内存里，不落盘、不写库、不进日志。
    - 进程重启即清空：这是「会话内」记忆，不是长期记忆。

二、长期记忆（本机真实数据，只读）
    growth（认识第几天 / 亲密度 / 阶段）、todos（未完成标题与最近截止）——
    全部按 X-User-Id 取，0 条就如实说 0 条。
    **模型不许写库、不许虚构、不许预置数据**：本模块只有读函数。

三、主动关心
    care_line() 是纯规则话术（零模型调用），给前端小气泡用；
    不新起后台 Tick，只复用现有的待办刷新 / hover 展开触发点。
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from apps.deskpet.backend import storage
from apps.deskpet.backend.models import PersonaStage, TodoStatus

# ---- 短期记忆上限（与 schemas._MAX_TURNS / _MAX_HISTORY_ITEMS 对齐） ----
MAX_TURNS = 6                 # 最多 6 轮（user + assistant 各一条 = 12 条）
MAX_TOTAL_CHARS = 1200        # 全部历史加起来的字符上限（从最新往前保留）
SESSION_TTL_SECONDS = 6 * 3600  # 6 小时没动过就丢（进程内存态，防无限增长）

_STAGE_CN = {
    PersonaStage.NEW: "初识",
    PersonaStage.FAMILIAR: "熟悉",
    PersonaStage.CLOSE: "老友",
}


# ===========================================================================
# 长期记忆：只读取本机真实数据
# ===========================================================================
@dataclass
class MemoryFacts:
    """一次闲聊要用到的全部真实事实（字段为空就表示「确实没有」，不许补）。"""
    days_known: int = 1
    intimacy: int = 0
    stage_cn: str = "初识"
    pending: list[str] = field(default_factory=list)
    nearest_deadline: str = ""

    @property
    def pending_count(self) -> int:
        return len(self.pending)


def collect_facts(conn, user_id: str) -> MemoryFacts:
    """读 growth / todos（全是只读查询，按 uid 隔离）。"""
    g = storage.get_growth(conn, user_id)
    todos = storage.list_todos(conn, user_id)
    pending = [t for t in todos if t.status != TodoStatus.DONE]

    nearest = ""
    stamps = [t for t in pending if t.deadline is not None]
    if stamps:
        first = min(stamps, key=lambda t: t.deadline)
        nearest = (f"「{first.title}」在 "
                   f"{first.deadline.strftime('%Y-%m-%d %H:%M')} 前")

    return MemoryFacts(
        days_known=max(1, g.days_known),
        intimacy=g.intimacy,
        stage_cn=_STAGE_CN.get(g.stage, "初识"),
        pending=[str(t.title) for t in pending][:6],
        nearest_deadline=nearest,
    )


def render_memory_block(facts: MemoryFacts) -> str:
    """把人话版记忆写进 system prompt（空的部分明确写「没有」）。"""
    lines = [
        f"- 认识这个人第 {facts.days_known} 天（关系阶段：{facts.stage_cn}，"
        f"亲密度 {facts.intimacy}）"
    ]
    if facts.pending_count:
        titles = "、".join(f"「{t}」" for t in facts.pending)
        lines.append(f"- 目前 {facts.pending_count} 件待办没做完：{titles}")
        if facts.nearest_deadline:
            lines.append(f"- 最近的一件：{facts.nearest_deadline}")
    else:
        lines.append("- 目前一件待办也没有（空清单是正常的新朋友状态，"
                     "不许替他想出一件来）")
    return "\n".join(lines)


def memory_summary(facts: MemoryFacts) -> str:
    """规则回复用的一句短话（不调模型）。0 待办时明确说没有。"""
    if not facts.pending_count:
        return "你的清单还是空的"
    head = facts.pending[0]
    if facts.pending_count == 1:
        return f"你手上还有「{head}」这一件"
    return f"你手上还有 {facts.pending_count} 件，最前面是「{head}」"


# ===========================================================================
# 短期记忆：后端会话表（进程内存，按 uid + session 隔离）
# ===========================================================================
class ChatSessionMemory:
    """最近 ≤6 轮的会话记忆。线程安全、有 TTL、永不落盘。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._turns: dict[tuple[str, str], list[dict]] = {}
        self._touched: dict[tuple[str, str], float] = {}

    @staticmethod
    def _key(user_id: str, session_id: str) -> tuple[str, str]:
        return (str(user_id or "anon"), str(session_id or ""))

    def _trim(self, turns: list[dict]) -> list[dict]:
        """从最新往前保留：≤MAX_TURNS 轮（12 条）且总字符 ≤MAX_TOTAL_CHARS。"""
        kept: list[dict] = []
        budget = MAX_TOTAL_CHARS
        for turn in reversed(turns[-MAX_TURNS * 2:]):
            text = str(turn.get("content") or "")
            if len(text) > budget:
                break
            budget -= len(text)
            kept.append(turn)
        kept.reverse()
        return kept

    def _expire(self) -> None:
        now = time.time()
        for key, at in list(self._touched.items()):
            if now - at > SESSION_TTL_SECONDS:
                self._turns.pop(key, None)
                self._touched.pop(key, None)

    def history(self, user_id: str, session_id: str) -> list[dict]:
        """取该会话最近几轮（返回副本，调用方改不到内部状态）。"""
        key = self._key(user_id, session_id)
        with self._lock:
            self._expire()
            return [dict(t) for t in self._turns.get(key, [])]

    def seed_if_new(self, user_id: str, session_id: str,
                    history: list[dict] | None) -> list[dict]:
        """该 session 没有记录时，用前端回传的 history 播种；有记录就完全忽略。"""
        key = self._key(user_id, session_id)
        with self._lock:
            self._expire()
            if key in self._turns or not history:
                return [dict(t) for t in self._turns.get(key, [])]
            seeded = self._trim([
                {"role": t["role"], "content": str(t["content"])}
                for t in history if t.get("role") in ("user", "assistant")
            ])
            self._turns[key] = seeded
            self._touched[key] = time.time()
            return [dict(t) for t in seeded]

    def append(self, user_id: str, session_id: str, role: str,
               content: str) -> None:
        """记一轮（user / assistant 各调一次）。空内容不记。"""
        text = str(content or "").strip()
        if not text:
            return
        key = self._key(user_id, session_id)
        with self._lock:
            turns = self._turns.get(key, [])
            turns.append({"role": role, "content": text})
            self._turns[key] = self._trim(turns)
            self._touched[key] = time.time()

    def reset(self) -> None:
        """测试用：清空全部会话记忆。"""
        with self._lock:
            self._turns.clear()
            self._touched.clear()


# 模块级单例：构造零 import、零 I/O（关态下也不碰任何模型层）
chat_sessions = ChatSessionMemory()


# ===========================================================================
# 主动关心（纯规则、零模型调用）
# ===========================================================================
def care_line(facts: MemoryFacts) -> str:
    """一句话关心话术。复用现有待办刷新/气泡展开触发点，不新起后台 Tick。

    确定性取值（同一状态同一句），方便验收复现；绝不调模型。
    """
    if not facts.pending_count:
        return "清单是空的，先歇一会儿吧，我在这儿。"
    if facts.nearest_deadline:
        return f"{facts.nearest_deadline}，要不先把它往前排？"
    if facts.pending_count == 1:
        return f"还剩「{facts.pending[0]}」这一件，慢慢来。"
    return f"还有 {facts.pending_count} 件没做完，一件件来，我陪你。"
