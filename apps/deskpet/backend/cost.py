"""apps.deskpet.backend.cost —— 本地成本累计 + 单次/每日上限（v6.1 §4.6）

口径（单机 exe，旧「公网三层限流/访问口令」作废）：
    - 只做本地成本累计：每次真实调用把 usage_metadata 记进
      用户数据目录 cost_log.json（路径唯一走 apps/deskpet/backend/paths.py）；
    - 单次/每日上限由 apps/deskpet/backend/settings.py 提供，超限自动降级并显示原因，
      由 LlmGateway 集中执行，不许静默继续烧钱；
    - 用量拿不到 → 如实记「用量未知」并跳过本次累计，绝不编数字凑成本；
    - 价格未配置（如 ollama-local 免费）→ estimate_cost 返回 None，
      记「免费/价格未配置」，不记 0。

estimate_cost 真实签名（apps/agent/runtime/token_tracker.py:151）：
    estimate_cost(pricing: Optional[Dict[str, float]],
                  input_tokens: int, output_tokens: int) -> Optional[float]
    pricing 从 config/models.json 对应 model 的
    pricing.input_per_mtok / output_per_mtok 取（flash 1.0/4.0、v4-pro 4.5/13.5）。
"""

from __future__ import annotations

import json
import threading
from typing import Any, Optional

from apps.deskpet.backend import clock, paths

_lock = threading.Lock()


def _log_path():
    return paths.get_cost_log_path()


def _load() -> dict:
    """读成本账本；缺失/损坏当作空账本（账本坏了不阻断主流程）。"""
    try:
        p = _log_path()
        if not p.exists():
            return {"entries": []}
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), list):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"entries": []}


def _save(data: dict) -> None:
    _log_path().parent.mkdir(parents=True, exist_ok=True)
    _log_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def record_usage(route: str, model_id: str, usage: Optional[dict],
                 pricing: Optional[dict]) -> tuple[Optional[float], str]:
    """记一次模型调用的真实用量与折算成本。

    参数：
        route    路由名（model_counter 同口径）
        model_id config/models.json 目录 id
        usage    usage_metadata（dict）或 None（拿不到 → 「用量未知」）
        pricing  该模型的 pricing dict 或 None（未配置 → 免费/未知，不记 0）

    返回 (成本元 或 None, 注记)。累计只累加非 None 成本。
    线程安全：整个读-改-写在锁内（桌面单用户，账本很小）。
    """
    with _lock:
        now = clock.real_now()
        if usage is None:
            entry = {
                "at": clock.to_iso(now), "date": now.date().isoformat(),
                "route": route, "model_id": model_id,
                "input_tokens": None, "output_tokens": None,
                "cost_yuan": None, "note": "用量未知，跳过本次累计",
            }
            data = _load()
            data["entries"].append(entry)
            _save(data)
            return None, "用量未知"

        in_tok = int(usage.get("input_tokens") or 0)
        out_tok = int(usage.get("output_tokens") or 0)
        # 懒加载：apps.agent.runtime.token_tracker 顶层不 import langchain，
        # 但保持与模型层一致的懒风格（关态零 import 的闸门在 llm.py）
        from infrastructure.llm.usage import estimate_cost
        cost = estimate_cost(pricing, in_tok, out_tok)
        note = ""
        if cost is None:
            note = "免费/价格未配置，不累计"
        entry = {
            "at": clock.to_iso(now), "date": now.date().isoformat(),
            "route": route, "model_id": model_id,
            "input_tokens": in_tok, "output_tokens": out_tok,
            "cost_yuan": cost, "note": note,
        }
        data = _load()
        data["entries"].append(entry)
        _save(data)
        return cost, note


def today_cost_yuan() -> float:
    """今日累计成本（只算如实记到数字的；「用量未知/免费」不计入）。"""
    today = clock.real_now().date().isoformat()
    with _lock:
        return float(sum(
            e.get("cost_yuan") or 0.0
            for e in _load()["entries"]
            if e.get("date") == today and isinstance(e.get("cost_yuan"), (int, float))))


def today_calls() -> int:
    """今日真实调用次数（含用量未知的，调了就是调了）。"""
    today = clock.real_now().date().isoformat()
    with _lock:
        return sum(1 for e in _load()["entries"] if e.get("date") == today)


def recent_entries(limit: int = 20) -> list[dict]:
    """最近 n 条账本（调试接口用；不含任何 Key 信息，账本本就不存 Key）。"""
    with _lock:
        return list(_load()["entries"][-limit:])
