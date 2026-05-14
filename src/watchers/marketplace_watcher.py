# -*- coding: utf-8 -*-
"""Explicit placeholder watchers for non-JD marketplaces."""

import logging

from src.event import EventQueue, WoolEvent
from src.watchers.base import BaseWatcher

logger = logging.getLogger("freeload")


class MarketplaceActivityWatcher(BaseWatcher):
    """Disabled-by-design watcher until a platform has a real earning action."""

    def __init__(
        self,
        platform: str,
        event_queue: EventQueue,
        poll_interval: int = 60,
        browser_pool=None,
        value_threshold: float = 1.0,
    ):
        super().__init__(event_queue, poll_interval)
        self.platform = platform
        self._browser_pool = browser_pool
        self._value_threshold = value_threshold

    async def scan(self) -> list[WoolEvent]:
        logger.info("[%s] 暂无真实自动薅羊毛动作，跳过扫描", self.platform)
        return []

    def status_info(self) -> dict:
        info = super().status_info()
        info["mode"] = "disabled_until_real_action"
        return info
