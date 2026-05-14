# -*- coding: utf-8 -*-
"""Browser pool and persistent platform-session management."""

import asyncio
import logging
from typing import Optional

from playwright.async_api import Browser, BrowserContext, async_playwright

from src.auth import AuthManager

logger = logging.getLogger("freeload")


class BrowserPool:
    """Manage pooled ephemeral contexts plus persistent per-platform sessions."""

    def __init__(self, pool_size: int = 2, headless: bool = True):
        self._pool_size = pool_size
        self._headless = headless
        self._playwright = None
        self._auth: Optional[AuthManager] = None
        self._browsers: list[Browser] = []
        self._contexts: dict[int, BrowserContext] = {}
        self._context_browsers: dict[int, Browser] = {}
        self._available: asyncio.Queue[BrowserContext] = asyncio.Queue()
        self._stopped = False

        self._persistent_contexts: dict[str, BrowserContext] = {}
        self._persistent_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        self._stopped = False
        self._playwright = await async_playwright().start()
        self._auth = AuthManager()

        for platform in ("jd", "taobao", "pdd", "miniapp"):
            self._persistent_locks[platform] = asyncio.Lock()

        for _ in range(self._pool_size):
            browser = await self._create_browser()
            self._browsers.append(browser)
            context = await self._create_context(browser)
            self._track_context(context, browser)
            await self._available.put(context)

        print(f"浏览器池已启动 ({self._pool_size} 个匿名上下文 + 持久平台会话)")

    async def stop(self) -> None:
        self._stopped = True
        await self._clear_available_queue()

        for context in list(self._persistent_contexts.values()):
            try:
                await context.close()
            except Exception:
                pass
        self._persistent_contexts.clear()

        for context in list(self._contexts.values()):
            try:
                await context.close()
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
        print("浏览器池已关闭")

    async def acquire(self) -> BrowserContext:
        while True:
            context = await self._available.get()
            if await self._is_context_healthy(context):
                return context

            await self._discard_context(context)
            replacement = await self._create_replacement_context()
            await self._available.put(replacement)

    async def acquire_for_platform(self, platform: str) -> BrowserContext:
        use_persistent = False
        inject_into_persistent = False
        if self._auth:
            try:
                candidate = self._auth.has_persistent_profile(platform)
                if asyncio.iscoroutine(candidate):
                    candidate = await candidate
                use_persistent = candidate is True
                if not use_persistent and platform == "jd":
                    state = await self._auth.get_saved_session_state(platform)
                    capabilities = state.get("capabilities") or {}
                    use_persistent = bool(state.get("verified") and capabilities.get("web"))
                    inject_into_persistent = use_persistent
            except Exception:
                use_persistent = False

        if use_persistent:
            lock = self._persistent_locks.setdefault(platform, asyncio.Lock())
            await lock.acquire()
            try:
                context = await self._get_persistent_context(platform)
                if inject_into_persistent and self._auth:
                    await self._auth.inject_cookies(context, platform)
                return context
            except Exception:
                lock.release()
                raise

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
        persistent_platform = self._persistent_platform_for_context(context)
        if persistent_platform:
            lock = self._persistent_locks.get(persistent_platform)
            if lock and lock.locked():
                lock.release()
            return

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
        except Exception as exc:
            logger.warning("[BrowserPool] failed to replace unhealthy context: %s", exc)

    async def create_isolated_context(self) -> BrowserContext:
        if not self._browsers:
            raise RuntimeError("浏览器池未启动，请先调用 start()")
        browser = await self._get_connected_browser()
        return await browser.new_context()

    async def _get_persistent_context(self, platform: str) -> BrowserContext:
        context = self._persistent_contexts.get(platform)
        if context is not None and await self._is_persistent_context_healthy(context):
            return context

        if context is not None:
            try:
                await context.close()
            except Exception:
                pass

        if not self._playwright:
            raise RuntimeError("Playwright 未启动")

        from src.stealth import apply_stealth_to_context, get_persistent_context_kwargs

        profile_dir = AuthManager.persistent_profile_dir(platform)
        profile_dir.mkdir(parents=True, exist_ok=True)
        context = await self._playwright.chromium.launch_persistent_context(
            str(profile_dir),
            **get_persistent_context_kwargs(
                headless=self._headless,
                use_system_chrome=True,
            ),
        )
        await apply_stealth_to_context(context)
        self._persistent_contexts[platform] = context
        return context

    def _persistent_platform_for_context(self, context: BrowserContext) -> str:
        for platform, stored in self._persistent_contexts.items():
            if stored is context:
                return platform
        return ""

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
            except Exception as exc:
                last_error = exc
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

    async def _is_persistent_context_healthy(self, context: BrowserContext) -> bool:
        try:
            pages = getattr(context, "pages", []) or []
            if pages:
                return True
            page = await context.new_page()
            await page.close()
            return True
        except Exception:
            return False

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

    async def available_count(self) -> int:
        return self._available.qsize()

    async def total_count(self) -> int:
        return len(self._browsers) + len(self._persistent_contexts)
