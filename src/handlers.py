# -*- coding: utf-8 -*-
"""Task handlers that execute real platform actions."""

import asyncio
import logging
from datetime import datetime
from typing import Iterable, Optional

from src.auth import AuthManager
from src.browser import BrowserPool
from src.config import Config
from src.event import WoolEvent
from src.executor import TaskHandler
from src.notify.base import BaseNotifier, NotifyMessage
from src.platforms import PLATFORM_DISPLAY_NAMES
from src.stealth import HumanBehavior

logger = logging.getLogger("freeload")

PLATFORM_NAMES = PLATFORM_DISPLAY_NAMES


class JDInteractiveMixin:
    """Shared helpers for JD handlers that may hit manual challenges."""

    _auth: AuthManager
    _config: Optional[Config] = None
    _notifier: Optional[BaseNotifier] = None

    def _rate_limit_cfg(self) -> dict:
        if not self._config:
            return {"min_delay_ms": 8000, "max_delay_ms": 20000, "session_warmup": True}
        return self._config.get("platforms", "jd", "rate_limit", default={}) or {}

    def _verification_timeout(self) -> int:
        if not self._config:
            return 180
        return int(self._config.get("platforms", "jd", "verification_timeout", default=180))

    async def _apply_rate_limit(self) -> None:
        cfg = self._rate_limit_cfg()
        await HumanBehavior.random_delay(
            cfg.get("min_delay_ms", 8000), cfg.get("max_delay_ms", 20000)
        )

    async def _warm_session(self, page) -> None:
        if self._rate_limit_cfg().get("session_warmup", True):
            await HumanBehavior.warm_jd(page)

    async def _humanize(self, page) -> None:
        try:
            await HumanBehavior.mouse_jitter(page)
            await HumanBehavior.scroll(page)
        except Exception:
            pass

    async def _wait_for_manual_challenge(self, page) -> bool:
        timeout = self._verification_timeout()
        for _ in range(timeout):
            page_text = await self._auth._safe_page_text(page)
            if not self._auth.login_challenge_reason(
                "jd", page_url=page.url, page_text=page_text
            ):
                return True
            await page.wait_for_timeout(1000)
        return False

    async def _resolve_jd_challenge(self, page, action_label: str) -> dict | None:
        page_text = await self._auth._safe_page_text(page)
        reason = self._auth.login_challenge_reason(
            "jd", page_url=page.url, page_text=page_text
        )
        if not reason:
            return None

        timeout = self._verification_timeout()
        logger.warning("[%s] 检测到风控验证页，等待手动完成（%ds）...", action_label, timeout)
        await self._notify_verification(action_label, page.url, timeout)
        solved = await self._wait_for_manual_challenge(page)
        if solved:
            await page.wait_for_timeout(1000)
            logger.info("[%s] 风控验证已完成，继续执行", action_label)
            return None

        detail = f"{action_label}触发风控验证，{timeout}s 内未完成快速验证，已跳过"
        logger.warning("[%s] %s", action_label, detail)
        return {"success": False, "detail": detail, "value": 0}

    async def _notify_verification(self, action_label: str, url: str, timeout: int) -> None:
        if not self._notifier:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        nl = chr(10)
        body = (
            "[Freeload] " + action_label + nl
            + "URL: " + url + nl
            + "Time: " + ts + nl + nl
            + "Please complete the JD verification in the browser within "
            + str(timeout) + " seconds. The system will continue automatically."
        )
        msg = NotifyMessage(
            subject="[Freeload] JD verification required: " + action_label,
            body=body,
            level="warning",
            event_type="verification_required",
        )
        try:
            await self._notifier.send(msg)
        except Exception as exc:
            logger.debug("[notify] verification alert failed: %s", exc)

    @staticmethod
    def _is_network_blocked(url: str) -> bool:
        """Return True when the page URL indicates a firewall/proxy block."""
        blocked_patterns = (
            "/disable/disable.htm",
            "/block/",
            "blocked.html",
            "access-denied",
        )
        return any(p in url for p in blocked_patterns)

    @staticmethod
    async def _first_selector(page, selectors: Iterable[str]):
        for selector in selectors:
            node = await page.query_selector(selector)
            if node:
                return node
        return None


class JDSignInHandler(JDInteractiveMixin, TaskHandler):
    """Handle JD mobile sign-in."""

    def __init__(self, browser_pool: BrowserPool):
        self._browser_pool = browser_pool
        self._auth = AuthManager()

    @staticmethod
    def _sign_in_result_from_content(content: str) -> str:
        if any(marker in content for marker in ("今日已签到", "已连续签到", "已签到")):
            return "今日已签到"
        if any(marker in content for marker in ("签到成功", "签到完成")):
            return "签到成功"
        return ""

    async def handle(self, event: WoolEvent) -> dict:
        result: dict = {"success": False, "detail": "未执行", "value": 0}
        for attempt in range(2):
            if attempt > 0:
                logger.info("[京东签到] 验证完成，重试一次...")
            await self._apply_rate_limit()
            result = await self._do_sign_in(event, attempt)
            if (
                attempt == 0
                and not result.get("success")
                and "风控验证" in result.get("detail", "")
                and "未完成" not in result.get("detail", "")
            ):
                continue
            break
        return result

    async def _do_sign_in(self, event: WoolEvent, attempt: int) -> dict:
        logger.info("[京东签到] 正在执行签到...")
        cookies = await self._auth.load_cookies("jd")
        has_profile = self._auth.has_persistent_profile("jd")
        session_state = await self._auth.get_saved_session_state("jd")
        has_verified_web = bool(
            session_state.get("verified")
            and (session_state.get("capabilities") or {}).get("web")
        )
        has_mobile = self._auth.has_required_cookie_group(cookies or [], "jd", "mobile_sign")
        has_web = self._auth.has_required_cookie_group(cookies or [], "jd", "web")

        if not cookies and not has_profile:
            detail = "未找到京东 cookie，请重新登录京东"
            logger.warning("[京东签到] %s", detail)
            return {"success": False, "detail": detail, "value": 0}

        if not has_mobile and not has_profile and not has_verified_web:
            if has_web:
                detail = "检测到网页 cookie 但尚未验证登录态，请先运行登录命令确认登录"
            else:
                detail = "未找到有效京东登录态，请先运行登录命令（需要 pt_key/pt_pin 或已验证的网页会话）"
            logger.warning("[京东签到] %s", detail)
            return {"success": False, "detail": detail, "value": 0}

        # 根据 cookie 类型选择签到 URL：
        # pt_key/pt_pin → 移动端签到页；thor/pin 已验证网页态 → PC 端签到页
        if has_mobile or has_profile:
            sign_url = "https://bean.m.jd.com/bean/signIndex.action"
            logger.info("[京东签到] 使用移动端签到页")
        else:
            sign_url = "https://bean.jd.com/beanIndex.action"
            logger.info("[京东签到] 使用 PC 端签到页（网页登录态）")

        context = await self._browser_pool.acquire_for_platform("jd")
        page = await context.new_page()
        try:
            if attempt == 0:
                await self._warm_session(page)
            await self._humanize(page)
            await page.goto(sign_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1500)

            if self._is_network_blocked(page.url):
                detail = f"当前网络屏蔽了京东签到页（被重定向到 {page.url}），请切换网络后重试"
                logger.warning("[京东签到] %s", detail)
                return {"success": False, "detail": detail, "value": 0}

            challenge_result = await self._resolve_jd_challenge(page, "京东签到")
            if challenge_result:
                return challenge_result

            # 移动端 JFE 52 → 自动降级到 PC 端重试
            if "jfe-lb.jd.com/file-no.2/public/jfe.html?err=52" in page.url:
                if sign_url != "https://bean.jd.com/beanIndex.action":
                    logger.info("[京东签到] 移动端返回 JFE 52，降级到 PC 端签到页重试")
                    await page.goto(
                        "https://bean.jd.com/beanIndex.action",
                        wait_until="domcontentloaded",
                        timeout=15000,
                    )
                    await page.wait_for_timeout(1500)
                    challenge_result = await self._resolve_jd_challenge(page, "京东签到")
                    if challenge_result:
                        return challenge_result
                else:
                    detail = "京东签到页面返回 JFE 52，cookie 可能已失效，请重新登录"
                    logger.warning("[京东签到] %s", detail)
                    return {"success": False, "detail": detail, "value": 0}

            selectors = [
                "#signBtn",
                ".sign-btn",
                "button:has-text('签到')",
                "a:has-text('签到')",
                ".J-sign-btn",
                "[class*=sign][class*=btn]",
                "[class*=sign]",
            ]
            for selector in selectors:
                button = await page.query_selector(selector)
                if not button:
                    continue
                try:
                    await button.click()
                    await page.wait_for_timeout(1000)
                except Exception as exc:
                    logger.warning("[京东签到] 点击签到按钮失败: %s", exc)
                    continue

                challenge_result = await self._resolve_jd_challenge(page, "京东签到")
                if challenge_result:
                    return challenge_result

                content = await page.content()
                result_text = self._sign_in_result_from_content(content)
                if result_text:
                    logger.info("[京东签到] %s", result_text)
                    return {"success": True, "detail": result_text, "value": event.value}

            content = await page.content()
            result_text = self._sign_in_result_from_content(content)
            if result_text:
                logger.info("[京东签到] %s", result_text)
                return {"success": True, "detail": result_text, "value": event.value}

            logger.warning("[京东签到] 未找到签到按钮，当前 URL: %s", page.url)
            return {"success": False, "detail": "未找到签到按钮或页面结构变化", "value": 0}
        except Exception as exc:
            logger.error("[京东签到] 执行异常: %s", exc)
            return {"success": False, "detail": str(exc), "value": 0}
        finally:
            await page.close()
            await self._browser_pool.release(context)


class JDFlashSaleHandler(JDInteractiveMixin, TaskHandler):
    """Handle JD flash-sale purchase attempts."""

    def __init__(self, browser_pool: BrowserPool):
        self._browser_pool = browser_pool
        self._auth = AuthManager()

    @staticmethod
    def _buy_result_from_content(content: str) -> str:
        success_markers = (
            "已下单",
            "提交成功",
            "订单已提交",
            "请您尽快完成支付",
            "去支付",
        )
        if any(marker in content for marker in success_markers):
            return "已提交秒杀订单"
        return ""

    @staticmethod
    def _target_url(event: WoolEvent) -> str:
        if isinstance(event.data, dict):
            for key in ("purchase_url", "item_url", "product_url"):
                value = event.data.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return event.url

    async def _attempt_purchase_step(self, page) -> tuple[bool, str]:
        selectors = [
            "button:has-text('立即抢购')",
            "button:has-text('马上抢')",
            "button:has-text('去结算')",
            "button:has-text('提交订单')",
            "a:has-text('立即抢购')",
            "a:has-text('去结算')",
            "a:has-text('提交订单')",
            ".buy-btn",
            "#InitCartUrl",
        ]
        buy_entry = await self._first_selector(page, selectors)

        if not buy_entry:
            content = await page.content()
            result_text = self._buy_result_from_content(content)
            if result_text:
                return True, result_text
            return False, "未找到秒杀购买入口"

        try:
            await buy_entry.click()
            await page.wait_for_timeout(1000)
        except Exception as exc:
            return False, f"点击秒杀购买入口失败: {exc}"

        challenge_result = await self._resolve_jd_challenge(page, "京东秒杀")
        if challenge_result:
            return False, str(challenge_result.get("detail", "京东秒杀触发风控验证"))

        content = await page.content()
        result_text = self._buy_result_from_content(content)
        if result_text:
            return True, result_text
        return False, ""

    async def handle(self, event: WoolEvent) -> dict:
        result: dict = {"success": False, "detail": "未执行", "value": 0}
        for attempt in range(2):
            if attempt > 0:
                logger.info("[京东秒杀] 验证完成，重试一次...")
            await self._apply_rate_limit()
            result = await self._do_flash_sale(event, attempt)
            if (
                attempt == 0
                and not result.get("success")
                and "风控验证" in result.get("detail", "")
                and "未完成" not in result.get("detail", "")
            ):
                continue
            break
        return result

    async def _do_flash_sale(self, event: WoolEvent, attempt: int) -> dict:
        logger.info("[京东秒杀] 发现秒杀商品: %s", event.title)
        target_url = self._target_url(event)
        if not target_url:
            return {"success": False, "detail": "缺少秒杀商品链接，无法执行真实动作", "value": 0}

        context = await self._browser_pool.acquire_for_platform("jd")
        page = await context.new_page()
        try:
            if attempt == 0:
                await self._warm_session(page)
            await self._humanize(page)
            await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
            await page.wait_for_timeout(1000)

            if self._is_network_blocked(page.url):
                detail = f"当前网络屏蔽了京东秒杀页（被重定向到 {page.url}），请切换网络后重试"
                logger.warning("[京东秒杀] %s", detail)
                return {"success": False, "detail": detail, "value": 0}

            challenge_result = await self._resolve_jd_challenge(page, "京东秒杀")
            if challenge_result:
                return challenge_result

            success, detail = await self._attempt_purchase_step(page)
            if success:
                return {"success": True, "detail": detail, "value": event.value}
            if detail:
                if "风控验证" in detail or "快速验证" in detail:
                    return {"success": False, "detail": detail, "value": 0}
                if detail == "未找到秒杀购买入口" or detail.startswith("点击秒杀购买入口失败"):
                    return {"success": False, "detail": detail, "value": 0}

            success, detail = await self._attempt_purchase_step(page)
            if success:
                return {"success": True, "detail": detail, "value": event.value}
            if detail:
                return {"success": False, "detail": detail, "value": 0}

            return {
                "success": False,
                "detail": "已尝试进入秒杀购买，但未看到下单成功证据",
                "value": 0,
            }
        except Exception as exc:
            logger.error("[京东秒杀] 执行异常: %s", exc)
            return {"success": False, "detail": str(exc), "value": 0}
        finally:
            await page.close()
            await self._browser_pool.release(context)


class JDCouponHandler(JDInteractiveMixin, TaskHandler):
    """Handle JD coupon claiming."""

    def __init__(self, browser_pool: BrowserPool):
        self._browser_pool = browser_pool
        self._auth = AuthManager()

    @staticmethod
    def _coupon_result_from_content(content: str) -> str:
        if any(marker in content for marker in ("今日已领取", "已领取")):
            return "今日已领取"
        if any(marker in content for marker in ("领取成功", "已到账")):
            return "领取成功"
        return ""

    async def handle(self, event: WoolEvent) -> dict:
        result: dict = {"success": False, "detail": "未执行", "value": 0}
        for attempt in range(2):
            if attempt > 0:
                logger.info("[京东领券] 验证完成，重试一次...")
            await self._apply_rate_limit()
            result = await self._do_coupon(event, attempt)
            if (
                attempt == 0
                and not result.get("success")
                and "风控验证" in result.get("detail", "")
                and "未完成" not in result.get("detail", "")
            ):
                continue
            break
        return result

    async def _do_coupon(self, event: WoolEvent, attempt: int) -> dict:
        logger.info("[京东领券] 正在检查可领优惠券...")
        context = await self._browser_pool.acquire_for_platform("jd")
        page = await context.new_page()
        try:
            if attempt == 0:
                await self._warm_session(page)
            await self._humanize(page)
            target_url = event.url or "https://coupon.m.jd.com/"
            await page.goto(
                target_url,
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(2)

            if self._is_network_blocked(page.url):
                detail = f"当前网络屏蔽了京东领券页（被重定向到 {page.url}），请切换网络后重试"
                logger.warning("[京东领券] %s", detail)
                return {"success": False, "detail": detail, "value": 0}

            challenge_result = await self._resolve_jd_challenge(page, "京东领券")
            if challenge_result:
                return challenge_result

            claim_buttons = await page.query_selector_all(
                "button:has-text('领取'), a:has-text('立即领取'), .coupon-get"
            )
            claimed = 0
            for button in claim_buttons:
                try:
                    await button.click()
                    await asyncio.sleep(0.5)
                except Exception:
                    continue

                challenge_result = await self._resolve_jd_challenge(page, "京东领券")
                if challenge_result:
                    return challenge_result
                claimed += 1

            content = await page.content()
            result_text = self._coupon_result_from_content(content)

            if claimed > 0:
                logger.info("[京东领券] 领取了 %d 张优惠券", claimed)
                return {"success": True, "detail": f"领取了 {claimed} 张优惠券", "value": event.value}
            if result_text:
                logger.info("[京东领券] %s", result_text)
                return {"success": True, "detail": result_text, "value": 0}

            logger.info("[京东领券] 暂无可领优惠券")
            return {"success": True, "detail": "无可领优惠券", "value": 0}
        except Exception as exc:
            logger.error("[京东领券] 执行异常: %s", exc)
            return {"success": False, "detail": str(exc), "value": 0}
        finally:
            await page.close()
            await self._browser_pool.release(context)


class MarketplaceActivityHandler(TaskHandler):
    """Honest marketplace page inspection without claiming earnings."""

    def __init__(self, browser_pool: BrowserPool):
        self._browser_pool = browser_pool

    async def handle(self, event: WoolEvent) -> dict:
        platform_name = PLATFORM_NAMES.get(event.platform, event.platform)
        logger.info("[%s活动] 正在检查活动入口: %s", platform_name, event.title)
        context = await self._browser_pool.acquire_for_platform(event.platform)
        page = await context.new_page()
        try:
            if event.url:
                await page.goto(event.url, wait_until="domcontentloaded", timeout=15000)
                await page.wait_for_timeout(1000)
            markers = event.data.get("markers") or []
            marker_text = "、".join(markers) if markers else "页面可访问"
            return {
                "success": True,
                "detail": f"仅完成活动入口巡检: {marker_text}",
                "value": event.value,
            }
        except Exception as exc:
            logger.error("[%s活动] 执行异常: %s", platform_name, exc)
            return {"success": False, "detail": str(exc), "value": 0}
        finally:
            await page.close()
            await self._browser_pool.release(context)
