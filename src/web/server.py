# -*- coding: utf-8 -*-
"""FastAPI web management UI that shares daemon state."""

import asyncio
import base64
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

logger = logging.getLogger("freeload")

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
PLATFORM_NAMES = {
    "jd": "京东",
    "taobao": "淘宝",
    "pdd": "拼多多",
    "miniapp": "品牌小程序",
}


def _login_state_view(state: dict[str, object]) -> dict[str, object]:
    """Normalize login state for template rendering."""
    return {
        "logged_in": bool(state.get("logged_in")),
        "verified": bool(state.get("verified")),
        "label": state.get("label") or ("已登录" if state.get("logged_in") else "未登录"),
        "detail": state.get("detail") or "",
        "cookie_count": int(state.get("cookie_count") or 0),
    }


def _watcher_state_view(daemon, platform: str) -> dict[str, object]:
    watcher = daemon.get_watcher(platform) if hasattr(daemon, "get_watcher") else None
    cfg = daemon.config.get("platforms", platform, default={}) or {}
    status = watcher.status_info() if watcher is not None else {}
    return {
        "registered": watcher is not None,
        "enabled": bool(cfg.get("enabled", True)),
        "running": bool(status.get("enabled", False)) if watcher is not None else False,
        "mode": status.get("mode", "watcher") if watcher is not None else "watcher",
        "scan_count": int(status.get("scan_count", 0)) if watcher is not None else 0,
        "error_count": int(status.get("error_count", 0)) if watcher is not None else 0,
    }


def create_app(daemon) -> FastAPI:
    """Create a FastAPI app instance bound to the daemon."""
    app = FastAPI(title="Wool Hunter", version="1.0.0")
    templates = Jinja2Templates(directory=str(TEMPLATE_DIR))

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="dashboard.html",
            context={"title": "仪表盘", "daemon": daemon},
        )

    @app.get("/platforms", response_class=HTMLResponse)
    async def platforms_page(request: Request):
        login_states: dict[str, dict[str, object]] = {}
        watcher_states: dict[str, dict[str, object]] = {}
        for platform in PLATFORM_NAMES:
            login_states[platform] = _login_state_view(
                await daemon.auth.get_saved_session_state(platform)
            )
            watcher_states[platform] = _watcher_state_view(daemon, platform)
        return templates.TemplateResponse(
            request=request,
            name="platforms.html",
            context={
                "title": "平台状态",
                "daemon": daemon,
                "login_states": login_states,
                "watcher_states": watcher_states,
            },
        )

    @app.get("/config", response_class=HTMLResponse)
    async def config_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="config.html",
            context={"title": "配置", "daemon": daemon},
        )

    @app.get("/logs", response_class=HTMLResponse)
    async def logs_page(request: Request):
        return templates.TemplateResponse(
            request=request,
            name="logs.html",
            context={"title": "日志", "daemon": daemon},
        )

    @app.get("/api/status")
    async def api_status():
        watchers_info = [watcher.status_info() for watcher in daemon.get_watchers()]
        executor_info = daemon.get_executor().status_info()
        return {
            "running": daemon._running,
            "watchers": watchers_info,
            "executor": executor_info,
            "now": datetime.now(timezone.utc).isoformat(),
        }

    @app.get("/api/earnings")
    async def api_earnings():
        today = await daemon.storage.get_today_earnings()
        total = await daemon.storage.get_total_earnings()
        return {"today": today, "total": total}

    @app.get("/api/events")
    async def api_events(limit: int = 50):
        db_tasks = await daemon.storage.get_recent_tasks(limit=limit)
        mem_tasks = daemon.get_executor().status_info().get("recent", [])
        merged: dict[str, dict[str, Any]] = {}
        for task in db_tasks:
            merged[task.get("id", "")] = task
        for task in mem_tasks:
            merged[task.get("id", "")] = task
        events = sorted(
            merged.values(),
            key=lambda item: item.get("created_at", ""),
            reverse=True,
        )
        return {"events": events[:limit]}

    @app.post("/api/platforms/{platform}/run")
    async def api_run_platform_action(platform: str, request: Request):
        if platform != "jd":
            return JSONResponse({"ok": False, "error": "当前仅支持京东手动执行真实动作"}, status_code=400)
        try:
            payload = await request.json()
            action = str(payload.get("action", "")).strip()
            action_map = {
                "sign_in": {"event_type": "sign_in", "title": "京东手动签到", "value": 2.0},
                "coupon": {"event_type": "coupon", "title": "京东手动领券", "value": 1.0},
            }
            if action not in action_map:
                return JSONResponse({"ok": False, "error": f"不支持的动作: {action}"}, status_code=422)
            spec = action_map[action]
            from src.event import WoolEvent

            result = await daemon.get_executor()._execute_event(  # type: ignore[attr-defined]
                WoolEvent(
                    platform="jd",
                    event_type=spec["event_type"],
                    title=spec["title"],
                    value=spec["value"],
                    urgency=7,
                )
            )
            return JSONResponse({"ok": True, "result": result})
        except Exception as exc:
            logger.exception("running platform action failed")
            return JSONResponse({"ok": False, "error": f"执行失败: {exc}"}, status_code=500)

    @app.get("/api/config")
    async def api_get_config():
        return daemon.config.to_form_payload()

    @app.post("/api/platforms/{platform}/enabled")
    async def api_set_platform_enabled(platform: str, request: Request):
        if platform not in PLATFORM_NAMES:
            return JSONResponse({"ok": False, "error": f"未知平台: {platform}"}, status_code=400)
        try:
            payload = await request.json()
            enabled = bool(payload.get("enabled"))
            state = await daemon.set_platform_enabled(platform, enabled)
            return JSONResponse({"ok": True, "state": state})
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        except Exception as exc:
            logger.exception("updating platform enabled failed")
            return JSONResponse({"ok": False, "error": f"更新失败: {exc}"}, status_code=500)

    @app.post("/api/config")
    async def api_save_config(request: Request):
        try:
            payload = await request.json()
            daemon.config.save_update(payload)
            if hasattr(daemon, "reload_config"):
                daemon.reload_config()
            return JSONResponse(
                {
                    "ok": True,
                    "message": "配置已保存。为确保所有任务使用新配置，请重启 daemon。",
                    "config": daemon.config.to_form_payload(),
                }
            )
        except ValueError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
        except FileNotFoundError as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
        except Exception as exc:
            logger.exception("saving config failed")
            return JSONResponse({"ok": False, "error": f"保存失败: {exc}"}, status_code=500)

    @app.get("/api/stream")
    async def api_stream():
        async def event_generator():
            while True:
                status = {
                    "watchers": [watcher.status_info() for watcher in daemon.get_watchers()],
                    "executor": daemon.get_executor().status_info(),
                }
                yield f"data: {json.dumps(status)}\n\n"
                await asyncio.sleep(3)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    login_sessions: dict[str, dict[str, Any]] = {}

    @app.get("/login/{platform}", response_class=HTMLResponse)
    async def login_page(request: Request, platform: str):
        if platform not in PLATFORM_NAMES:
            return templates.TemplateResponse(
                request=request,
                name="login.html",
                context={
                    "title": "登录",
                    "platform": platform,
                    "platform_name": platform,
                    "daemon": daemon,
                },
                status_code=404,
            )
        return templates.TemplateResponse(
            request=request,
            name="login.html",
            context={
                "title": f"{PLATFORM_NAMES.get(platform, platform)} 登录",
                "platform": platform,
                "platform_name": PLATFORM_NAMES.get(platform, platform),
                "daemon": daemon,
            },
        )

    @app.post("/api/login/{platform}/start")
    async def api_login_start(platform: str):
        if platform not in PLATFORM_NAMES:
            return JSONResponse({"error": f"未知平台: {platform}"}, status_code=400)

        session_id = f"{platform}_{int(datetime.now().timestamp())}"
        try:
            from playwright.async_api import async_playwright

            from src.stealth import (
                _detect_available_channel,
                create_stealth_browser,
                create_stealth_context,
            )

            playwright = await async_playwright().start()
            login_url = daemon.auth._platform_domain(platform)
            fallbacks = daemon.auth._fallback_urls(platform)
            urls_to_try = [login_url] + fallbacks
            use_system = platform in {"taobao", "pdd"} and bool(_detect_available_channel())
            browser = await create_stealth_browser(
                playwright,
                headless=False,
                use_system_chrome=use_system,
            )
            context = await create_stealth_context(browser)
            page = await context.new_page()
            opened_url = login_url
            last_error = ""
            for url in urls_to_try:
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    opened_url = url
                    break
                except Exception as exc:
                    last_error = str(exc)
            else:
                raise RuntimeError(last_error or f"无法打开 {platform} 登录页")

            await asyncio.sleep(2)

            screenshot = await page.screenshot(type="png")
            qr_code = base64.b64encode(screenshot).decode()

            login_sessions[session_id] = {
                "playwright": playwright,
                "browser": browser,
                "context": context,
                "page": page,
                "opened_url": opened_url,
                "started_at": datetime.now(timezone.utc),
                "logged_in": False,
                "platform": platform,
            }

            return JSONResponse({"session_id": session_id, "qr_code": qr_code})
        except Exception as exc:
            error_message = str(exc)
            logger.warning("[Web登录] %s 启动失败: %s", platform, error_message)
            try:
                if "playwright" in locals():
                    try:
                        if "browser" in locals():
                            await browser.close()
                    except Exception:
                        pass
                    await playwright.stop()
            except Exception:
                pass
            is_blocked = (
                "ERR_CONNECTION_CLOSED" in error_message
                or "ERR_CONNECTION_REFUSED" in error_message
            )
            cli_cmd = daemon.auth.cli_login_commands(platform)["repo_root"]
            hint = (
                f"{platform} 拦截了当前浏览器会话。请改用可见浏览器登录：{daemon.auth.cli_login_hint(platform)}"
                if is_blocked
                else f"登录失败: {error_message}"
            )
            return JSONResponse(
                {"error": hint, "cli_cmd": cli_cmd, "blocked": is_blocked},
                status_code=500,
            )

    @app.get("/api/login/{platform}/status")
    async def api_login_status(platform: str, session_id: str = Query(...)):
        session = login_sessions.get(session_id)
        if not session:
            return JSONResponse({"error": "会话已过期"}, status_code=404)

        try:
            context = session["context"]
            probe = await daemon.auth.inspect_context_login(
                platform,
                context,
                preferred_page=session.get("page"),
            )

            if probe["confirmed"]:
                await daemon.auth._save_cookies(
                    platform,
                    probe["cookies"],
                    metadata=probe["metadata"],
                )
                session["logged_in"] = True
                asyncio.create_task(_cleanup_session(session_id))
                return JSONResponse({"logged_in": True})

            page = probe.get("page") or session["page"]
            screenshot = await page.screenshot(type="png")
            qr_code = base64.b64encode(screenshot).decode()
            elapsed = int(
                (datetime.now(timezone.utc) - session["started_at"]).total_seconds()
            )
            return JSONResponse(
                {
                    "logged_in": False,
                    "qr_code": qr_code,
                    "elapsed": elapsed,
                    "page_url": probe.get("page_url", ""),
                    "challenge": daemon.auth.login_challenge_reason(
                        platform,
                        page_url=str(probe.get("page_url", "")),
                        page_text=str(probe.get("page_text", "")),
                    ),
                }
            )
        except Exception as exc:
            logger.warning("[Web登录] 状态检查异常: %s", exc)
            return JSONResponse({"error": str(exc)}, status_code=500)

    async def _cleanup_session(session_id: str):
        await asyncio.sleep(2)
        session = login_sessions.pop(session_id, None)
        if not session:
            return
        try:
            await session["page"].close()
            await session["context"].close()
            await session["browser"].close()
            await session["playwright"].stop()
        except Exception:
            pass

    return app
