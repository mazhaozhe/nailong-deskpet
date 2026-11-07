"""apps.deskpet.backend.app —— FastAPI 启动入口

阶段 3：接模型层（方案 B 收敛版）。挂 router + auth 中间件 + 静态文件。
模型可用走 LlmGateway（apps/deskpet/backend/llm.py 唯一入口，懒加载），
关态 / 无 Key / 超上限一律降级规则版，degraded 如实标注。
启动：python -m apps.deskpet.backend.app  或  uvicorn apps.deskpet.backend.app:app
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.deskpet.backend.api import cleanup_expired_exports, download_router, router
from apps.deskpet.backend.auth import auth_middleware


def create_app() -> FastAPI:
    app = FastAPI(title="桌宠 · 软件工程作业")
    app.middleware("http")(auth_middleware)
    app.include_router(router)
    # P0-1：导出文件下载路由（/exports/{filename}，白名单校验在 api.py）
    app.include_router(download_router)

    # Step 0-2：启动时清理过期导出物（expires_at 真生效，不假装过期）
    cleanup_expired_exports()

    # 静态文件（前端页 + 预置通知 JSON）。
    # 注意：导出目录不在 static 下（Step 0-2 移到用户数据目录 apps/deskpet/backend/paths.py），
    # /static/exports/* 一律 404，白名单三道闩不再被旁路。
    static_dir = Path(__file__).resolve().parents[1] / "desktop" / "renderer"
    # 阶段 5：奶龙素材只有 apps/deskpet/desktop/assets/nailong 这一份；经只读静态挂载
    # 提供给 FastAPI 主体页，Electron 与浏览器共用，不复制第二套帧。
    nailong_dir = Path(__file__).resolve().parents[1] / "desktop" / "assets" / "nailong"
    app.mount("/static/nailong", StaticFiles(directory=str(nailong_dir)), name="nailong")
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/")
    async def index():
        return FileResponse(str(static_dir / "index.html"))

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn
    # 单机 exe 口径：只绑本地回环（brief 第七节；禁止 0.0.0.0）
    uvicorn.run(app, host="127.0.0.1", port=8000)