# -*- coding: utf-8 -*-
"""任务处理器 — 各平台各类事件的具体执行逻辑。"""

# ================================
# 导入依赖
# ================================
import asyncio
import logging
from typing import Optional

from src.event import WoolEvent
from src.executor import TaskHandler
from src.browser import BrowserPool
from src.auth import AuthManager

logger = logging.getLogger("freeload")


PLATFORM_NAMES = {
    "jd": "京东",
    "taobao": "淘宝",
    "pdd": "拼多多",
}
JD_CHALLENGE_WAIT_SECONDS = 120


# ================================
# 京东签到处理器
# ================================
class JDSignInHandler(TaskHandler):
    """京东每日签到处理器 — 打开签到页面并点击签到按钮。"""

    def __init__(self, browser_pool: BrowserPool):
        self._browser_pool = browser_pool
        self._auth = AuthManager()

    async def _wait_for_manual_challenge(self, page) -> bool:
        """Keep the verification page open so the user can complete JD challenge."""
        for _ in range(JD_CHALLENGE_WAIT_SECONDS):
            page_text = await self._auth._safe_page_text(page)
            if not self._auth.login_challenge_reason(
                "jd", page_url=page.url, page_text=page_text
            ):
                return True
            await page.wait_for_timeout(1000)
        return False

    async def handle(self, event: WoolEvent) -> dict:
        logger.info("[京东签到] 正在执行签到...")
        cookies = await self._auth.load_cookies("jd")
        if not cookies:
            detail = "未找到京东 cookie，请重新登录京东"
            logger.warning("[京东签到] %s", detail)
            return {"success": False, "detail": detail, "value": 0}

        if not self._auth.has_required_cookie_group(cookies, "jd", "mobile_sign"):
            detail = "当前京东 cookie 只有网页登录态，缺少移动签到所需的 pt_key/pt_pin，已跳过自动签到"
            logger.warning("[京东签到] %s", detail)
            return {"success": False, "detail": detail, "value": 0}

        context = await self._browser_pool.acquire_for_platform("jd")
        page = await context.new_page()
        try:
            await page.goto(
                "https://bean.m.jd.com/bean/signIndex.action",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await page.wait_for_timeout(1500)
            page_text = await self._auth._safe_page_text(page)

            if self._auth.login_challenge_reason("jd", page_url=page.url, page_text=page_text):
                logger.warning("[京东签到] 检测到风控验证页，等待手动完成快速验证...")
                solved = await self._wait_for_manual_challenge(page)
                if not solved:
                    detail = (
                        f"京东触发风控验证，已保留窗口 {JD_CHALLENGE_WAIT_SECONDS}s，"
                        "请先手动完成“快速验证”后再重试"
                    )
                    logger.warning("[京东签到] %s", detail)
                    return {"success": False, "detail": detail, "value": 0}
                await page.wait_for_timeout(1000)
                page_text = await self._auth._safe_page_text(page)

            if "jfe-lb.jd.com/file-no.2/public/jfe.html?err=52" in page.url:
                detail = "京东签到页返回 JFE 52，当前网络或 cookie 不满足移动签到要求"
                logger.warning("[京东签到] %s", detail)
                return {"success": False, "detail": detail, "value": 0}

            # 尝试点击签到按钮
            selectors = [
                "#signBtn",
                ".sign-btn",
                "button:has-text('签到')",
                "a:has-text('签到')",
                "[class*=sign]",
            ]
            for sel in selectors:
                btn = await page.query_selector(sel)
                if btn:
                    try:
                        await btn.click()
                        await page.wait_for_timeout(1000)
                        logger.info("[京东签到] ✅ 签到成功")
                        return {"success": True, "detail": "签到完成", "value": event.value}
                    except Exception as e:
                        logger.warning("[京东签到] 点击按钮失败: %s", e)

            # 检查是否已签到
            content = await page.content()
            if "已签到" in content or "已连续签到" in content:
                logger.info("[京东签到] 今日已签到")
                return {"success": True, "detail": "今日已签到", "value": event.value}

            logger.warning("[京东签到] 未找到签到按钮")
            return {"success": False, "detail": "未找到签到按钮或页面结构变化", "value": 0}

        except Exception as e:
            logger.error("[京东签到] 执行异常: %s", e)
            return {"success": False, "detail": str(e), "value": 0}
        finally:
            await page.close()
            await self._browser_pool.release(context)


# ================================
# 京东秒杀处理器
# ================================
class JDFlashSaleHandler(TaskHandler):
    """京东秒杀处理器 — 记录秒杀事件（后续可扩展为自动抢购）。"""

    def __init__(self, browser_pool: BrowserPool):
        self._browser_pool = browser_pool

    async def handle(self, event: WoolEvent) -> dict:
        logger.info("[京东秒杀] 发现秒杀商品: %s", event.title)
        # 当前仅记录，后续可加入自动跳转购买逻辑
        return {"success": True, "detail": "已记录秒杀商品", "value": event.value}


# ================================
# 京东领券处理器
# ================================
class JDCouponHandler(TaskHandler):
    """京东领券处理器 — 打开领券中心领取可用优惠券。"""

    def __init__(self, browser_pool: BrowserPool):
        self._browser_pool = browser_pool

    async def handle(self, event: WoolEvent) -> dict:
        logger.info("[京东领券] 正在检查可领优惠券...")
        context = await self._browser_pool.acquire_for_platform("jd")
        page = await context.new_page()
        try:
            await page.goto(
                "https://coupon.m.jd.com/",
                wait_until="domcontentloaded",
                timeout=15000,
            )
            await asyncio.sleep(2)

            # 尝试领取
            claim_btns = await page.query_selector_all(
                "button:has-text('领取'), a:has-text('立即领取'), .coupon-get"
            )
            claimed = 0
            for btn in claim_btns:
                try:
                    await btn.click()
                    await asyncio.sleep(0.5)
                    claimed += 1
                except Exception:
                    continue

            if claimed > 0:
                logger.info("[京东领券] ✅ 领取了 %d 张优惠券", claimed)
                return {"success": True, "detail": f"领取 {claimed} 张优惠券", "value": event.value}
            else:
                logger.info("[京东领券] 暂无可用优惠券")
                return {"success": True, "detail": "无可领优惠券", "value": 0}

        except Exception as e:
            logger.error("[京东领券] 执行异常: %s", e)
            return {"success": False, "detail": str(e), "value": 0}
        finally:
            await page.close()
            await self._browser_pool.release(context)


class MarketplaceActivityHandler(TaskHandler):
    """Open a marketplace activity page and record the visible activity check."""

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
        except Exception as e:
            logger.error("[%s活动] 执行异常: %s", platform_name, e)
            return {"success": False, "detail": str(e), "value": 0}
        finally:
            await page.close()
            await self._browser_pool.release(context)
