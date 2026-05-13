# -*- coding: utf-8 -*-
"""Playwright 浏览器池管理。"""

# ================================
# 导入依赖
# ================================
import asyncio
import logging
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext

from src.auth import AuthManager

logger = logging.getLogger("freeload")


# ================================
# 浏览器池
# ================================
class BrowserPool:
    """浏览器实例池。

    预启动多个浏览器实例，按需分配给 Watcher 和 Executor，
    避免反复创建/销毁浏览器带来的性能开销。
    """

    def __init__(self, pool_size: int = 2, headless: bool = True):
        self._pool_size = pool_size
        self._headless = headless
        self._playwright = None
        self._auth: Optional[AuthManager] = None
        self._browsers: list[Browser] = []
        self._contexts: dict[int, BrowserContext] = {}
        self._context_browsers: dict[int, Browser] = {}
        self._lock = asyncio.Lock()
        self._available: asyncio.Queue[BrowserContext] = asyncio.Queue()
        self._stopped = False

    # ================================
    # 生命周期管理
    # ================================
    async def start(self) -> None:
        """启动 Playwright 并预创建浏览器实例。"""
        self._stopped = False
        self._playwright = await async_playwright().start()
        self._auth = AuthManager()
        for _ in range(self._pool_size):
            browser = await self._create_browser()
            self._browsers.append(browser)
            context = await self._create_context(browser)
            await self._available.put(context)
            self._track_context(context, browser)
        print(f"🌐 浏览器池已启动 ({self._pool_size} 个实例, stealth 反检测)")

    async def stop(self) -> None:
        """关闭所有浏览器并停止 Playwright。"""
        self._stopped = True
        await self._clear_available_queue()
        for ctx in list(self._contexts.values()):
            try:
                await ctx.close()
            except Exception:
                pass
        for browser in list(self._browsers):
            try:
                await browser.close()
            except Exception:
                pass
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
        self._browsers.clear()
        self._contexts.clear()
        self._context_browsers.clear()
        self._playwright = None
        print("🌐 浏览器池已关闭")

    # ================================
    # 上下文获取与释放
    # ================================
    async def acquire(self) -> BrowserContext:
        """获取一个空闲的浏览器上下文（阻塞直到有可用实例）。"""
        while True:
            context = await self._available.get()
            if await self._is_context_healthy(context):
                return context

            await self._discard_context(context)
            replacement = await self._create_replacement_context()
            await self._available.put(replacement)

    async def acquire_for_platform(self, platform: str) -> BrowserContext:
        """获取浏览器上下文并自动注入指定平台的 cookie。"""
        context = await self.acquire()
        if not self._auth:
            return context
        try:
            await self._auth.inject_cookies(context, platform)
            return context
        except Exception:
            await self._discard_context(context)
            replacement = await self._create_replacement_context()
            try:
                await self._auth.inject_cookies(replacement, platform)
                return replacement
            except Exception:
                await self._discard_context(replacement)
                raise

    async def release(self, context: BrowserContext) -> None:
        """归还浏览器上下文到池中。"""
        if self._stopped:
            await self._discard_context(context)
            return

        if await self._is_context_healthy(context):
            await self._available.put(context)
            return

        await self._discard_context(context)
        try:
            replacement = await self._create_replacement_context()
            await self._available.put(replacement)
        except Exception as e:
            logger.warning("[BrowserPool] failed to replace unhealthy context: %s", e)

    async def create_isolated_context(self) -> BrowserContext:
        """创建独立的浏览器上下文（用于登录等隔离场景）。"""
        if not self._browsers:
            raise RuntimeError("浏览器池未启动，请先调用 start()")
        browser = await self._get_connected_browser()
        return await browser.new_context()

    async def _create_browser(self) -> Browser:
        from src.stealth import create_stealth_browser

        return await create_stealth_browser(self._playwright, headless=self._headless)

    async def _create_context(self, browser: Browser) -> BrowserContext:
        from src.stealth import create_stealth_context

        return await create_stealth_context(browser)

    def _track_context(self, context: BrowserContext, browser: Browser) -> None:
        self._contexts[id(context)] = context
        self._context_browsers[id(context)] = browser

    async def _create_replacement_context(self) -> BrowserContext:
        last_error: Exception | None = None
        for _ in range(max(1, len(self._browsers) + 1)):
            browser = await self._get_connected_browser()
            try:
                context = await self._create_context(browser)
                self._track_context(context, browser)
                return context
            except Exception as e:
                last_error = e
                await self._discard_browser(browser)

        browser = await self._create_browser()
        self._browsers.append(browser)
        try:
            context = await self._create_context(browser)
        except Exception:
            await self._discard_browser(browser)
            if last_error is not None:
                raise last_error
            raise
        self._track_context(context, browser)
        return context

    async def _get_connected_browser(self) -> Browser:
        for browser in list(self._browsers):
            if await self._is_browser_connected(browser):
                return browser
            await self._discard_browser(browser)

        browser = await self._create_browser()
        self._browsers.append(browser)
        return browser

    @staticmethod
    async def _is_browser_connected(browser: Browser) -> bool:
        try:
            result = browser.is_connected()
            if asyncio.iscoroutine(result):
                result = await result
            return bool(result)
        except Exception:
            return False

    async def _is_context_healthy(self, context: BrowserContext) -> bool:
        if id(context) not in self._contexts:
            return False

        browser = self._context_browsers.get(id(context))
        if browser is not None and not await self._is_browser_connected(browser):
            return False

        page = None
        try:
            page = await context.new_page()
            return True
        except Exception:
            return False
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def _discard_context(self, context: BrowserContext) -> None:
        self._contexts.pop(id(context), None)
        self._context_browsers.pop(id(context), None)
        try:
            await context.close()
        except Exception:
            pass

    async def _discard_browser(self, browser: Browser) -> None:
        if browser in self._browsers:
            self._browsers.remove(browser)

        stale_contexts = [
            context
            for context_id, context in list(self._contexts.items())
            if self._context_browsers.get(context_id) is browser
        ]
        for context in stale_contexts:
            await self._discard_context(context)

        try:
            await browser.close()
        except Exception:
            pass
        await self._purge_untracked_available_contexts()

    async def _purge_untracked_available_contexts(self) -> None:
        retained: list[BrowserContext] = []
        while True:
            try:
                context = self._available.get_nowait()
            except asyncio.QueueEmpty:
                break
            if id(context) in self._contexts:
                retained.append(context)

        for context in retained:
            self._available.put_nowait(context)

    async def _clear_available_queue(self) -> None:
        while True:
            try:
                self._available.get_nowait()
            except asyncio.QueueEmpty:
                break

    # ================================
    # 状态
    # ================================
    async def available_count(self) -> int:
        """返回当前可用实例数量。"""
        return self._available.qsize()

    async def total_count(self) -> int:
        """返回总实例数量。"""
        return len(self._browsers)
