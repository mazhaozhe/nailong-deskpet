"""deskpet.llm —— 模型层单一入口 LlmGateway（五个预留口子之①）

方案 B「收敛的 ReAct」（§2 已定案，不许推翻）：
    - 循环收敛 ≤2：一次结构化调用 + Pydantic 校验重试，不构造 ReAct Agent、
      不调 init_runtime()/get_agent()/switch_model()、不注册任何工具；
    - 工具表收敛到 0：run_shell 从源头不进进程（本模块绝不 import
      tools.* / memory.rag）；
    - 输出收敛成固定 JSON schema：with_structured_output(Schema)，禁止自由文本。

懒加载铁闸（硬闸门 off_state_no_imports 的命根子）：
    本模块顶层【绝不】import shared.models / langchain_* / shared.cache ——
    全部下沉到 _ensure_model() 内部，只有「开关非关 + 有 Key」的首次真实
    调用才触发 import；关态 / 自动无 Key 下连 import 都不发生。
    缓存 enable_llm_cache(db_path) 必须传路径（v6.1 更正，无参会 TypeError），
    且只在首次真正启用模型时懒加载调一次——启动时无条件调会打穿关态闸门；
    db 路径走 deskpet/paths.py（用户数据目录 cache.sqlite），该函数内部
    已 try/except 吞异常返回 False，缓存不可用不阻断主流程。

分级（读 config/models.json 目录 id，目录文件只读不碰）：
    主力     deepseek-chat   （provider 模型名 deepseek-flash）
    手动档   deepseek-reasoner（deepseek-v4-pro，use_v4_pro=True 时用）
    离线兜底 ollama-local     （主力失败后降级链试一次，再不行回规则版）

Key（附录 B）：ModelManager._resolve_key() 只读环境变量
    os.environ[spec.api_key_env]，阶段 3 自动态探测因此只查注入环境变量
    DEEPSEEK_API_KEY（交付清单已声明的约定变量名）；safeStorage 加密那份
    在 Electron 主进程里，阶段 5 由主进程解密后经环境变量注入。
    明文 Key 不进日志 / 仓库 / exe 包（本模块不打印、不落盘任何 Key）。
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence, Type

from pydantic import BaseModel

from deskpet import capabilities, cost, model_counter, paths, settings

# ---- config/models.json 目录 id（阶段 3 分级依据） ----
PRIMARY_ID = "deepseek-chat"        # 主力：deepseek-flash
MANUAL_ID = "deepseek-reasoner"     # 手动档：deepseek-v4-pro
LOCAL_FALLBACK_ID = "ollama-local"  # 离线兜底
# 注入用环境变量名（= 目录 api_key_env；附录 B：假 Key 验收一律走它）
KEY_ENV = "DEEPSEEK_API_KEY"

MAX_ATTEMPTS = 2   # 方案 B：单请求模型调用 ≤2（首次 + Pydantic 校验重试 1 次）


@dataclass
class Outcome:
    """invoke() 的统一结果。degraded=True 时 data=None，degrade_reason 必填。"""
    data: Optional[BaseModel]
    degraded: bool
    degrade_reason: str
    model_id: str = ""


def classify_error(exc: BaseException) -> tuple[str, str]:
    """把模型异常归类成明确原因（401 / 余额 / 网络分别报，不吞异常细节）。"""
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if ("402" in low or "balance" in low or "余额" in str(exc)
            or "insufficient" in low):
        return "balance", "账户余额不足（402）"
    if ("401" in low or "403" in low or "auth" in low
            or "invalid api key" in low or "unauthorized" in low):
        return "key_invalid", "Key 无效或未授权（401）"
    if ("connection" in low or "timeout" in low or "network" in low
            or "refused" in low or "resolve" in low):
        return "network", "网络错误（连接失败/超时）"
    return "other", text[:200]


class LlmGateway:
    """所有路由的模型调用都必须经过本入口（口子①：重试与配额集中在这里）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._manager: Any = None          # ModelManager（懒加载后才有）
        self._cache_enabled = False
        self._pricing: dict[str, Optional[dict]] = {}
        self._session_calls: dict[str, int] = {}  # 游戏单局调用计数（内存态）

    # ------------------------------------------------------------------
    # 开关 + Key 探测（关态零 import 的关键：这里绝不 import 模型层）
    # ------------------------------------------------------------------
    @staticmethod
    def _key_present() -> bool:
        """自动/开态的 Key 探测：阶段 3 只覆盖注入环境变量（附录 B）。"""
        return bool(os.environ.get(KEY_ENV, "").strip())

    def _probe(self) -> tuple[bool, str]:
        """三态判定。返回 (可用, 不可用原因)。可用时原因为空。"""
        state = settings.get_switch()
        if state == settings.SWITCH_OFF:
            return False, "模型开关=关（纯规则版，零调用）"
        if self._key_present():
            return True, ""
        if state == settings.SWITCH_AUTO:
            return False, "自动档未探测到 Key（环境变量未配置），静默降级规则版"
        return False, "开关=开但未配置 Key（请填写自己的 Key；401/网络/余额都会如实上报）"

    # ------------------------------------------------------------------
    # 懒加载启用：唯一的模型层 import 点
    # ------------------------------------------------------------------
    @staticmethod
    def _catalog_path() -> Path:
        # config/ 只读不碰（硬约束）；catalog 位置随仓库根
        return Path(__file__).resolve().parents[1] / "config" / "models.json"

    def _load_pricing(self) -> None:
        """从目录读各模型 pricing（estimate_cost 三参的 pricing 来源）。"""
        try:
            raw = json.loads(
                self._catalog_path().read_text(encoding="utf-8"))
            self._pricing = {
                m.get("id", ""): (m.get("pricing") or None)
                for m in raw.get("models", [])
            }
        except (OSError, json.JSONDecodeError):
            self._pricing = {}  # 读不到 → 成本按「价格未配置」如实标注

    def _ensure_model(self, model_id: str) -> Any:
        """首次真正启用才 import shared.models / shared.cache（懒加载唯一入口）。

        manager 只建一次；换档用 manager.switch(id)（那是 ModelManager 自己的
        实例切换，不是被禁的 agent api.switch_model()——后者会重建 Agent）。
        """
        with self._lock:
            if self._manager is None:
                from shared.models import ModelManager   # ← 懒加载 import 点
                mgr = ModelManager(self._catalog_path())
                mgr.init_default(PRIMARY_ID)
                self._manager = mgr
                self._load_pricing()
                if not self._cache_enabled:
                    # v6.1 §4.5：必须传路径；只在首次真正启用时调一次。
                    # 「启动时无条件调」会打穿关态 sys.modules 闸门，禁止。
                    from shared.cache import enable_llm_cache
                    self._cache_enabled = enable_llm_cache(
                        paths.get_cache_db_path())
            if model_id and model_id != self._manager.current_id:
                self._manager.switch(model_id)
            return self._manager.current_model

    # ------------------------------------------------------------------
    # 单次调用（所有路由唯一入口）
    # ------------------------------------------------------------------
    async def invoke(self, *, schema: Type[BaseModel],
                     messages: Sequence[dict], route: str, capability: str,
                     prefer_manual: bool = False,
                     session_id: Optional[str] = None) -> Outcome:
        """结构化调用 + Pydantic 校验重试（≤2）+ 降级链 + 成本闸 + 配额。

        集中在 gateway 的四件事（口子④）：
            重试次数、成本配额、单局调用上限、degraded/degrade_reason
            （将来「检索失败」也复用这对字段做降级信号）。
        """
        cap = capabilities.get(capability)
        if cap is None:
            return Outcome(None, True, f"能力未登记：{capability}")
        if not cap.enabled:
            return Outcome(None, True, f"能力 {capability} 已停用")

        # 游戏大脑：单局（策略+复盘共用会话计数）模型调用 ≤3
        if (session_id is not None and cap.per_session_cap is not None
                and self._session_calls.get(session_id, 0) >= cap.per_session_cap):
            return Outcome(None, True,
                           f"本局模型调用已达上限（{cap.per_session_cap} 次），回默认规则")

        ok, reason = self._probe()
        if not ok:
            return Outcome(None, True, reason)

        # 每日成本闸：超上限自动降级并显示原因，不许静默继续烧钱
        caps = settings.get_cost_caps()
        daily = caps.get("daily_yuan")
        per_call = caps.get("per_call_yuan")
        if daily is not None and cost.today_cost_yuan() >= daily:
            return Outcome(None, True,
                           f"今日成本 {cost.today_cost_yuan():.4f} 元已达"
                           f"每日上限 {daily} 元，自动降级规则版")

        want = MANUAL_ID if prefer_manual else PRIMARY_ID
        last_reason = ""
        for _attempt in range(MAX_ATTEMPTS):
            try:
                model = self._ensure_model(want)
            except Exception as exc:   # 模型层启用失败 → 如实归类，不走 500
                _, human = classify_error(exc)
                last_reason = f"模型层启用失败（{human}）"
                break
            model_counter.bump(route)
            if session_id is not None:
                self._session_calls[session_id] = \
                    self._session_calls.get(session_id, 0) + 1
            try:
                raw = await model.with_structured_output(
                    schema, include_raw=True).ainvoke(messages)
            except Exception as exc:   # 通信类错误：不重试同档，直接走降级链
                _, human = classify_error(exc)
                last_reason = human
                break

            # 校验失败的调用同样烧了 token：先如实记账再谈重试
            usage = getattr(raw.get("raw"), "usage_metadata", None) \
                if isinstance(raw, dict) else None
            call_cost, _note = cost.record_usage(
                route, want, usage, self._pricing.get(want))
            if (call_cost is not None and per_call is not None
                    and call_cost > per_call):
                return Outcome(None, True,
                               f"单次成本 {call_cost:.4f} 元超上限"
                               f" {per_call} 元，自动降级")

            parsed = raw.get("parsed") if isinstance(raw, dict) else None
            perr = raw.get("parsing_error") if isinstance(raw, dict) else None
            if parsed is not None and perr is None:
                return Outcome(parsed, False, "", model_id=want)
            last_reason = (f"模型输出校验失败"
                          f"（{type(perr).__name__ if perr else 'schema不符'}），已重试")
        # 校验重试用尽 / 通信失败 → 降级链第二级：ollama-local 离线兜底
        fallback_outcome = await self._try_local_fallback(
            schema, messages, route, capability, session_id)
        if fallback_outcome is not None:
            fallback_outcome.degrade_reason = (
                f"{last_reason}；已降级本地 ollama-local")
            return fallback_outcome
        return Outcome(None, True,
                       f"{last_reason}；ollama-local 也不可用，回规则版")

    async def _try_local_fallback(self, schema: Type[BaseModel],
                                  messages: Sequence[dict], route: str,
                                  capability: str,
                                  session_id: Optional[str]) -> Optional[Outcome]:
        """降级链：缓存 → ollama-local → 规则版。ollama 不在跑就如实失败。"""
        cap = capabilities.get(capability)
        if cap is None or not cap.enabled:
            return None
        if (session_id is not None and cap.per_session_cap is not None
                and self._session_calls.get(session_id, 0) >= cap.per_session_cap):
            return None
        try:
            model = self._ensure_model(LOCAL_FALLBACK_ID)
        except Exception:
            # 实例化失败（依赖缺失/目录问题等）→ 如实走规则版
            return None
        model_counter.bump(f"{route}#ollama-fallback")
        if session_id is not None:
            self._session_calls[session_id] = \
                self._session_calls.get(session_id, 0) + 1
        try:
            raw = await model.with_structured_output(
                schema, include_raw=True).ainvoke(messages)
            usage = getattr(raw.get("raw"), "usage_metadata", None) \
                if isinstance(raw, dict) else None
            # ollama-local 目录无 pricing → record_usage 返回 (None,"免费/价格未配置")
            cost.record_usage(f"{route}#ollama-fallback", LOCAL_FALLBACK_ID,
                              usage, self._pricing.get(LOCAL_FALLBACK_ID))
            parsed = raw.get("parsed") if isinstance(raw, dict) else None
            perr = raw.get("parsing_error") if isinstance(raw, dict) else None
            if parsed is not None and perr is None:
                return Outcome(parsed, True, "", model_id=LOCAL_FALLBACK_ID)
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Key 管理（阶段 3 本地接口；safeStorage 份阶段 5 由主进程接线）
    # ------------------------------------------------------------------
    def key_status(self) -> dict:
        """脱敏状态：只回「已配置」+ 尾四位，明文绝不外泄。"""
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            return {"configured": False, "masked_tail": None,
                    "source": "env", "detail": "未探测到 Key（环境变量未注入）"}
        return {"configured": True, "masked_tail": key[-4:],
                "source": "env",
                "detail": "已配置（尾四位）；safeStorage 加密份待阶段 5 主进程注入"}

    def delete_key(self) -> dict:
        """删 Key：清掉进程内环境变量注入 + 重置懒加载态 → 立刻回规则版。"""
        had = self._key_present()
        os.environ.pop(KEY_ENV, None)
        self.reset()
        return {"deleted": had,
                "note": "环境变量注入的 Key 已清除，立刻回规则版；"
                        "safeStorage 加密份由阶段 5 主进程同步删除"}

    async def check_connectivity(self) -> dict:
        """一键连通性检查：401 / 余额 / 网络分别返回，不吞异常。

        会发一次最小真实请求（成本极低）；拿不到 usage 如实标未知。
        """
        ok, reason = self._probe()
        if not ok:
            state = settings.get_switch()
            status = ("switch_off" if state == settings.SWITCH_OFF
                      else "not_configured")
            return {"status": status, "ok": False, "detail": reason}
        try:
            model = self._ensure_model(PRIMARY_ID)
            model_counter.bump("key/check")
            msg = await model.ainvoke("ping")
            usage = getattr(msg, "usage_metadata", None)
            cost.record_usage("key/check", PRIMARY_ID, usage,
                              self._pricing.get(PRIMARY_ID))
            return {"status": "ok", "ok": True, "detail": "连通正常"}
        except Exception as exc:
            kind, human = classify_error(exc)
            return {"status": kind, "ok": False, "detail": human}

    # ------------------------------------------------------------------
    # 状态管理
    # ------------------------------------------------------------------
    def reset(self) -> None:
        """重置懒加载态：删 Key / 切开关后立刻生效（不用重启）。"""
        with self._lock:
            self._manager = None
            self._cache_enabled = False
            self._session_calls.clear()

    def session_calls(self, session_id: str) -> int:
        return self._session_calls.get(session_id, 0)

    def clear_session(self, session_id: str) -> None:
        self._session_calls.pop(session_id, None)


# 模块级单例：构造零 import（安全），首次真正调用才可能触发模型层 import
gateway = LlmGateway()
