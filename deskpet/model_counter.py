"""deskpet.model_counter —— 模型调用计数器（硬闸门 2 的可验证证据）

关态 / 无 Key / 降级规则版时必须为 0（阶段 2 恒 0；阶段 3 起
「关」「自动 + 无 Key」也不许有调用，「自动 + 有 Key」才允许涨）。
提供只读调试接口 GET /api/debug/model-calls 读这个计数。

为什么单独一个模块：
    - 关态压根不 import 模型层，计数器从 0 开始；
    - 阶段 3 的模型调用入口（with_structured_output.ainvoke 等）
      必须经过 bump() 计数，否则就是漏了埋点。
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_total = 0
_by_route: dict[str, int] = {}


def bump(route: str = "unknown") -> int:
    """记一次模型调用。关态 / 无 Key 降级时不应被调用。"""
    global _total
    with _lock:
        _total += 1
        _by_route[route] = _by_route.get(route, 0) + 1
    return _total


def total() -> int:
    """总调用次数。关态 / 无 Key 时必须为 0。"""
    with _lock:
        return _total


def by_route() -> dict[str, int]:
    with _lock:
        return dict(_by_route)


def reset() -> None:
    """测试用：重置计数。"""
    global _total
    with _lock:
        _total = 0
        _by_route.clear()