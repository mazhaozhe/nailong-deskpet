"""apps.deskpet.backend.llm —— 模型层单一入口 LlmGateway（五个预留口子之①）

方案 B「收敛的 ReAct」（§2 已定案，不许推翻）：
    - 循环收敛 ≤2：一次结构化调用 + Pydantic 校验重试，不构造 ReAct Agent、
      不调 init_runtime()/get_agent()/switch_model()、不注册任何工具；
    - 工具表收敛到 0：run_shell 从源头不进进程（本模块绝不 import
      apps.agent.tools.* / infrastructure.retrieval.rag）；
    - 输出收敛成固定 JSON schema：with_structured_output(Schema)，禁止自由文本。
      注意 tool_choice 必须显式传 "auto"：LangChain 默认会强制绑定某个函数
      （tool_choice={"type":"function",...}），而 DeepSeek 思考模式对这种强制
      tool_choice 直接回 400「Thinking mode does not support this tool_choice」。
      这是拿到真 Key 后才暴露的问题——桩不校验请求形态，所以桩测全绿也没发现。

懒加载铁闸（硬闸门 off_state_no_imports 的命根子）：
    本模块顶层【绝不】import infrastructure.llm.models / langchain_* / infrastructure.cache ——
    全部下沉到 _ensure_model() 内部，只有「开关非关 + 有 Key」的首次真实
    调用才触发 import；关态 / 自动无 Key 下连 import 都不发生。
    缓存 enable_llm_cache(db_path) 必须传路径（v6.1 更正，无参会 TypeError），
    且只在首次真正启用模型时懒加载调一次——启动时无条件调会打穿关态闸门；
    db 路径走 apps/deskpet/backend/paths.py（用户数据目录 cache.sqlite），该函数内部
    已 try/except 吞异常返回 False，缓存不可用不阻断主流程。

分级（读 config/models.json 目录 id，目录文件只读不碰）：
    主力     deepseek-chat   （provider 模型名 deepseek-flash）
    手动档   deepseek-reasoner（deepseek-v4-pro，use_v4_pro=True 时用）
    离线兜底 ollama-local     （主力失败后降级链试一次，再不行回规则版）

Key（附录 B）：ModelManager._resolve_key() 只读环境变量
    os.environ[spec.api_key_env]，所以本进程只认注入环境变量
    DEEPSEEK_API_KEY（交付清单已声明的约定变量名）。
    实际链路：桌宠设置页收 Key → Electron 主进程 safeStorage 加密存 userData →
    重启 Python 子进程时经环境变量注入（main.js startBackend）→ 本模块探测并启用。
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

from apps.deskpet.backend import capabilities, cost, model_counter, paths, settings

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
    """把模型异常归类成明确原因（401 / 余额 / 网络分别报，不吞异常细节）。

    返回的文案直接进 degrade_reason / 设置页「一键检查」结果，所以要同时
    说清「是哪一类」和「下一步做什么」，不能只回一句「调用失败」。
    """
    text = f"{type(exc).__name__}: {exc}"
    low = text.lower()
    if ("402" in low or "balance" in low or "余额" in str(exc)
            or "insufficient" in low):
        return "balance", "账户余额不足（402）：请到 DeepSeek 平台充值后再试"
    if ("401" in low or "403" in low or "auth" in low
            or "invalid api key" in low or "unauthorized" in low):
        return "key_invalid", "Key 无效或未授权（401）：请确认 Key 复制完整、未被删除或吊销"
    if "400" in low or "invalid_request_error" in low:
        # 请求形态被拒（如思考模式不支持强制 tool_choice）：不是 Key 的问题，
        # 也不是网络问题，单独一类，免得用户在 Key 上瞎折腾。
        return "request_rejected", f"请求被模型拒绝（400）：{text[:160]}"
    if ("connection" in low or "timeout" in low or "network" in low
            or "refused" in low or "resolve" in low):
        return "network", "网络错误（连接失败/超时）：请检查网络与代理设置后重试"
    return "other", text[:200]


def _structured_call(model: Any, schema: Type[BaseModel],
                     messages: Sequence[dict]) -> Any:
    """统一的结构化调用构造：显式 tool_choice="auto"。

    不传 tool_choice 时 LangChain 会把 tool_choice 写成强制绑定，
    DeepSeek 思考模式（deepseek-flash / v4-pro）直接 400 拒绝。
    少数自定义/第三方模型包装不认 tool_choice 关键字时回退到默认写法，
    保持兼容（默认写法对这些模型本来就是对的）。
    """
    try:
        return model.with_structured_output(
            schema, include_raw=True, tool_choice="auto")
    except TypeError:
        return model.with_structured_output(schema, include_raw=True)


def _decode_first_json(text: str) -> Any:
    """从文本里解出第一个完整 JSON 对象，容忍尾部杂质。

    实测（真 Key，deepseek-flash）：模型偶尔在多字节中文后面多吐一个 `}`，
    `json.loads` 报 "Extra data: line 1 column N"，LangChain 随即把这条
    tool call 判成 invalid_tool_call、parsed=None。内容其实是好的，
    只是尾巴多了字符。raw_decode 只吃第一个完整对象，正好治这个。
    """
    start = text.find("{")
    if start < 0:
        return None
    try:
        payload, _end = json.JSONDecoder().raw_decode(text[start:])
    except (json.JSONDecodeError, ValueError):
        return None
    return payload if isinstance(payload, dict) else None


def _validate_payload(payload: Any, schema: Type[BaseModel]) -> Any:
    """payload → schema 实例，过不了返回 None。

    额外要求「至少有一个字段名对得上」：本仓库的 schema 字段大多带默认值，
    Pydantic 默认 extra=ignore，若只做 model_validate，一份完全不相关的 JSON
    （字段名全不匹配）会被悄悄收成「全默认值的空对象」——那不是抢救，
    是把真实失败伪装成成功。
    """
    if not isinstance(payload, dict):
        return None
    if not set(schema.model_fields) & set(payload):
        return None
    try:
        return schema.model_validate(payload)
    except Exception:
        return None


def _recover_structure(raw: Any, schema: Type[BaseModel]) -> Any:
    """从 raw 消息里抢救结构化结果，救不回来返回 None。

    两类实测畸形（都出现在 tool_choice="auto" 下）：
      1. 模型不用 tool call，直接回纯文本 JSON（可能带 ```json 围栏）；
      2. tool call 的 arguments 尾部多一个 `}`，LangChain 判 invalid_tool_call。
    内容本身是对的，能过 Pydantic 校验就收下，免得白白重试一次再降级。
    """
    if not isinstance(raw, dict):
        return None
    msg = raw.get("raw")
    if msg is None:
        return None

    # 2. 坏的 tool call：参数串里抢救
    for call in (getattr(msg, "invalid_tool_calls", None) or []):
        args = call.get("args")
        text = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
        parsed = _validate_payload(_decode_first_json(text), schema)
        if parsed is not None:
            return parsed

    # 1. 纯文本 JSON
    content = getattr(msg, "content", "") or ""
    if isinstance(content, list):   # 多模态分片：只拼文本段
        content = "".join(
            part.get("text", "") for part in content
            if isinstance(part, dict) and part.get("type") == "text")
    text = str(content).strip()
    if not text:
        return None
    if text.startswith("```"):      # 去掉 ```json 围栏
        parts = text.split("```")
        text = parts[1] if len(parts) > 1 else text
        if text[:4].lower() == "json":
            text = text[4:]
        text = text.strip().rstrip("`").strip()
    return _validate_payload(_decode_first_json(text), schema)



class LlmGateway:
    """所有路由的模型调用都必须经过本入口（口子①：重试与配额集中在这里）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._manager: Any = None          # ModelManager（懒加载后才有）
        self._cache_enabled = False
        self._pricing: dict[str, Optional[dict]] = {}
        self._session_calls: dict[str, int] = {}  # 单会话调用计数（内存态）

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
        return Path(__file__).resolve().parents[3] / "config" / "models.json"

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
        """首次真正启用才 import infrastructure.llm.models / infrastructure.cache（懒加载唯一入口）。

        manager 只建一次；换档用 manager.switch(id)（那是 ModelManager 自己的
        实例切换，不是被禁的 agent api.switch_model()——后者会重建 Agent）。
        """
        with self._lock:
            if self._manager is None:
                from infrastructure.llm.models import ModelManager   # ← 懒加载 import 点
                mgr = ModelManager(self._catalog_path())
                mgr.init_default(PRIMARY_ID)
                self._manager = mgr
                self._load_pricing()
                if not self._cache_enabled:
                    # v6.1 §4.5：必须传路径；只在首次真正启用时调一次。
                    # 「启动时无条件调」会打穿关态 sys.modules 闸门，禁止。
                    from infrastructure.cache import enable_llm_cache
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

        # 会话级配额：per_session_cap 非空的按 session_id 计数拦下（当前 5 项能力均为 None）
        if (session_id is not None and cap.per_session_cap is not None
                and self._session_calls.get(session_id, 0) >= cap.per_session_cap):
            return Outcome(None, True,
                           f"本会话模型调用已达上限（{cap.per_session_cap} 次），回规则兜底")

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
                raw = await _structured_call(model, schema, messages).ainvoke(messages)
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
            if parsed is None:
                # auto 模式下模型偶尔直接回纯文本 JSON：能过校验就收下
                parsed = _recover_structure(raw, schema)
                if parsed is not None:
                    perr = None
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
            raw = await _structured_call(model, schema, messages).ainvoke(messages)
            usage = getattr(raw.get("raw"), "usage_metadata", None) \
                if isinstance(raw, dict) else None
            # ollama-local 目录无 pricing → record_usage 返回 (None,"免费/价格未配置")
            cost.record_usage(f"{route}#ollama-fallback", LOCAL_FALLBACK_ID,
                              usage, self._pricing.get(LOCAL_FALLBACK_ID))
            parsed = raw.get("parsed") if isinstance(raw, dict) else None
            perr = raw.get("parsing_error") if isinstance(raw, dict) else None
            if parsed is None:
                parsed = _recover_structure(raw, schema)
                if parsed is not None:
                    perr = None
            if parsed is not None and perr is None:
                return Outcome(parsed, True, "", model_id=LOCAL_FALLBACK_ID)
            return None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Key 管理（本地接口；Key 由主进程 safeStorage 加密保存并注入本进程）
    # ------------------------------------------------------------------
    def key_status(self) -> dict:
        """脱敏状态：只回「已配置」+ 尾四位，明文绝不外泄。"""
        key = os.environ.get(KEY_ENV, "").strip()
        if not key:
            return {"configured": False, "masked_tail": None,
                    "source": "env",
                    "detail": "未探测到 Key：本进程环境变量里没有注入，"
                              "当前按规则版运行（设置页可配置）"}
        return {"configured": True, "masked_tail": key[-4:],
                "source": "env",
                "detail": "已配置（仅显示尾四位）：Key 由桌面主进程用系统加密"
                          "保存在本机，重启本地服务后经环境变量注入本进程，"
                          "明文不进日志、不进仓库、不进渲染进程"}

    def delete_key(self) -> dict:
        """删 Key：清掉进程内环境变量注入 + 重置懒加载态 → 立刻回规则版。"""
        had = self._key_present()
        os.environ.pop(KEY_ENV, None)
        self.reset()
        return {"deleted": had,
                "note": "本进程内注入的 Key 已清除并重置模型层，立刻回规则版；"
                        "桌面端随后会删除 userData 里的加密份并重启本地服务，"
                        "旧 Key 不会留在子进程里"}

    async def check_connectivity(self) -> dict:
        """一键连通性检查：401 / 余额 / 网络分别返回，不吞异常。

        会发一次最小真实请求（成本极低）；拿不到 usage 如实标未知。
        """
        state = settings.get_switch()
        if state == settings.SWITCH_OFF:
            return {"status": "switch_off", "ok": False,
                    "detail": "模型开关=关：当前根本不调用模型，Key 未参与检查；"
                              "把开关拨到「自动」或「开」再点一次"}
        if not self._key_present():
            return {"status": "not_configured", "ok": False,
                    "detail": "未配置 Key：先点「配置 / 修改」填入自己的 Key 再检查"}
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
