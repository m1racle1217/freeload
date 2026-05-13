import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src import auth as auth_module
from src.auth import AuthManager


class AuthSessionCookieTests(unittest.TestCase):
    def cookie(self, name, value="value"):
        return {"name": name, "value": value, "domain": ".example.com", "path": "/"}

    def test_plain_tracking_cookie_is_not_a_session(self):
        cookies = [self.cookie("__jdu"), self.cookie("QRCodeKey")]

        self.assertFalse(AuthManager._has_session_cookie(cookies, "jd"))

    def test_jd_accepts_desktop_session_cookie_pair(self):
        cookies = [self.cookie("thor"), self.cookie("pin")]

        self.assertTrue(AuthManager._has_session_cookie(cookies, "jd"))

    def test_jd_accepts_mobile_session_cookie_pair(self):
        cookies = [self.cookie("pt_key"), self.cookie("pt_pin")]

        self.assertTrue(AuthManager._has_session_cookie(cookies, "jd"))

    def test_jd_desktop_session_is_not_mobile_sign_session(self):
        cookies = [self.cookie("thor"), self.cookie("pin")]

        self.assertFalse(AuthManager.has_required_cookie_group(cookies, "jd", "mobile_sign"))

    def test_jd_mobile_sign_session_requires_pt_pair(self):
        cookies = [self.cookie("pt_key"), self.cookie("pt_pin")]

        self.assertTrue(AuthManager.has_required_cookie_group(cookies, "jd", "mobile_sign"))

    def test_cookie_count_growth_does_not_imply_login(self):
        cookies = [self.cookie(f"tracking_{idx}") for idx in range(10)]

        self.assertFalse(AuthManager._has_session_cookie(cookies, "taobao", initial_count=0))

    def test_pdd_does_not_accept_anonymous_api_uid_with_vds_cookie(self):
        cookies = [self.cookie("api_uid"), self.cookie("pdd_vds")]

        self.assertFalse(AuthManager._has_session_cookie(cookies, "pdd"))

    def test_pdd_page_state_can_confirm_logged_in_session(self):
        cookies = [self.cookie("api_uid"), self.cookie("pdd_vds")]

        self.assertTrue(
            AuthManager.is_login_confirmed(
                "pdd",
                cookies,
                page_url="https://mobile.yangkeduo.com/index.html",
                page_text="拼小圈 新提醒 16 限时秒杀 充值中心 补贴多人团 免费领水果",
            )
        )

    def test_jd_login_requires_page_verification_when_only_web_cookie_exists(self):
        cookies = [self.cookie("thor"), self.cookie("pin")]

        self.assertFalse(AuthManager.is_login_confirmed("jd", cookies))

    def test_jd_page_state_can_confirm_real_login(self):
        cookies = [self.cookie("thor"), self.cookie("pin")]

        self.assertTrue(
            AuthManager.is_login_confirmed(
                "jd",
                cookies,
                page_url="https://home.jd.com/",
                page_text="我的京东 我的订单 京豆 收货地址",
            )
        )

    def test_jd_challenge_page_is_not_treated_as_authenticated(self):
        cookies = [self.cookie("thor"), self.cookie("pin")]

        self.assertFalse(
            AuthManager.is_login_confirmed(
                "jd",
                cookies,
                page_url="https://www.jd.com/",
                page_text="我的订单 我的京东 验证一下，购物无忧 快速验证",
            )
        )

    def test_taobao_login_requires_page_verification_when_only_cookie_exists(self):
        cookies = [self.cookie("_tb_token_"), self.cookie("cookie2")]

        self.assertFalse(AuthManager.is_login_confirmed("taobao", cookies))


class AuthSavedSessionTests(unittest.IsolatedAsyncioTestCase):
    def cookie(self, name, value="value"):
        return {"name": name, "value": value, "domain": ".jd.com", "path": "/"}

    async def test_has_saved_session_uses_platform_cookie_rules(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "jd.json").write_text(
                json.dumps([self.cookie("__jdu"), self.cookie("QRCodeKey")]),
                encoding="utf-8",
            )

            with patch.object(auth_module, "COOKIE_DIR", tmp_path):
                self.assertFalse(await AuthManager().has_saved_session("jd"))

    async def test_has_saved_session_accepts_valid_cookie_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "jd.json").write_text(
                json.dumps([self.cookie("thor"), self.cookie("pin")]),
                encoding="utf-8",
            )

            with patch.object(auth_module, "COOKIE_DIR", tmp_path):
                self.assertFalse(await AuthManager().has_saved_session("jd"))

    async def test_has_saved_session_accepts_verified_jd_cookie_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            payload = {
                "cookies": [self.cookie("thor"), self.cookie("pin")],
                "metadata": {"login_verified": True, "page_url": "https://home.jd.com/"},
            }
            (tmp_path / "jd.json").write_text(json.dumps(payload), encoding="utf-8")

            with patch.object(auth_module, "COOKIE_DIR", tmp_path):
                self.assertTrue(await AuthManager().has_saved_session("jd"))

    async def test_saved_session_state_marks_unverified_jd_cookie_as_pending(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "jd.json").write_text(
                json.dumps([self.cookie("thor"), self.cookie("pin")]),
                encoding="utf-8",
            )

            with patch.object(auth_module, "COOKIE_DIR", tmp_path):
                state = await AuthManager().get_saved_session_state("jd")
                self.assertFalse(state["logged_in"])
                self.assertEqual(state["label"], "待验证")
