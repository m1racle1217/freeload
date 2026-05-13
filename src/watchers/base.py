# -*- coding: utf-8 -*-
"""Watcher 基类 — 各平台监控器的通用抽象。"""

# ================================
# 导入依赖
# ================================
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Optional

from src.event import WoolEvent, EventQueue
from src.auth import AuthManager

logger = logging.getLogger("freeload")


# ================================
# Watcher 基类
# ================================
class BaseWatcher(ABC):
    """平台监控器基类。

    每个平台继承此类，实现 scan() 方法扫描羊毛机会，
    将发现的 WoolEvent 推入全局事件队列。

    通过 run() 主循环持续监控，永不退出。
    """

    def __init__(self, event_queue: EventQueue, poll_interval: int = 60):
        self.event_queue = event_queue
        self.poll_interval = poll_interval
        self.platform: str = "unknown"
        self._enabled: bool = True
        self._last_scan_time: Optional[float] = None
        self._scan_count: int = 0
        self._error_count: int = 0

    # ================================
    # 属性
    # ================================
    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, value: bool) -> None:
        self._enabled = value

    @property
    def last_scan_time(self) -> Optional[float]:
        return self._last_scan_time

    # ================================
    # 主循环
    # ================================
    async def run(self) -> None:
        """Watcher 主循环 — 由 daemon 启动。

        持续循环，每次调用 scan() 扫描羊毛，
        发现事件推入队列，出错则静默恢复。
        """
        logger.info("[%s] Watcher 启动，轮询间隔 %ds", self.platform, self.poll_interval)

        while True:
            if not self._enabled:
                await asyncio.sleep(5)
                continue

            try:
                events = await self.scan()
                self._scan_count += 1
                self._last_scan_time = asyncio.get_event_loop().time()

                for event in events:
                    await self.event_queue.push(event)
                    if event.value > 0:
                        logger.info(
                            "[%s] 发现羊毛: %s (¥%.2f, 紧急度%d)",
                            self.platform, event.title, event.value, event.urgency,
                        )

            except Exception as e:
                self._error_count += 1
                logger.error("[%s] 扫描异常 (#%d): %s", self.platform, self._error_count, e)

            await asyncio.sleep(self.poll_interval)

    # ================================
    # 子类实现
    # ================================
    @abstractmethod
    async def scan(self) -> list[WoolEvent]:
        """扫描平台当前可用的羊毛机会。

        子类必须实现此方法，返回 WoolEvent 列表。
        无可用羊毛时返回空列表。
        """
        ...

    # ================================
    # Cookie 管理
    # ================================
    async def ensure_login(self, auth: AuthManager) -> bool:
        """确保平台已登录，返回是否已登录。"""
        cookies = await auth.load_cookies(self.platform)
        return cookies is not None and len(cookies) > 0

    # ================================
    # 状态报告
    # ================================
    def status_info(self) -> dict:
        """返回 Watcher 当前状态信息，供 Web 面板使用。"""
        return {
            "platform": self.platform,
            "enabled": self._enabled,
            "poll_interval": self.poll_interval,
            "last_scan_time": self._last_scan_time,
            "scan_count": self._scan_count,
            "error_count": self._error_count,
        }
