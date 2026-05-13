import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from src.browser import BrowserPool
from src.event import WoolEvent
from src.handlers import JDSignInHandler
from src.watchers.jd_watcher import JDWatcher
from src.event import EventQueue


class JDSignInHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_auto_sign_when_mobile_cookies_are_missing(self):
        browser_pool = AsyncMock()
        handler = JDSignInHandler(browser_pool)
        handler._auth.load_cookies = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="sign_in", title="签到", value=2.0)
        )

        self.assertFalse(result["success"])
        self.assertIn("pt_key/pt_pin", result["detail"])
        browser_pool.acquire_for_platform.assert_not_called()

    async def test_waits_on_jd_challenge_page_before_closing(self):
        browser_pool = AsyncMock()
        handler = JDSignInHandler(browser_pool)
        handler._auth.load_cookies = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                {"name": "pt_key", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pt_pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )
        handler._wait_for_manual_challenge = AsyncMock(return_value=False)  # type: ignore[method-assign]

        context = AsyncMock()
        page = AsyncMock()
        page.url = "https://www.jd.com/"
        page.content = AsyncMock(return_value="")
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)
        handler._auth._safe_page_text = AsyncMock(  # type: ignore[attr-defined]
            return_value="验证一下 购物无忧 快速验证"
        )

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="sign_in", title="签到", value=2.0)
        )

        self.assertFalse(result["success"])
        self.assertIn("快速验证", result["detail"])
        handler._wait_for_manual_challenge.assert_awaited_once()
        page.close.assert_awaited_once()
        browser_pool.release.assert_awaited_once_with(context)


class JDWatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_sign_event_is_skipped_without_mobile_sign_cookies(self):
        watcher = JDWatcher(EventQueue(), browser_pool=AsyncMock())
        watcher.auth.load_cookies = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )

        event = await watcher._check_sign_in(
            [
                {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )

        self.assertIsNone(event)


class BrowserPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_recreates_context_when_cookie_injection_fails(self):
        pool = BrowserPool()
        stale = AsyncMock()
        fresh = AsyncMock()
        browser = Mock()
        browser.close = AsyncMock()
        browser.is_connected.return_value = True

        pool._auth = AsyncMock()  # type: ignore[attr-defined]
        pool._auth.inject_cookies = AsyncMock(  # type: ignore[attr-defined]
            side_effect=[RuntimeError("driver closed"), None]
        )
        pool._browsers = [browser]
        pool._contexts = {id(stale): stale}
        pool._available = AsyncMock()
        pool.acquire = AsyncMock(return_value=stale)  # type: ignore[method-assign]
        pool._create_context = AsyncMock(return_value=fresh)  # type: ignore[method-assign]

        context = await pool.acquire_for_platform("jd")

        self.assertIs(context, fresh)
        stale.close.assert_awaited()
        pool._create_context.assert_awaited_once_with(browser)
        self.assertEqual(pool._auth.inject_cookies.await_count, 2)  # type: ignore[attr-defined]

    async def test_recreates_browser_when_replacement_browser_is_disconnected(self):
        pool = BrowserPool()
        stale = AsyncMock()
        fresh_context = AsyncMock()
        disconnected_browser = Mock()
        disconnected_browser.close = AsyncMock()
        replacement_browser = Mock()
        replacement_browser.close = AsyncMock()

        disconnected_browser.is_connected.return_value = False
        replacement_browser.is_connected.return_value = True

        pool._auth = AsyncMock()  # type: ignore[attr-defined]
        pool._auth.inject_cookies = AsyncMock(  # type: ignore[attr-defined]
            side_effect=[RuntimeError("driver closed"), None]
        )
        pool._browsers = [disconnected_browser]
        pool._contexts = {id(stale): stale}
        pool.acquire = AsyncMock(return_value=stale)  # type: ignore[method-assign]
        pool._create_browser = AsyncMock(return_value=replacement_browser)  # type: ignore[method-assign]
        pool._create_context = AsyncMock(return_value=fresh_context)  # type: ignore[method-assign]

        context = await pool.acquire_for_platform("jd")

        self.assertIs(context, fresh_context)
        disconnected_browser.close.assert_awaited()
        pool._create_browser.assert_awaited_once()
        pool._create_context.assert_awaited_once_with(replacement_browser)
        self.assertEqual(pool._browsers, [replacement_browser])

    async def test_release_discards_closed_context_and_queues_replacement(self):
        pool = BrowserPool()
        closed_context = AsyncMock()
        fresh_context = AsyncMock()
        browser = Mock()
        browser.close = AsyncMock()

        closed_context.pages = []
        closed_context.new_page.side_effect = RuntimeError("Target page, context or browser has been closed")
        browser.is_connected.return_value = True

        pool._browsers = [browser]
        pool._contexts = {id(closed_context): closed_context}
        pool._available = asyncio.Queue()
        pool._create_context = AsyncMock(return_value=fresh_context)  # type: ignore[method-assign]

        await pool.release(closed_context)

        queued = await pool._available.get()
        self.assertIs(queued, fresh_context)
        closed_context.close.assert_awaited()
