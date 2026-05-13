# -*- coding: utf-8 -*-
"""京东监控器 — 签到、领券、秒杀检测。"""

# ================================
# 导入依赖
# ================================
import logging
import asyncio
from datetime import datetime, timezone
from typing import Optional

from playwright.async_api import async_playwright

from src.event import WoolEvent, EventQueue
from src.watchers.base import BaseWatcher
from src.auth import AuthManager

logger = logging.getLogger("freeload")


# ================================
# 京东 Watcher
# ================================
class JDWatcher(BaseWatcher):
    """京东平台监控器。

    扫描以下羊毛机会：
    - 每日签到（京豆）
    - 领券中心可领优惠券
    - 限时秒杀/促销活动
    - 东东农场/红包等日常活动
    """

    def __init__(self, event_queue: EventQueue, poll_interval: int = 30,
                 browser_pool=None):
        super().__init__(event_queue, poll_interval)
        self.platform = "jd"
        self.auth = AuthManager()
        self._browser_pool = browser_pool
        self._last_sign_date: Optional[str] = None

    # ================================
    # 主扫描
    # ================================
    async def scan(self) -> list[WoolEvent]:
        """执行一次完整扫描，返回发现的羊毛事件。"""
        events: list[WoolEvent] = []
        cookies = await self.auth.load_cookies("jd")

        if not cookies:
            logger.warning("[京东] 未登录，跳过扫描")
            return events

        try:
            # ================================
            # 任务 1: 每日签到
            # ================================
            sign_event = await self._check_sign_in(cookies)
            if sign_event:
                events.append(sign_event)

            # ================================
            # 任务 2: 领券中心
            # ================================
            coupon_events = await self._check_coupons(cookies)
            events.extend(coupon_events)

            # ================================
            # 任务 3: 秒杀检测
            # ================================
            flash_events = await self._check_flash_sales(cookies)
            events.extend(flash_events)

        except Exception as e:
            logger.error("[京东] 扫描异常: %s", e)

        return events

    # ================================
    # 每日签到
    # ================================
    async def _check_sign_in(self, cookies: list[dict]) -> Optional[WoolEvent]:
        """检测今日是否已签到，未签到则返回签到事件。"""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        # 今天已经签到过了
        if self._last_sign_date == today:
            return None

        event = WoolEvent(
            platform="jd",
            event_type="sign_in",
            title="京东每日签到领京豆",
            value=2.0,          # 平均 2 京豆 ≈ 0.02 元，但这是基础任务
            urgency=3,
            url="https://bean.m.jd.com/bean/signIndex.action",
            data={"action": "sign_in"},
        )
        self._last_sign_date = today
        return event

    # ================================
    # 领券检测
    # ================================
    async def _check_coupons(self, cookies: list[dict]) -> list[WoolEvent]:
        """检测可领取的优惠券。"""
        events: list[WoolEvent] = []
        # TODO: 使用 Playwright 访问领券中心，提取可领券列表
        # 当前版本通过简单 URL 检测，后续可扩展为完整的浏览器自动化
        return events

    # ================================
    # 秒杀检测
    # ================================
    async def _check_flash_sales(self, cookies: list[dict]) -> list[WoolEvent]:
        """检测限时秒杀活动。"""
        events: list[WoolEvent] = []

        # 优先使用 BrowserPool，否则独立启动 stealth 浏览器
        if self._browser_pool is not None:
            context = await self._browser_pool.acquire_for_platform("jd")
            try:
                await self._scan_flash_page(context, events)
            finally:
                await self._browser_pool.release(context)
        else:
            from src.stealth import create_stealth_browser, create_stealth_context
            from playwright.async_api import async_playwright
            async with async_playwright() as p:
                browser = await create_stealth_browser(p, headless=True)
                context = await create_stealth_context(browser, inject_cookies=cookies)
                try:
                    await self._scan_flash_page(context, events)
                finally:
                    await context.close()
                    await browser.close()

        return events

    async def _scan_flash_page(self, context, events: list) -> None:
        """在指定 context 中扫描秒杀页面。"""
        page = await context.new_page()
        try:
            await page.goto(
                "https://miaosha.jd.com/",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(2)

            try:
                items = await page.query_selector_all(".miao-item")
                for item in items[:5]:
                    title_el = await item.query_selector(".item-name")
                    price_el = await item.query_selector(".item-price")

                    title = await title_el.inner_text() if title_el else "未知商品"
                    price_text = await price_el.inner_text() if price_el else "0"

                    try:
                        price = float(price_text.strip().replace("¥", ""))
                    except ValueError:
                        price = 0

                    event = WoolEvent(
                        platform="jd",
                        event_type="flash_sale",
                        title=f"京东秒杀: {title[:30]}",
                        value=max(price * 0.3, 10.0),
                        urgency=8,
                        url=page.url,
                        data={"price": price, "source": "miaosha"},
                    )
                    events.append(event)
            except Exception:
                pass
        except Exception as e:
            logger.debug("[京东] 秒杀检测跳过: %s", e)
        finally:
            await page.close()
