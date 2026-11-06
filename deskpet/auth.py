"""deskpet.auth —— 认证与身份（口令管进门，UUID 管你是谁，两层分开）

两层分开：
    - 口令：门禁，所有人共用，失败连门都进不了；
    - UUID：身份，每人一个，决定数据隔离边界；
    - 口令泄露换一个不影响数据归属；UUID 丢了数据找不回（demo 可接受）。

需求文档如实写「演示级隔离，不含账号体系」。

口令来源：环境变量 DESKPET_ACCESS_TOKEN（不进前端/仓库/日志/PPT）。
支持多口令（逗号分隔，团队成员各一个）轮换。
"""

from __future__ import annotations

import hmac
import os
import re
import uuid
from typing import Optional

HEADER_ACCESS_TOKEN = "X-Access-Token"
HEADER_USER_ID = "X-User-Id"

# UUID v4 格式（防伪造/注入）
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE)


# ===========================================================================
# 口令层（进门）
# ===========================================================================
def _valid_tokens() -> set[str]:
    """从环境变量读有效口令集合。支持逗号分隔多口令。"""
    raw = os.environ.get("DESKPET_ACCESS_TOKEN", "")
    return {t.strip() for t in raw.split(",") if t.strip()}


def verify_access_token(token: str | None) -> bool:
    """校验访问口令。常量时间比较防时序攻击。

    demo 口令未配置时放行（便于本地开发）；上线前必须配置。
    """
    tokens = _valid_tokens()
    if not tokens:
        return True  # 未配置口令：放行（demo 友好）
    if not token:
        return False
    return any(hmac.compare_digest(token, t) for t in tokens)


# ===========================================================================
# 身份层（你是谁）
# ===========================================================================
def extract_user_id(headers: dict) -> Optional[str]:
    """从请求头提取 user_id。做 UUID 格式校验防伪造。"""
    raw = headers.get(HEADER_USER_ID) or headers.get(HEADER_USER_ID.lower())
    if not raw:
        return None
    val = str(raw).strip()
    if not _UUID_RE.match(val):
        return None  # 格式不对，当 없处理
    return val


def ensure_user_id(headers: dict) -> str:
    """提取或生成 user_id。首次访问生成 UUID，前端存 localStorage 带回。"""
    existing = extract_user_id(headers)
    if existing:
        return existing
    return str(uuid.uuid4())


# ===========================================================================
# 中间件装配
# ===========================================================================
async def auth_middleware(request, call_next):
    """FastAPI 中间件：先验口令（进门），再取/建 UUID（你是谁）。

    流程：
      1. 取 X-Access-Token → verify_access_token() → 失败返 401；
      2. 取 X-User-Id → UUID 格式校验，不通过/没有则生成；
      3. 挂 request.state.user_id，路由从这取身份做隔离。
    """
    from fastapi.responses import JSONResponse
    from deskpet.storage import get_connection, log_access, init_schema

    token = request.headers.get(HEADER_ACCESS_TOKEN)
    if not verify_access_token(token):
        conn = get_connection()
        init_schema(conn)
        log_access(conn, granted=False, ip_hint=request.client.host
                   if request.client else "")
        conn.close()
        return JSONResponse(status_code=401, content={"detail": "访问口令无效"})

    user_id = ensure_user_id(dict(request.headers))
    request.state.user_id = user_id

    response = await call_next(request)
    response.headers[HEADER_USER_ID] = user_id
    return response