"""deskpet.paths —— 用户数据目录单一入口（v6.1 裁决）

为什么必须有这个模块：
    导出文件曾被放在 desktop/renderer/exports/（被 mount 的静态根），
    /static/exports/{fname} 直接 200，旁路了 /exports 的白名单三道闩
    （Step 0-2）。修复后导出目录、LLM 缓存 db、本地成本累计、三态
    settings 全部统一落用户数据目录，路径拼接只许走本模块，别各写一份。

优先级（v6.1 裁决）：
    1. 显式环境变量 DESKPET_DATA_DIR
       —— 阶段 5 由 Electron 主进程把 app.getPath('userData') 经环境变量
          注入给 FastAPI 子进程（Python 侧拿不到 Electron API）；
       —— 测试用 monkeypatch.setenv 一行覆盖（tmp_path），绝不回落项目内路径。
    2. 平台默认用户数据目录：
       - Windows: %LOCALAPPDATA%\\naiwa-deskpet（不写死盘符/字面量）
       - 其它:    $XDG_DATA_HOME/naiwa-deskpet 或 ~/.local/share/naiwa-deskpet

阶段 3 只做「可注入 + 默认值」，不接 Electron、不改 desktop/main.js。
"""

from __future__ import annotations

import os
from pathlib import Path

# 注入用环境变量名（交付清单已声明；阶段 5 主进程 spawn 时设置）
DESKPET_DATA_DIR_ENV = "DESKPET_DATA_DIR"
_APP_DIRNAME = "naiwa-deskpet"


def get_data_dir() -> Path:
    """用户数据根目录（不存在则创建）。唯一入口，禁止绕过。"""
    explicit = os.environ.get(DESKPET_DATA_DIR_ENV)
    if explicit:
        d = Path(explicit).expanduser()
    else:
        local_appdata = os.environ.get("LOCALAPPDATA")
        if local_appdata:
            d = Path(local_appdata) / _APP_DIRNAME
        else:
            xdg = os.environ.get("XDG_DATA_HOME")
            base = (Path(xdg).expanduser() if xdg
                    else Path.home() / ".local" / "share")
            d = base / _APP_DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_exports_dir() -> Path:
    """导出 docx 落这里（绝不放 desktop/renderer/ 下，防静态根旁路）。"""
    d = get_data_dir() / "exports"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_cache_db_path() -> Path:
    """LLM 缓存 db（阶段 3：LlmGateway 懒加载 enable_llm_cache 用）。"""
    return get_data_dir() / "cache.sqlite"


def get_settings_path() -> Path:
    """三态开关等设置（阶段 3；不混进 SQLite 业务库）。"""
    return get_data_dir() / "settings.json"


def get_cost_log_path() -> Path:
    """本地成本累计落点（阶段 3）。"""
    return get_data_dir() / "cost_log.json"
