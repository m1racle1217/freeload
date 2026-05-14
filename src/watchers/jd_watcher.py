# -*- coding: utf-8 -*-
"""JD watcher: sign-in, coupon scan, and flash-sale discovery."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from src.auth import AuthManager
from src.event import EventQueue, WoolEvent
from src.watchers.base import BaseWatcher

logger = logging.getLogger("freeload")


class JDWatcher(BaseWatcher):
    """Discover JD opportunities that can later be executed by handlers."""

    def __init__(self, event_queue: EventQueue, poll_interval: int = 30, browser_pool=None):
        super().__init__(event_queue, poll_interval)
        self.platform = "jd"
        self.auth = AuthManager()
        self._browser_pool = browser_pool
        self._last_sign_date: Optional[str] = None

    async def scan(self) -> list[WoolEvent]:
        events: list[WoolEvent] = []
        cookies = await self.auth.load_cookies("jd")
        has_profile = self.auth.has_persistent_profile("jd")

        if (not cookies or not self.auth._has_session_cookie(cookies, "jd")) and not has_profile:
            logger.warning("[京东] 未登录，跳过扫描")
            return events

        try:
            sign_event = await self._check_sign_in(cookies)
            if sign_event:
                events.append(sign_event)

            coupon_events = await self._check_coupons(cookies)
            events.extend(coupon_events)

            flash_events = await self._check_flash_sales(cookies)
            events.extend(flash_events)
        except Exception as exc:
            logger.error("[京东] 扫描异常: %s", exc)

        return events

    async def _check_sign_in(self, cookies: list[dict]) -> Optional[WoolEvent]:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        has_mobile = self.auth.has_required_cookie_group(cookies, "jd", "mobile_sign")
        has_profile = self.auth.has_persistent_profile("jd")

        if not has_mobile and not has_profile:
            # 网页 cookie 需要已验证才能触发签到
            session_state = await self.auth.get_saved_session_state("jd")
            has_verified_web = bool(
                session_state.get("verified")
                and (session_state.get("capabilities") or {}).get("web")
            )
            if not has_verified_web:
                logger.info("[京东] 未找到有效登录态（需要 pt_key/pt_pin 或已验证网页会话），跳过签到检测")
                return None

        if self._last_sign_date == today:
            return None

        self._last_sign_date = today
        return WoolEvent(
            platform="jd",
            event_type="sign_in",
            title="京东每日签到领京豆",
            value=2.0,
            urgency=3,
            url="https://bean.jd.com/beanIndex.action",
            data={"action": "sign_in"},
        )

    async def _check_coupons(self, cookies: list[dict]) -> list[WoolEvent]:
        events: list[WoolEvent] = []
        if self._browser_pool is None:
            logger.info("[京东] 当前缺少可复用浏览器上下文，跳过领券扫描")
            return events

        context = await self._browser_pool.acquire_for_platform("jd")
        try:
            page = await context.new_page()
            try:
                await page.goto(
                    "https://coupon.m.jd.com/",
                    wait_until="domcontentloaded",
                    timeout=15000,
                )
                await page.wait_for_timeout(1200)
                claim_btns = await page.query_selector_all(
                    "button:has-text('领取'), a:has-text('立即领取'), .coupon-get"
                )
                content = await page.content()
                if not claim_btns and "立即领取" not in content and "领取" not in content:
                    return events

                events.append(
                    WoolEvent(
                        platform="jd",
                        event_type="coupon",
                        title=f"京东领券中心发现可领优惠券 ({max(len(claim_btns), 1)} 项)",
                        value=1.0,
                        urgency=5,
                        url="https://coupon.m.jd.com/",
                        data={"source": "coupon_center", "claim_count": len(claim_btns)},
                    )
                )
            finally:
                await page.close()
        except Exception as exc:
            logger.debug("[京东] 领券扫描跳过: %s", exc)
        finally:
            await self._browser_pool.release(context)

        return events

    async def _check_flash_sales(self, cookies: list[dict]) -> list[WoolEvent]:
        events: list[WoolEvent] = []
        configured = self._configured_flash_sale_targets()
        if configured:
            for target in configured:
                url = str(target.get("url", "")).strip()
                if not url:
                    continue
                title = str(target.get("title", "")).strip() or "京东指定秒杀商品"
                value = float(target.get("value", 10.0) or 10.0)
                events.append(
                    WoolEvent(
                        platform="jd",
                        event_type="flash_sale",
                        title=title,
                        value=value,
                        urgency=9,
                        url=url,
                        data={
                            "source": "configured_target",
                            "item_url": url,
                            "purchase_url": url,
                        },
                    )
                )
            return events
        if self._browser_pool is None:
            logger.info("[京东] 当前缺少可复用浏览器上下文，跳过秒杀扫描")
            return events

        context = await self._browser_pool.acquire_for_platform("jd")
        try:
            await self._scan_flash_page(context, events)
        finally:
            await self._browser_pool.release(context)
        return events

    @staticmethod
    def _normalize_jd_url(url: str, *, fallback: str) -> str:
        if not url:
            return fallback
        if url.startswith("//"):
            return f"https:{url}"
        if url.startswith("/"):
            return f"https://miaosha.jd.com{url}"
        return url

    async def _scan_flash_page(self, context, events: list[WoolEvent]) -> None:
        page = await context.new_page()
        try:
            await page.goto(
                "https://miaosha.jd.com/",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(2)

            items = await page.query_selector_all(".miao-item")
            for item in items[:5]:
                try:
                    title_el = await item.query_selector(".item-name")
                    price_el = await item.query_selector(".item-price")
                    link_el = await item.query_selector("a")

                    title = await title_el.inner_text() if title_el else "未知商品"
                    price_text = await price_el.inner_text() if price_el else "0"
                    raw_url = await link_el.get_attribute("href") if link_el else ""

                    try:
                        price = float(price_text.strip().replace("¥", "").replace("楼", ""))
                    except ValueError:
                        price = 0.0

                    item_url = self._normalize_jd_url(raw_url or "", fallback=page.url)
                    events.append(
                        WoolEvent(
                            platform="jd",
                            event_type="flash_sale",
                            title=f"京东秒杀: {title[:30]}",
                            value=max(price * 0.3, 10.0),
                            urgency=8,
                            url=item_url,
                            data={
                                "price": price,
                                "source": "miaosha",
                                "listing_url": page.url,
                                "item_url": item_url,
                                "purchase_url": item_url,
                            },
                        )
                    )
                except Exception:
                    continue
        except Exception as exc:
            logger.debug("[京东] 秒杀检测跳过: %s", exc)
        finally:
            await page.close()

    def _configured_flash_sale_targets(self) -> list[dict]:
        config = getattr(self, "_config", None)
        if config is None or not hasattr(config, "get"):
            return []
        platform_cfg = config.get("platforms", "jd", default={}) or {}
        return list(platform_cfg.get("flash_sale_targets", []) or [])

    def status_info(self) -> dict:
        info = super().status_info()
        info["mode"] = "watcher"
        return info
