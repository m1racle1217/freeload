# -*- coding: utf-8 -*-
"""FastAPI Web 管理面板 — 与 daemon 共享状态。"""

# ================================
# 导入依赖
# ================================
import json
import asyncio
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone

# ================================
# 确保能找到 src 包
# ================================
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger("freeload")


# ================================
# 模板路径
# ================================
TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"


# ================================
# 创建 FastAPI 应用
# ================================
def create_app(daemon) -> FastAPI:
    """创建 Web 应用实例，注入 daemon 引用。"""
    app = FastAPI(title="Wool Hunter", version="1.0.0")

    # 模板引擎
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    # ================================
    # 仪表盘页面
    # ================================
    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return templates.TemplateResponse(
            "dashboard.html",
            {
                "request": request,
                "title": "仪表盘",
                "daemon": daemon,
            },
        )

    # ================================
    # 平台状态页面
    # ================================
    @app.get("/platforms", response_class=HTMLResponse)
    async def platforms_page(request: Request):
        return templates.TemplateResponse(
            "platforms.html",
            {
                "request": request,
                "title": "平台状态",
                "daemon": daemon,
            },
        )

    # ================================
    # 配置页面
    # ================================
    @app.get("/config", response_class=HTMLResponse)
    async def config_page(request: Request):
        return templates.TemplateResponse(
            "config.html",
            {
                "request": request,
                "title": "配置",
                "config_preview": json.dumps(
                    daemon.config.to_dict(), ensure_ascii=False, indent=2
                ),
            },
        )

    # ================================
    # 日志页面
    # ================================
    @app.get("/logs", response_class=HTMLResponse)
    async def logs_page(request: Request):
        return templates.TemplateResponse(
            "logs.html",
            {
                "request": request,
                "title": "日志",
                "daemon": daemon,
            },
        )

    # ================================
    # API: 运行状态
    # ================================
    @app.get("/api/status")
    async def api_status():
        watchers_info = [w.status_info() for w in daemon.get_watchers()]
        executor_info = daemon.get_executor().status_info()

        return {
            "running": daemon._running,
            "watchers": watchers_info,
            "executor": executor_info,
            "now": datetime.now(timezone.utc).isoformat(),
        }

    # ================================
    # API: 今日收益
    # ================================
    @app.get("/api/earnings")
    async def api_earnings():
        today = await daemon.storage.get_today_earnings()
        total = await daemon.storage.get_total_earnings()
        return {"today": today, "total": total}

    # ================================
    # API: 最近事件
    # ================================
    @app.get("/api/events")
    async def api_events(limit: int = 50):
        tasks = await daemon.storage.get_recent_tasks(limit=limit)
        return {"events": tasks}

    # ================================
    # SSE: 实时状态推送
    # ================================
    @app.get("/api/stream")
    async def api_stream():
        async def event_generator():
            while True:
                status = {
                    "watchers": [w.status_info() for w in daemon.get_watchers()],
                    "executor": daemon.get_executor().status_info(),
                }
                yield f"data: {json.dumps(status)}\n\n"
                await asyncio.sleep(3)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    return app
