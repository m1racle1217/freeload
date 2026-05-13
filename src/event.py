# -*- coding: utf-8 -*-
"""羊毛事件模型与优先级事件队列。"""

# ================================
# 导入依赖
# ================================
import uuid
import heapq
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# ================================
# 羊毛事件定义
# ================================
@dataclass(order=False)
class WoolEvent:
    """一次羊毛机会的完整描述，包含价值、紧急度、目标页面等信息。"""

    # ================================
    # 标识字段
    # ================================
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    platform: str = ""          # 平台标识: jd / taobao / pdd / miniapp
    event_type: str = ""        # 事件类型: flash_sale / coupon / sign_in / points / redpacket

    # ================================
    # 业务字段
    # ================================
    title: str = ""             # 人类可读的描述
    value: float = 0.0          # 预估价值（元），用于排序
    urgency: int = 5            # 紧急度 1-10，越高越优先
    url: str = ""               # 目标页面链接
    data: dict = field(default_factory=dict)  # 附加数据，如订单信息、优惠券码等
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    # ================================
    # 执行状态
    # ================================
    grabbed: bool = False       # 是否已被执行引擎处理


# ================================
# 优先级事件队列
# ================================
class EventQueue:
    """线程安全的优先级事件队列。

    按 (urgency 降序, value 降序, created_at 升序) 排序，
    保证高价值高紧急的事件优先被消费。
    """

    def __init__(self):
        self._heap: list[tuple] = []
        self._seen_ids: set[str] = set()

    async def push(self, event: WoolEvent) -> None:
        """放入事件，自动去重。"""
        if event.id in self._seen_ids:
            return
        self._seen_ids.add(event.id)
        # heapq 是最小堆，取负值实现降序
        heapq.heappush(
            self._heap,
            (-event.urgency, -event.value, event.created_at, event),
        )

    async def pop(self) -> Optional[WoolEvent]:
        """取出优先级最高的事件。队列为空时返回 None。"""
        if not self._heap:
            return None
        _, _, _, event = heapq.heappop(self._heap)
        return event

    async def peek(self) -> Optional[WoolEvent]:
        """查看优先级最高的事件但不取出。"""
        if not self._heap:
            return None
        return self._heap[0][3]

    async def clear(self) -> None:
        """清空队列。"""
        self._heap.clear()
        self._seen_ids.clear()

    async def size(self) -> int:
        """返回当前队列中的事件数量。"""
        return len(self._heap)

    async def deduplicate(self) -> None:
        """清理已处理事件的 ID 记录，释放内存。"""
        self._seen_ids.clear()
        # 重建 seen_ids 只保留仍在堆中的事件
        for _, _, _, event in self._heap:
            self._seen_ids.add(event.id)
