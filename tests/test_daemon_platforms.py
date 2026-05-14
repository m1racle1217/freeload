import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from src.daemon import Daemon
from src.event import EventQueue
from src.watchers.marketplace_watcher import MarketplaceActivityWatcher
from src.watchers.jd_watcher import JDWatcher


class MemoryConfig:
    def __init__(self):
        self._data = {
            "browser": {"pool_size": 1, "headless": True},
            "notify": {"email": {}},
            "platforms": {
                "jd": {"enabled": True, "poll_interval": 30, "value_threshold": 1.0},
                "taobao": {"enabled": False, "poll_interval": 60, "value_threshold": 1.0},
                "pdd": {"enabled": False, "poll_interval": 60, "value_threshold": 1.0},
                "miniapp": {"enabled": False, "poll_interval": 300, "value_threshold": 0.5},
            },
        }

    def load(self):
        pass

    def update_platform_enabled(self, platform: str, enabled: bool):
        self._data.setdefault("platforms", {}).setdefault(platform, {})["enabled"] = bool(enabled)

    def get(self, *keys, default=None):
        value = self._data
        for key in keys:
            value = value.get(key) if isinstance(value, dict) else None
        return default if value is None else value


class DaemonPlatformRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.created_tasks = []

    async def test_registers_enabled_marketplace_watchers(self):
        daemon = self._daemon()

        await daemon._register_watchers()

        platforms = {watcher.platform for watcher in daemon.get_watchers()}
        self.assertEqual(platforms, {"jd"})

    async def test_registers_handlers_for_enabled_marketplaces(self):
        daemon = self._daemon()

        daemon._register_handlers()

        handlers = daemon.executor._handlers
        self.assertIn("taobao:activity_check", handlers)
        self.assertIn("pdd:activity_check", handlers)

    async def test_set_runtime_enabled_toggles_executor_and_watchers(self):
        daemon = self._daemon()
        await daemon._register_watchers()
        daemon._register_handlers()
        daemon.executor._running = True

        state = await daemon.set_runtime_enabled(False)

        self.assertFalse(state["enabled"])
        self.assertFalse(daemon.executor.status_info()["running"])
        watcher = daemon.get_watcher("jd")
        self.assertIsNotNone(watcher)
        self.assertFalse(watcher.enabled)

    async def test_set_runtime_enabled_true_restarts_executor_task_when_stopped(self):
        daemon = self._daemon()
        await daemon._register_watchers()
        daemon._register_handlers()
        daemon.executor._running = False
        created_before = len(self.created_tasks)

        state = await daemon.set_runtime_enabled(True)

        self.assertTrue(state["enabled"])
        self.assertTrue(daemon.executor.status_info()["running"])
        self.assertGreater(len(self.created_tasks), created_before)

    async def test_set_runtime_enabled_true_registers_missing_enabled_watchers(self):
        daemon = self._daemon()
        daemon.config._data["platforms"]["jd"]["enabled"] = True
        daemon._watchers.clear()
        daemon._watchers_by_platform.clear()
        daemon.executor._running = False

        state = await daemon.set_runtime_enabled(True)

        self.assertTrue(state["enabled"])
        self.assertIsNotNone(daemon.get_watcher("jd"))

    async def test_set_platform_enabled_registers_marketplace_watcher_when_missing(self):
        daemon = self._daemon()
        await daemon._register_watchers()

        watcher = daemon.get_watcher("taobao")
        self.assertIsNone(watcher)

        state = await daemon.set_platform_enabled("taobao", True)

        self.assertTrue(state["enabled"])
        self.assertTrue(state["watcher_registered"])
        self.assertIsNotNone(daemon.get_watcher("taobao"))

    def _daemon(self):
        with patch("src.daemon.Config", return_value=MemoryConfig()):
            daemon = Daemon()
        daemon.browser_pool = Mock()
        self.created_tasks.clear()

        def capture_task(coro):
            self.created_tasks.append(coro)
            coro.close()
            return Mock()

        self.addCleanup(self._close_created_tasks)
        patcher = patch.object(asyncio, "create_task", side_effect=capture_task)
        patcher.start()
        self.addCleanup(patcher.stop)
        return daemon

    def _close_created_tasks(self):
        for coro in self.created_tasks:
            try:
                coro.close()
            except Exception:
                pass


class MarketplaceActivityWatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_scan_returns_empty_until_real_action_is_implemented(self):
        watcher = MarketplaceActivityWatcher(
            "pdd",
            EventQueue(),
            browser_pool=Mock(),
            value_threshold=2.5,
        )

        events = await watcher.scan()

        self.assertEqual(events, [])


class JDWatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_flash_sale_scan_returns_empty_without_browser_pool(self):
        watcher = JDWatcher(EventQueue(), browser_pool=None)
        with patch("playwright.async_api.async_playwright", side_effect=AssertionError("should not launch")):
            events = await watcher._check_flash_sales(
                [
                    {"name": "pt_key", "value": "x", "domain": ".jd.com", "path": "/"},
                    {"name": "pt_pin", "value": "y", "domain": ".jd.com", "path": "/"},
                ]
            )

        self.assertEqual(events, [])

    async def test_coupon_scan_returns_event_when_claim_entry_exists(self):
        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.close = AsyncMock()
        page.content = AsyncMock(return_value="立即领取 满200减20")
        page.query_selector_all = AsyncMock(return_value=[AsyncMock(), AsyncMock()])

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)

        browser_pool = AsyncMock()
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)
        browser_pool.release = AsyncMock()

        watcher = JDWatcher(EventQueue(), browser_pool=browser_pool)

        events = await watcher._check_coupons(
            [
                {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, "coupon")
        self.assertIn("领券", events[0].title)
        browser_pool.acquire_for_platform.assert_awaited_once_with("jd")
        browser_pool.release.assert_awaited_once_with(context)

    async def test_coupon_scan_returns_empty_without_browser_pool(self):
        watcher = JDWatcher(EventQueue(), browser_pool=None)
        with patch("playwright.async_api.async_playwright", side_effect=AssertionError("should not launch")):
            events = await watcher._check_coupons(
                [
                    {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                    {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
                ]
            )

        self.assertEqual(events, [])

    async def test_flash_sale_scan_prefers_item_link_over_listing_url(self):
        title_el = AsyncMock()
        title_el.inner_text = AsyncMock(return_value="iPhone 绉掓潃涓撳満")
        price_el = AsyncMock()
        price_el.inner_text = AsyncMock(return_value="1999")
        link_el = AsyncMock()
        link_el.get_attribute = AsyncMock(return_value="//item.jd.com/123.html")

        item = AsyncMock()
        item.query_selector = AsyncMock(
            side_effect=[title_el, price_el, link_el]
        )

        page = AsyncMock()
        page.goto = AsyncMock()
        page.close = AsyncMock()
        page.url = "https://miaosha.jd.com/"
        page.query_selector_all = AsyncMock(return_value=[item])

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)

        watcher = JDWatcher(EventQueue(), browser_pool=AsyncMock())
        events = []

        await watcher._scan_flash_page(context, events)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].url, "https://item.jd.com/123.html")
        self.assertEqual(events[0].data["purchase_url"], "https://item.jd.com/123.html")
        self.assertEqual(events[0].data["listing_url"], "https://miaosha.jd.com/")

    async def test_flash_sale_scan_emits_configured_targets_before_page_scan(self):
        browser_pool = AsyncMock()
        watcher = JDWatcher(EventQueue(), browser_pool=browser_pool)
        watcher._configured_flash_sale_targets = lambda: [  # type: ignore[attr-defined]
            {
                "title": "指定秒杀商品",
                "url": "https://item.jd.com/888.html",
                "value": 66.0,
            }
        ]

        events = await watcher._check_flash_sales(
            [
                {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].url, "https://item.jd.com/888.html")
        self.assertEqual(events[0].event_type, "flash_sale")
        browser_pool.acquire_for_platform.assert_not_called()
