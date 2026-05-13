# -*- coding: utf-8 -*-
"""执行引擎 — 消费事件队列，执行实际薅羊毛操作。"""

# ================================
# 导入依赖
# ================================
import asyncio
import logging
from typing import Optional

from src.event import WoolEvent, EventQueue
from src.browser import BrowserPool

logger = logging.getLogger("freeload")


# ================================
# 任务处理器抽象
# ================================
class TaskHandler:
    """各平台各类任务的具体执行逻辑。"""

    async def handle(self, event: WoolEvent) -> dict:
        """执行任务并返回结果。

        Returns:
            {"success": bool, "detail": str, "value": float}
        """
        raise NotImplementedError


# ================================
# 执行引擎
# ================================
class Executor:
    """事件队列消费者。

    不断从 EventQueue 取出优先级最高的事件，
    分配平台对应的 TaskHandler 执行，
    结果记录到 storage，高价值事件触发通知。
    """

    def __init__(self, event_queue: EventQueue, browser_pool: BrowserPool):
        self._event_queue = event_queue
        self._browser_pool = browser_pool
        self._handlers: dict[str, TaskHandler] = {}
        self._running = False
        self._processed_count = 0
        self._success_count = 0
        self._recent_results: list[dict] = []

    # ================================
    # 注册任务处理器
    # ================================
    def register_handler(self, key: str, handler: TaskHandler) -> None:
        """注册一个处理函数。

        key 格式: "platform:event_type"，如 "jd:flash_sale"
        """
        self._handlers[key] = handler

    async def get_handler(self, platform: str, event_type: str) -> Optional[TaskHandler]:
        """获取匹配的处理器。"""
        # 先尝试精确匹配
        handler = self._handlers.get(f"{platform}:{event_type}")
        if handler:
            return handler
        # 再尝试平台通用处理器
        handler = self._handlers.get(f"{platform}:*")
        return handler

    # ================================
    # 主循环
    # ================================
    async def run(self) -> None:
        """消费者主循环。"""
        self._running = True
        logger.info("⚡ 执行引擎已启动")

        while self._running:
            event = await self._event_queue.pop()
            if event is None:
                await asyncio.sleep(1)
                continue

            result = await self._execute_event(event)
            self._processed_count += 1

            if result.get("success"):
                self._success_count += 1

            # ================================
            # 记录最近结果
            # ================================
            record = {
                "id": event.id,
                "platform": event.platform,
                "event_type": event.event_type,
                "title": event.title,
                "value": event.value,
                "success": result.get("success", False),
                "detail": result.get("detail", ""),
            }
            self._recent_results.append(record)
            if len(self._recent_results) > 100:
                self._recent_results.pop(0)

            # ================================
            # 高价值事件日志
            # ================================
            if result.get("success"):
                logger.info(
                    "[执行] ✅ %s - %s (¥%.2f)",
                    event.platform, event.title, event.value,
                )
            else:
                logger.warning(
                    "[执行] ❌ %s - %s: %s",
                    event.platform, event.title, result.get("detail", "未知错误"),
                )

    async def stop(self) -> None:
        """停止执行引擎。"""
        self._running = False

    # ================================
    # 事件执行
    # ================================
    async def _execute_event(self, event: WoolEvent) -> dict:
        """执行单个事件。"""
        handler = await self.get_handler(event.platform, event.event_type)

        if handler is None:
            return {"success": False, "detail": "无可用处理器", "value": 0}

        try:
            return await handler.handle(event)
        except Exception as e:
            logger.exception("[执行] %s/%s 执行异常", event.platform, event.event_type)
            return {"success": False, "detail": str(e), "value": 0}

    # ================================
    # 状态
    # ================================
    def status_info(self) -> dict:
        """返回执行引擎状态。"""
        return {
            "running": self._running,
            "processed": self._processed_count,
            "success": self._success_count,
            "recent": self._recent_results[-20:],
        }
