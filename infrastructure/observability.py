"""公共结构化日志；不依赖 Agent 编排或终端界面。"""

import json
import logging
import sys
from datetime import datetime
from pathlib import Path

from config.settings import settings


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
def setup_logging() -> logging.Logger:
    """
    初始化结构化日志器：
    - 文件（UTF-8，JSON 行）：INFO 级全量记录；
    - 控制台（stderr）：只出 WARNING 及以上——INFO 的 JSON 行不再刷屏。
    """
    log_path: Path = settings.abs_path(settings.log_file)
    logger = logging.getLogger("agent")
    logger.setLevel(logging.INFO)
    # 防止重复初始化时叠加 handler（例如测试中多次调用）
    if logger.handlers:
        return logger

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    logger.addHandler(file_handler)

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.WARNING)
    console.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
    logger.addHandler(console)
    return logger


logger = logging.getLogger("agent")


def log_step(step: str, **fields) -> None:
    """记录一个 ReAct 步骤为 JSON 行（工具名、参数、结果摘要等）。"""
    entry = {"ts": datetime.now().isoformat(timespec="seconds"), "step": step, **fields}
    # 截断超长内容，避免日志爆炸
    for key, value in entry.items():
        if isinstance(value, str) and len(value) > 500:
            entry[key] = value[:500] + "...(截断)"
    logger.info(json.dumps(entry, ensure_ascii=False))


# ---------------------------------------------------------------------------
# 流式输出
# ---------------------------------------------------------------------------
