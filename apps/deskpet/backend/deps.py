"""apps.deskpet.backend.deps —— 请求依赖（连接 + user_id）

每个请求从池里拿连接、用完关；user_id 从 auth 中间件挂的 state 取。
"""

from __future__ import annotations

from fastapi import Request

from apps.deskpet.backend import storage


def get_conn():
    """拿连接；调用方用完 close。FastAPI Depends 用法：
        def route(conn = Depends(get_conn)):
    但 SQLite 连接轻量，这里直接 yield 模式：
    """
    conn = storage.get_connection()
    storage.init_schema(conn)
    try:
        yield conn
    finally:
        conn.close()


def uid(request: Request) -> str:
    """从 request.state 取 user_id（auth 中间件挂的）。"""
    return request.state.user_id