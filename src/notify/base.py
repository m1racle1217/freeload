# -*- coding: utf-8 -*-
"""通知基类 — 通知渠道的通用抽象。"""

# ================================
# 导入依赖
# ================================
from abc import ABC, abstractmethod
from typing import Optional


# ================================
# 通知消息
# ================================
class NotifyMessage:
    """通知消息体。"""

    def __init__(
        self,
        subject: str,
        body: str,
        level: str = "info",
        event_type: Optional[str] = None,
    ):
        self.subject = subject
        self.body = body
        self.level = level        # info / warning / success / critical
        self.event_type = event_type


# ================================
# 通知器基类
# ================================
class BaseNotifier(ABC):
    """通知器基类，所有通知渠道继承此类。"""

    @abstractmethod
    async def send(self, message: NotifyMessage) -> bool:
        """发送通知，返回是否成功。"""
        ...
