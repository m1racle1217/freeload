# -*- coding: utf-8 -*-
"""Playwright 浏览器池管理。"""

# ================================
# 导入依赖
# ================================
import asyncio
from typing import Optional

from playwright.async_api import async_playwright, Browser, BrowserContext

from src.auth import AuthManager


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
        self._browsers: list[Browser] = []
        self._contexts: dict[str, BrowserContext] = {}
        self._lock = asyncio.Lock()
        self._available: asyncio.Queue[BrowserContext] = asyncio.Queue()

    # ================================
    # 生命周期管理
    # ================================
    async def start(self) -> None:
        """启动 Playwright 并预创建浏览器实例。"""
        from src.stealth import create_stealth_browser, create_stealth_context

        self._playwright = await async_playwright().start()
        self._auth = AuthManager()
        for _ in range(self._pool_size):
            browser = await create_stealth_browser(self._playwright, headless=self._headless)
            self._browsers.append(browser)
            context = await create_stealth_context(browser)
            await self._available.put(context)
            self._contexts[id(context)] = context
        print(f"🌐 浏览器池已启动 ({self._pool_size} 个实例, stealth 反检测)")

    async def stop(self) -> None:
        """关闭所有浏览器并停止 Playwright。"""
        for ctx in self._contexts.values():
            await ctx.close()
        for browser in self._browsers:
            await browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browsers.clear()
        self._contexts.clear()
        print("🌐 浏览器池已关闭")

    # ================================
    # 上下文获取与释放
    # ================================
    async def acquire(self) -> BrowserContext:
        """获取一个空闲的浏览器上下文（阻塞直到有可用实例）。"""
        context = await self._available.get()
        return context

    async def acquire_for_platform(self, platform: str) -> BrowserContext:
        """获取浏览器上下文并自动注入指定平台的 cookie。"""
        context = await self.acquire()
        if self._auth:
            await self._auth.inject_cookies(context, platform)
        return context

    async def release(self, context: BrowserContext) -> None:
        """归还浏览器上下文到池中。"""
        await self._available.put(context)

    async def create_isolated_context(self) -> BrowserContext:
        """创建独立的浏览器上下文（用于登录等隔离场景）。"""
        if not self._browsers:
            raise RuntimeError("浏览器池未启动，请先调用 start()")
        return await self._browsers[0].new_context()

    # ================================
    # 状态
    # ================================
    async def available_count(self) -> int:
        """返回当前可用实例数量。"""
        return self._available.qsize()

    async def total_count(self) -> int:
        """返回总实例数量。"""
        return len(self._browsers)
