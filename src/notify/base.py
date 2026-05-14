# -*- coding: utf-8 -*-
"""Shared notification abstractions."""

from abc import ABC, abstractmethod
from typing import Optional


class NotifyMessage:
    """Notification payload passed to notifier backends."""

    def __init__(
        self,
        subject: str,
        body: str,
        level: str = "info",
        event_type: Optional[str] = None,
    ):
        self.subject = subject
        self.body = body
        self.level = level
        self.event_type = event_type


class BaseNotifier(ABC):
    """Base class for all notification channels."""

    @abstractmethod
    async def send(self, message: NotifyMessage) -> bool:
        """Send a notification and report whether it succeeded."""
        ...
