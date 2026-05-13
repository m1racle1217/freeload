# -*- coding: utf-8 -*-
"""FastAPI Web 管理面板 — 与 daemon 共享状态。"""

# ================================
# 导入依赖
# ================================
import json
import asyncio
import base64
import sys
import logging
from pathlib import Path
from datetime import datetime, timezone

# ================================
# 确保能找到 src 包
# ================================
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from typing import Optional

from fastapi import FastAPI, Request, Query
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
            request=request,
            name="dashboard.html",
            context={
                "title": "仪表盘",
                "daemon": daemon,
            },
        )

    # ================================
    # 平台状态页面
    # ================================
    @app.get("/platforms", response_class=HTMLResponse)
    async def platforms_page(request: Request):
        # 检查各平台 cookie 状态
        cookie_status = {}
        for p in ["jd", "taobao", "pdd", "miniapp"]:
            cookies = await daemon.auth.load_cookies(p)
            cookie_status[p] = cookies is not None
        return templates.TemplateResponse(
            request=request,
            name="platforms.html",
            context={
                "title": "平台状态",
                "daemon": daemon,
                "cookie_status": cookie_status,
            },
        )

    # ================================
    # 配置页面
    # ================================
    @app.get("/config", response_class=HTMLResponse)
    async def config_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="config.html",
            context={
                "title": "配置",
                "daemon": daemon,
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
            request=request,
            name="logs.html",
            context={
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
        # 数据库记录 + executor 内存记录合并
        db_tasks = await daemon.storage.get_recent_tasks(limit=limit)
        mem_tasks = daemon.get_executor().status_info().get("recent", [])
        # 去重：以 id 为 key，内存中的覆盖数据库的
        merged: dict[str, dict] = {}
        for t in db_tasks:
            merged[t.get("id", "")] = t
        for t in mem_tasks:
            merged[t.get("id", "")] = t
        events = sorted(merged.values(), key=lambda x: x.get("created_at", ""), reverse=True)
        return {"events": events[:limit]}

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

    # ================================
    # 登录会话存储
    # ================================
    _login_sessions: dict[str, dict] = {}

    # ================================
    # 登录页面
    # ================================
    PLATFORM_NAMES = {"jd": "京东", "taobao": "淘宝", "pdd": "拼多多", "miniapp": "品牌小程序"}

    @app.get("/login/{platform}", response_class=HTMLResponse)
    async def login_page(request: Request, platform: str):
        """渲染登录页面。"""
        if platform not in PLATFORM_NAMES:
            return templates.TemplateResponse(
                request=request, name="login.html",
                context={"title": "登录", "platform": platform,
                         "platform_name": platform, "daemon": daemon},
                status_code=404,
            )
        return templates.TemplateResponse(
            request=request, name="login.html",
            context={"title": f"{PLATFORM_NAMES.get(platform, platform)} 登录",
                     "platform": platform,
                     "platform_name": PLATFORM_NAMES.get(platform, platform),
                     "daemon": daemon},
        )

    # ================================
    # API: 开始登录
    # ================================
    @app.post("/api/login/{platform}/start")
    async def api_login_start(platform: str):
        """启动浏览器并捕获登录页面/二维码。"""
        session_id = f"{platform}_{int(datetime.now().timestamp())}"

        try:
            from playwright.async_api import async_playwright

            p = await async_playwright().start()
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                ],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            page = await context.new_page()
            # 隐藏 webdriver 特征
            await page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")

            login_url = daemon.auth._platform_domain(platform)
            await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(2)

            # 截取页面截图（包含二维码）
            screenshot = await page.screenshot(type="png")
            qr_b64 = base64.b64encode(screenshot).decode()

            _login_sessions[session_id] = {
                "playwright": p, "browser": browser,
                "context": context, "page": page,
                "started_at": datetime.now(timezone.utc),
                "logged_in": False, "platform": platform,
            }

            return JSONResponse({"session_id": session_id, "qr_code": qr_b64})

        except Exception as e:
            logger.warning("[Web登录] %s 启动失败: %s", platform, e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ================================
    # API: 登录状态检测
    # ================================
    @app.get("/api/login/{platform}/status")
    async def api_login_status(platform: str, session_id: str = Query(...)):
        """检测登录状态，返回最新二维码。"""
        session = _login_sessions.get(session_id)
        if not session:
            return JSONResponse({"error": "会话已过期"})

        try:
            page = session["page"]
            context = session["context"]

            # 检测 cookie
            cookies = await context.cookies()
            has_session = daemon.auth._has_session_cookie(cookies, platform)

            if has_session:
                # 登录成功，保存 cookie
                await daemon.auth._save_cookies(platform, cookies)
                session["logged_in"] = True
                # 清理
                asyncio.create_task(_cleanup_session(session_id))
                return JSONResponse({"logged_in": True})

            # 刷新截图
            screenshot = await page.screenshot(type="png")
            qr_b64 = base64.b64encode(screenshot).decode()
            elapsed = int((datetime.now(timezone.utc) - session["started_at"]).total_seconds())

            return JSONResponse({
                "logged_in": False,
                "qr_code": qr_b64,
                "elapsed": elapsed,
            })

        except Exception as e:
            logger.warning("[Web登录] 状态检测异常: %s", e)
            return JSONResponse({"error": str(e)})

    # ================================
    # 异步清理登录会话
    # ================================
    async def _cleanup_session(session_id: str):
        """延迟清理登录会话。"""
        await asyncio.sleep(2)
        session = _login_sessions.pop(session_id, None)
        if session:
            try:
                await session["page"].close()
                await session["context"].close()
                await session["browser"].close()
                await session["playwright"].stop()
            except Exception:
                pass

    return app
