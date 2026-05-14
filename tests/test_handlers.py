import asyncio
import unittest
from unittest.mock import AsyncMock, Mock

from src.browser import BrowserPool
from src.event import WoolEvent
from src.handlers import JDCouponHandler, JDFlashSaleHandler, JDSignInHandler
from src.watchers.jd_watcher import JDWatcher
from src.event import EventQueue


class JDSignInHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_skips_auto_sign_when_web_cookies_are_unverified(self):
        # thor/pin present but session not verified → should not proceed to browser
        browser_pool = AsyncMock()
        handler = JDSignInHandler(browser_pool)
        handler._auth.load_cookies = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )
        handler._auth.has_persistent_profile = Mock(return_value=False)  # type: ignore[attr-defined]
        handler._auth.get_saved_session_state = AsyncMock(  # type: ignore[attr-defined]
            return_value={
                "logged_in": False,
                "verified": False,
                "capabilities": {"web": False, "mobile_sign": False},
            }
        )

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="sign_in", title="签到", value=2.0)
        )

        self.assertFalse(result["success"])
        self.assertIn("未验证", result["detail"])
        browser_pool.acquire_for_platform.assert_not_called()

    async def test_accepts_verified_web_session_without_mobile_cookies(self):
        browser_pool = AsyncMock()
        handler = JDSignInHandler(browser_pool)
        handler._auth.load_cookies = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )
        handler._auth.has_persistent_profile = Mock(return_value=False)  # type: ignore[attr-defined]
        handler._auth.get_saved_session_state = AsyncMock(  # type: ignore[attr-defined]
            return_value={
                "logged_in": True,
                "verified": True,
                "capabilities": {"web": True, "mobile_sign": False},
            }
        )
        handler._auth._safe_page_text = AsyncMock(return_value="sign page")  # type: ignore[attr-defined]

        button = AsyncMock()
        page = AsyncMock()
        page.url = "https://bean.m.jd.com/bean/signIndex.action"
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector = AsyncMock(return_value=button)
        page.content = AsyncMock(return_value="签到成功")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="sign_in", title="签到", value=2.0)
        )

        self.assertTrue(result["success"])
        browser_pool.acquire_for_platform.assert_awaited_once_with("jd")

    async def test_accepts_persistent_profile_without_mobile_cookies(self):
        browser_pool = AsyncMock()
        handler = JDSignInHandler(browser_pool)
        handler._auth.load_cookies = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )
        handler._auth.has_persistent_profile = Mock(return_value=True)  # type: ignore[attr-defined]
        handler._auth._safe_page_text = AsyncMock(return_value="sign page")  # type: ignore[attr-defined]

        button = AsyncMock()
        page = AsyncMock()
        page.url = "https://bean.m.jd.com/bean/signIndex.action"
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector = AsyncMock(return_value=button)
        page.content = AsyncMock(return_value="签到成功")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="sign_in", title="签到", value=2.0)
        )

        self.assertTrue(result["success"])
        browser_pool.acquire_for_platform.assert_awaited_once_with("jd")

    async def test_waits_on_jd_challenge_page_before_closing(self):
        browser_pool = AsyncMock()
        handler = JDSignInHandler(browser_pool)
        handler._auth.load_cookies = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                {"name": "pt_key", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pt_pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )
        handler._wait_for_manual_challenge = AsyncMock(return_value=False)  # type: ignore[method-assign]

        context = AsyncMock()
        page = AsyncMock()
        page.url = "https://www.jd.com/"
        page.content = AsyncMock(return_value="")
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)
        handler._auth._safe_page_text = AsyncMock(  # type: ignore[attr-defined]
            return_value="验证一下 购物无忧 快速验证"
        )

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="sign_in", title="签到", value=2.0)
        )

        self.assertFalse(result["success"])
        self.assertIn("快速验证", result["detail"])
        handler._wait_for_manual_challenge.assert_awaited_once()
        page.close.assert_awaited_once()
        browser_pool.release.assert_awaited_once_with(context)

    async def test_sign_in_succeeds_only_when_page_confirms_signed_in(self):
        browser_pool = AsyncMock()
        handler = JDSignInHandler(browser_pool)
        handler._auth.load_cookies = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                {"name": "pt_key", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pt_pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )
        handler._auth._safe_page_text = AsyncMock(return_value="签到页面")  # type: ignore[attr-defined]

        button = AsyncMock()
        page = AsyncMock()
        page.url = "https://bean.m.jd.com/bean/signIndex.action"
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector = AsyncMock(return_value=button)
        page.content = AsyncMock(return_value="今日已签到，已连续签到")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="sign_in", title="签到", value=2.0)
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["detail"], "今日已签到")

    async def test_sign_in_does_not_claim_success_without_page_evidence(self):
        browser_pool = AsyncMock()
        handler = JDSignInHandler(browser_pool)
        handler._auth.load_cookies = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                {"name": "pt_key", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pt_pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )
        handler._auth._safe_page_text = AsyncMock(return_value="签到页面")  # type: ignore[attr-defined]

        button = AsyncMock()
        page = AsyncMock()
        page.url = "https://bean.m.jd.com/bean/signIndex.action"
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector = AsyncMock(return_value=button)
        page.content = AsyncMock(return_value="签到入口还在，没有成功提示")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="sign_in", title="签到", value=2.0)
        )

        self.assertFalse(result["success"])
        self.assertIn("页面结构变化", result["detail"])

    async def test_waits_when_challenge_appears_after_sign_in_click(self):
        browser_pool = AsyncMock()
        handler = JDSignInHandler(browser_pool)
        handler._auth.load_cookies = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                {"name": "pt_key", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pt_pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )
        handler._auth._safe_page_text = AsyncMock(  # type: ignore[attr-defined]
            side_effect=["sign page", "challenge page"]
        )
        handler._auth.login_challenge_reason = Mock(side_effect=["", "challenge"])  # type: ignore[attr-defined]
        handler._wait_for_manual_challenge = AsyncMock(return_value=False)  # type: ignore[method-assign]

        button = AsyncMock()
        page = AsyncMock()
        page.url = "https://bean.m.jd.com/bean/signIndex.action"
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector = AsyncMock(return_value=button)
        page.content = AsyncMock(return_value="challenge content")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="sign_in", title="绛惧埌", value=2.0)
        )

        self.assertFalse(result["success"])
        handler._wait_for_manual_challenge.assert_awaited_once()

    async def test_sign_in_accepts_already_signed_page_without_click(self):
        browser_pool = AsyncMock()
        handler = JDSignInHandler(browser_pool)
        handler._auth.load_cookies = AsyncMock(  # type: ignore[attr-defined]
            return_value=[
                {"name": "pt_key", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pt_pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )
        handler._auth._safe_page_text = AsyncMock(return_value="签到页面")  # type: ignore[attr-defined]

        page = AsyncMock()
        page.url = "https://bean.m.jd.com/bean/signIndex.action"
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector = AsyncMock(return_value=None)
        page.content = AsyncMock(return_value="今日已签到")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="sign_in", title="签到", value=2.0)
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["detail"], "今日已签到")


class JDCouponHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_reports_claimed_coupon_count(self):
        browser_pool = AsyncMock()
        handler = JDCouponHandler(browser_pool)

        claim_button = AsyncMock()
        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[claim_button, claim_button])
        page.content = AsyncMock(return_value="立即领取")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="coupon", title="领券", value=1.0)
        )

        self.assertTrue(result["success"])
        self.assertIn("2 张优惠券", result["detail"])
        self.assertEqual(claim_button.click.await_count, 2)
        browser_pool.release.assert_awaited_once_with(context)

    async def test_returns_no_coupon_when_page_has_no_claim_entry(self):
        browser_pool = AsyncMock()
        handler = JDCouponHandler(browser_pool)

        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[])
        page.content = AsyncMock(return_value="暂无可领优惠券")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="coupon", title="领券", value=1.0)
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["detail"], "无可领优惠券")

    async def test_treats_success_banner_as_real_coupon_claim(self):
        browser_pool = AsyncMock()
        handler = JDCouponHandler(browser_pool)

        claim_button = AsyncMock()
        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[claim_button])
        page.content = AsyncMock(return_value="领取成功，优惠券已到账")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="coupon", title="领券", value=1.0)
        )

        self.assertTrue(result["success"])
        self.assertIn("1 张优惠券", result["detail"])

    async def test_reports_page_change_when_claim_clicks_fail_but_coupon_is_already_claimed(self):
        browser_pool = AsyncMock()
        handler = JDCouponHandler(browser_pool)

        claim_button = AsyncMock()
        claim_button.click.side_effect = RuntimeError("button detached")
        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector_all = AsyncMock(return_value=[claim_button])
        page.content = AsyncMock(return_value="今日已领取")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="coupon", title="领券", value=1.0)
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["detail"], "今日已领取")


class JDFlashSaleHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_target_url_for_real_flash_sale_action(self):
        browser_pool = AsyncMock()
        handler = JDFlashSaleHandler(browser_pool)

        result = await handler.handle(
            WoolEvent(platform="jd", event_type="flash_sale", title="jd flash", value=10.0)
        )

        self.assertFalse(result["success"])
        self.assertIn("链接", result["detail"])
        browser_pool.acquire_for_platform.assert_not_called()

    async def test_reports_missing_purchase_entry_when_flash_sale_page_has_no_buy_button(self):
        browser_pool = AsyncMock()
        handler = JDFlashSaleHandler(browser_pool)

        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector = AsyncMock(return_value=None)
        page.content = AsyncMock(return_value="秒杀活动进行中")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(
                platform="jd",
                event_type="flash_sale",
                title="jd flash",
                value=10.0,
                url="https://item.jd.com/123.html",
            )
        )

        self.assertFalse(result["success"])
        self.assertIn("购买入口", result["detail"])

    async def test_uses_purchase_url_from_event_data_when_available(self):
        browser_pool = AsyncMock()
        handler = JDFlashSaleHandler(browser_pool)

        buy_button = AsyncMock()
        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector = AsyncMock(return_value=buy_button)
        page.content = AsyncMock(return_value="订单已提交")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(
                platform="jd",
                event_type="flash_sale",
                title="jd flash",
                value=10.0,
                url="https://miaosha.jd.com/",
                data={"purchase_url": "https://item.jd.com/123.html"},
            )
        )

        self.assertTrue(result["success"])
        page.goto.assert_any_await(
            "https://item.jd.com/123.html",
            wait_until="domcontentloaded",
            timeout=15000,
        )

    async def test_reports_success_only_when_order_evidence_appears_after_click(self):
        browser_pool = AsyncMock()
        handler = JDFlashSaleHandler(browser_pool)

        buy_button = AsyncMock()
        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector = AsyncMock(return_value=buy_button)
        page.content = AsyncMock(return_value="已下单，请您尽快完成支付")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(
                platform="jd",
                event_type="flash_sale",
                title="jd flash",
                value=10.0,
                url="https://item.jd.com/123.html",
            )
        )

        self.assertTrue(result["success"])
        self.assertIn("订单", result["detail"])


    async def test_waits_when_challenge_appears_after_buy_click(self):
        browser_pool = AsyncMock()
        handler = JDFlashSaleHandler(browser_pool)
        handler._auth = AsyncMock()  # type: ignore[attr-defined]
        handler._auth._safe_page_text = AsyncMock(  # type: ignore[attr-defined]
            side_effect=["flash sale page", "challenge page"]
        )
        handler._auth.login_challenge_reason = Mock(  # type: ignore[attr-defined]
            side_effect=["", "challenge"]
        )
        handler._wait_for_manual_challenge = AsyncMock(return_value=False)  # type: ignore[method-assign]

        buy_button = AsyncMock()
        page = AsyncMock()
        page.url = "https://item.jd.com/123.html"
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.query_selector = AsyncMock(return_value=buy_button)
        page.content = AsyncMock(return_value="challenge content")

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(
                platform="jd",
                event_type="flash_sale",
                title="jd flash",
                value=10.0,
                url="https://item.jd.com/123.html",
            )
        )

        self.assertFalse(result["success"])
        handler._wait_for_manual_challenge.assert_awaited_once()

    async def test_retries_purchase_flow_after_challenge_is_solved(self):
        browser_pool = AsyncMock()
        handler = JDFlashSaleHandler(browser_pool)
        handler._resolve_jd_challenge = AsyncMock(side_effect=[None, None, None])  # type: ignore[method-assign]

        first_button = AsyncMock()
        submit_button = AsyncMock()
        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.content = AsyncMock(side_effect=["仍需提交订单", "订单已提交 请您尽快完成支付"])
        page.query_selector = AsyncMock(side_effect=[first_button, submit_button])

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(
                platform="jd",
                event_type="flash_sale",
                title="jd flash",
                value=10.0,
                url="https://item.jd.com/123.html",
            )
        )

        self.assertTrue(result["success"])
        self.assertEqual(first_button.click.await_count, 1)
        self.assertEqual(submit_button.click.await_count, 1)

    async def test_reports_failure_when_challenge_is_solved_but_no_order_evidence_after_retry(self):
        browser_pool = AsyncMock()
        handler = JDFlashSaleHandler(browser_pool)
        handler._resolve_jd_challenge = AsyncMock(side_effect=[None, None, None])  # type: ignore[method-assign]

        first_button = AsyncMock()
        submit_button = AsyncMock()
        page = AsyncMock()
        page.goto = AsyncMock()
        page.wait_for_timeout = AsyncMock()
        page.content = AsyncMock(side_effect=["仍需提交订单", "还是没有成功证据"])
        page.query_selector = AsyncMock(side_effect=[first_button, submit_button])

        context = AsyncMock()
        context.new_page = AsyncMock(return_value=page)
        browser_pool.acquire_for_platform = AsyncMock(return_value=context)

        result = await handler.handle(
            WoolEvent(
                platform="jd",
                event_type="flash_sale",
                title="jd flash",
                value=10.0,
                url="https://item.jd.com/123.html",
            )
        )

        self.assertFalse(result["success"])
        self.assertIn("未看到下单成功证据", result["detail"])

class JDWatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_sign_event_is_skipped_with_unverified_web_cookies(self):
        # thor/pin present but session not verified → watcher should not emit event
        watcher = JDWatcher(EventQueue(), browser_pool=AsyncMock())
        watcher.auth.has_persistent_profile = Mock(return_value=False)  # type: ignore[attr-defined]
        watcher.auth.get_saved_session_state = AsyncMock(  # type: ignore[attr-defined]
            return_value={
                "logged_in": False,
                "verified": False,
                "capabilities": {"web": False, "mobile_sign": False},
            }
        )

        event = await watcher._check_sign_in(
            [
                {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )

        self.assertIsNone(event)

    async def test_sign_event_is_emitted_with_verified_web_session(self):
        watcher = JDWatcher(EventQueue(), browser_pool=AsyncMock())
        watcher.auth.has_persistent_profile = Mock(return_value=False)  # type: ignore[attr-defined]
        watcher.auth.get_saved_session_state = AsyncMock(  # type: ignore[attr-defined]
            return_value={
                "logged_in": True,
                "verified": True,
                "capabilities": {"web": True, "mobile_sign": False},
            }
        )

        event = await watcher._check_sign_in(
            [
                {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "sign_in")

    async def test_sign_event_is_emitted_with_persistent_profile(self):
        watcher = JDWatcher(EventQueue(), browser_pool=AsyncMock())
        watcher.auth.has_persistent_profile = Mock(return_value=True)  # type: ignore[attr-defined]

        event = await watcher._check_sign_in(
            [
                {"name": "thor", "value": "x", "domain": ".jd.com", "path": "/"},
                {"name": "pin", "value": "y", "domain": ".jd.com", "path": "/"},
            ]
        )

        self.assertIsNotNone(event)
        self.assertEqual(event.event_type, "sign_in")


class BrowserPoolTests(unittest.IsolatedAsyncioTestCase):
    async def test_prefers_persistent_platform_context_over_cookie_injection(self):
        pool = BrowserPool()
        persistent = AsyncMock()
        lock = asyncio.Lock()

        pool._auth = AsyncMock()  # type: ignore[attr-defined]
        pool._auth.has_persistent_profile = Mock(return_value=True)  # type: ignore[attr-defined]
        pool._get_persistent_context = AsyncMock(return_value=persistent)  # type: ignore[method-assign]
        pool._persistent_locks = {"jd": lock}  # type: ignore[attr-defined]

        context = await pool.acquire_for_platform("jd")

        self.assertIs(context, persistent)
        pool._get_persistent_context.assert_awaited_once_with("jd")  # type: ignore[attr-defined]
        pool._auth.inject_cookies.assert_not_called()  # type: ignore[attr-defined]

        await pool.release(context)

    async def test_uses_persistent_context_for_verified_jd_web_session(self):
        pool = BrowserPool()
        persistent = AsyncMock()
        ephemeral = AsyncMock()
        lock = asyncio.Lock()

        pool._auth = AsyncMock()  # type: ignore[attr-defined]
        pool._auth.has_persistent_profile = Mock(return_value=False)  # type: ignore[attr-defined]
        pool._auth.get_saved_session_state = AsyncMock(  # type: ignore[attr-defined]
            return_value={
                "logged_in": True,
                "verified": True,
                "capabilities": {"web": True, "mobile_sign": False},
            }
        )
        pool._auth.inject_cookies = AsyncMock()  # type: ignore[attr-defined]
        pool._get_persistent_context = AsyncMock(return_value=persistent)  # type: ignore[method-assign]
        pool._persistent_locks = {"jd": lock}  # type: ignore[attr-defined]
        pool.acquire = AsyncMock(return_value=ephemeral)  # type: ignore[method-assign]

        context = await pool.acquire_for_platform("jd")

        self.assertIs(context, persistent)
        pool._get_persistent_context.assert_awaited_once_with("jd")
        pool._auth.inject_cookies.assert_awaited_once_with(persistent, "jd")  # type: ignore[attr-defined]

        await pool.release(context)

    async def test_recreates_context_when_cookie_injection_fails(self):
        pool = BrowserPool()
        stale = AsyncMock()
        fresh = AsyncMock()
        browser = Mock()
        browser.close = AsyncMock()
        browser.is_connected.return_value = True

        pool._auth = AsyncMock()  # type: ignore[attr-defined]
        pool._auth.inject_cookies = AsyncMock(  # type: ignore[attr-defined]
            side_effect=[RuntimeError("driver closed"), None]
        )
        pool._browsers = [browser]
        pool._contexts = {id(stale): stale}
        pool._available = AsyncMock()
        pool.acquire = AsyncMock(return_value=stale)  # type: ignore[method-assign]
        pool._create_context = AsyncMock(return_value=fresh)  # type: ignore[method-assign]

        context = await pool.acquire_for_platform("jd")

        self.assertIs(context, fresh)
        stale.close.assert_awaited()
        pool._create_context.assert_awaited_once_with(browser)
        self.assertEqual(pool._auth.inject_cookies.await_count, 2)  # type: ignore[attr-defined]

    async def test_recreates_browser_when_replacement_browser_is_disconnected(self):
        pool = BrowserPool()
        stale = AsyncMock()
        fresh_context = AsyncMock()
        disconnected_browser = Mock()
        disconnected_browser.close = AsyncMock()
        replacement_browser = Mock()
        replacement_browser.close = AsyncMock()

        disconnected_browser.is_connected.return_value = False
        replacement_browser.is_connected.return_value = True

        pool._auth = AsyncMock()  # type: ignore[attr-defined]
        pool._auth.inject_cookies = AsyncMock(  # type: ignore[attr-defined]
            side_effect=[RuntimeError("driver closed"), None]
        )
        pool._browsers = [disconnected_browser]
        pool._contexts = {id(stale): stale}
        pool.acquire = AsyncMock(return_value=stale)  # type: ignore[method-assign]
        pool._create_browser = AsyncMock(return_value=replacement_browser)  # type: ignore[method-assign]
        pool._create_context = AsyncMock(return_value=fresh_context)  # type: ignore[method-assign]

        context = await pool.acquire_for_platform("jd")

        self.assertIs(context, fresh_context)
        disconnected_browser.close.assert_awaited()
        pool._create_browser.assert_awaited_once()
        pool._create_context.assert_awaited_once_with(replacement_browser)
        self.assertEqual(pool._browsers, [replacement_browser])

    async def test_release_discards_closed_context_and_queues_replacement(self):
        pool = BrowserPool()
        closed_context = AsyncMock()
        fresh_context = AsyncMock()
        browser = Mock()
        browser.close = AsyncMock()

        closed_context.pages = []
        closed_context.new_page.side_effect = RuntimeError("Target page, context or browser has been closed")
        browser.is_connected.return_value = True

        pool._browsers = [browser]
        pool._contexts = {id(closed_context): closed_context}
        pool._available = asyncio.Queue()
        pool._create_context = AsyncMock(return_value=fresh_context)  # type: ignore[method-assign]

        await pool.release(closed_context)

        queued = await pool._available.get()
        self.assertIs(queued, fresh_context)
        closed_context.close.assert_awaited()
