import asyncio
import unittest
from unittest.mock import AsyncMock, Mock, patch

from src.daemon import Daemon
from src.event import EventQueue
from src.watchers.marketplace_watcher import MarketplaceActivityWatcher


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
    async def test_scan_returns_activity_event_for_logged_in_platform(self):
        page = Mock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.close = AsyncMock()
        page.locator.return_value.inner_text = AsyncMock(return_value="首页 限时秒杀 百亿补贴")

        context = Mock()
        context.new_page = AsyncMock(return_value=page)

        browser_pool = Mock()
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)
        browser_pool.release = AsyncMock()

        watcher = MarketplaceActivityWatcher(
            "pdd",
            EventQueue(),
            browser_pool=browser_pool,
            value_threshold=2.5,
        )
        watcher.auth.load_cookies = AsyncMock(return_value=[{"name": "pdd_user_id"}])
        watcher.auth.has_saved_session = AsyncMock(return_value=True)

        events = await watcher.scan()

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].platform, "pdd")
        self.assertEqual(events[0].event_type, "activity_check")
        self.assertEqual(events[0].value, 2.5)
        self.assertEqual(events[0].data["markers"], ["限时秒杀", "百亿补贴"])
        browser_pool.acquire_for_platform.assert_awaited_once_with("pdd")
        browser_pool.release.assert_awaited_once_with(context)
