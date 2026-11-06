"""deskpet.settings —— 三态开关 + 成本上限（settings.json）

v6.1 §4.7【A+B】三态开关（全局生效）：
    - off  关：纯规则、零调用、懒加载，不 import langchain / shared.models；
    - auto 自动（默认）：探测到 Key（阶段 3 = 注入环境变量，附录 B）才启用，
      无 Key / Key 失效 / 断网时静默降级规则版并如实标注 degraded；
    - on   开：强制模型，失败给明确原因（401 / 余额 / 网络分别报），不假装成功。

落盘：用户数据目录 settings.json（路径唯一走 deskpet/paths.py），
绝不混进 SQLite 业务库。app.getPath('userData') 是 Electron 主进程 API，
阶段 3 只做「可注入 + 默认值」：DESKPET_DATA_DIR 环境变量 > 平台默认目录；
阶段 5 由主进程把 userData 路径注入进来。

成本上限（v6.1 §4.6）：本地单次/每日成本硬闸，超上限自动降级并显示原因。
不做公网三层限流 / 访问口令（单机 exe 每人一份库，旧口径已作废）。
"""

from __future__ import annotations

import json
from typing import Any

from deskpet import paths

SWITCH_OFF = "off"
SWITCH_AUTO = "auto"
SWITCH_ON = "on"
VALID_STATES = (SWITCH_OFF, SWITCH_AUTO, SWITCH_ON)

# 成本上限默认值（元）。用户可经 settings.json 覆盖；None = 不设上限。
DEFAULT_COST_CAPS: dict[str, Any] = {
    "per_call_yuan": 0.5,   # 单次调用成本超此值 → 降级并显示原因
    "daily_yuan": 2.0,       # 当日累计成本达此值 → 自动降级规则版
}


def _read_raw() -> dict:
    """读 settings.json；缺失/损坏都当作空（设置损坏不阻断主流程）。"""
    try:
        p = paths.get_settings_path()
        if not p.exists():
            return {}
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def load_settings() -> dict:
    """默认值 + 磁盘覆盖（每次现读，改开关无需重启）。"""
    merged: dict[str, Any] = {
        "model_switch": SWITCH_AUTO,
        "cost_caps": dict(DEFAULT_COST_CAPS),
    }
    raw = _read_raw()
    if raw.get("model_switch") in VALID_STATES:
        merged["model_switch"] = raw["model_switch"]
    caps = raw.get("cost_caps")
    if isinstance(caps, dict):
        for k in DEFAULT_COST_CAPS:
            v = caps.get(k)
            if v is None or isinstance(v, (int, float)):
                merged["cost_caps"][k] = v
    return merged


def get_switch() -> str:
    """当前三态开关状态。"""
    return load_settings()["model_switch"]


def set_switch(state: str) -> str:
    """写入开关状态（非法值抛 ValueError → 路由层转 422）。"""
    if state not in VALID_STATES:
        raise ValueError(
            f"非法开关状态 {state!r}，只能取 {VALID_STATES}")
    data = _read_raw()
    data["model_switch"] = state
    paths.get_settings_path().write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return state


def get_cost_caps() -> dict:
    """单次/每日成本上限（元）。"""
    return load_settings()["cost_caps"]
