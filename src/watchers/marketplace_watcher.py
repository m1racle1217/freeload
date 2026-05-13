# -*- coding: utf-8 -*-
"""Marketplace watchers for Taobao and Pinduoduo activity checks."""

import logging
from typing import Any

from src.auth import AuthManager
from src.event import EventQueue, WoolEvent
from src.watchers.base import BaseWatcher

logger = logging.getLogger("freeload")


PLATFORM_ACTIVITY_CONFIG: dict[str, dict[str, Any]] = {
    "taobao": {
        "name": "淘宝",
        "url": "https://www.taobao.com/",
        "title": "淘宝活动入口巡检",
        "markers": ("聚划算", "百亿补贴", "限时秒杀", "淘宝直播", "购物车"),
    },
    "pdd": {
        "name": "拼多多",
        "url": "https://mobile.yangkeduo.com/",
        "title": "拼多多活动入口巡检",
        "markers": ("限时秒杀", "百亿补贴", "充值中心", "免费领水果", "补贴多人团"),
    },
}


class MarketplaceActivityWatcher(BaseWatcher):
    """Scan a marketplace landing page and enqueue a lightweight activity check."""

    def __init__(
        self,
        platform: str,
        event_queue: EventQueue,
        poll_interval: int = 60,
        browser_pool=None,
        value_threshold: float = 1.0,
    ):
        super().__init__(event_queue, poll_interval)
        if platform not in PLATFORM_ACTIVITY_CONFIG:
            raise ValueError(f"Unsupported marketplace platform: {platform}")
        self.platform = platform
        self.auth = AuthManager()
        self._browser_pool = browser_pool
        self._value_threshold = value_threshold
        self._config = PLATFORM_ACTIVITY_CONFIG[platform]

    async def scan(self) -> list[WoolEvent]:
        cookies = await self.auth.load_cookies(self.platform)
        if not cookies or not await self.auth.has_saved_session(self.platform):
            logger.warning("[%s] 未登录，跳过扫描", self._config["name"])
            return []

        context = await self._browser_pool.acquire_for_platform(self.platform)
        try:
            page = await context.new_page()
            try:
                await page.goto(
                    self._config["url"],
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                await page.wait_for_timeout(1200)
                text = await page.locator("body").inner_text(timeout=3000)
                matched = [
                    marker
                    for marker in self._config["markers"]
                    if marker in text
                ][:5]
            except Exception as exc:
                logger.debug("[%s] 活动检测跳过: %s", self._config["name"], exc)
                return []
            finally:
                await page.close()
        finally:
            await self._browser_pool.release(context)

        value = max(float(self._value_threshold), 1.0)
        event = WoolEvent(
            platform=self.platform,
            event_type="activity_check",
            title=self._config["title"],
            value=value,
            urgency=4,
            url=self._config["url"],
            data={"markers": matched, "source": "marketplace_activity"},
        )
        return [event]

    def status_info(self) -> dict:
        info = super().status_info()
        info["mode"] = "activity_check"
        return info
