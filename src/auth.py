# -*- coding: utf-8 -*-
"""登录管理：弹窗扫码登录、cookie 持久化与校验。"""

# ================================
# 导入依赖
# ================================
import json
import asyncio
from pathlib import Path
from typing import Optional

from playwright.async_api import async_playwright, BrowserContext


# ================================
# 路径常量
# ================================
COOKIE_DIR = Path(__file__).resolve().parent.parent / "cookies"
LOGIN_TIMEOUT = 120_000  # 登录超时 120 秒


# ================================
# 登录管理器
# ================================
class AuthManager:
    """管理各平台登录与 cookie 持久化。

    首次使用通过弹出可见浏览器让用户扫码/输密码完成登录，
    cookie 保存到本地文件，后续 daemon 可静默加载使用。
    """

    @staticmethod
    def _platform_domain(platform: str) -> str:
        """返回各平台的登录页域名。"""
        domains = {
            "jd": "https://passport.jd.com/new/login.aspx",
            "taobao": "https://login.taobao.com",
            "pdd": "https://mms.pinduoduo.com",
            "miniapp": "https://login.weixin.qq.com",
        }
        return domains.get(platform, f"https://{platform}.com")

    @staticmethod
    def _cookie_path(platform: str) -> Path:
        """返回 cookie 文件路径。"""
        COOKIE_DIR.mkdir(parents=True, exist_ok=True)
        return COOKIE_DIR / f"{platform}.json"

    # ================================
    # 弹窗扫码登录
    # ================================
    async def login_platform(self, platform: str) -> bool:
        """弹出可见浏览器，等待用户扫码登录。

        Args:
            platform: 平台标识 (jd / taobao / pdd / miniapp)

        Returns:
            登录是否成功
        """
        login_url = self._platform_domain(platform)
        print(f"\n🖥️  正在打开 {platform} 登录页面...")
        print(f"🔗 地址: {login_url}")
        print("📱 请在浏览器中扫码或输入账号密码完成登录")
        print(f"⏳ 等待登录（最长 {LOGIN_TIMEOUT // 1000} 秒）...\n")

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)

                # ================================
                # 等待用户完成登录
                # ================================
                # ================================
                # 等待用户完成登录（每 0.5s 检测一次）
                # ================================
                for _ in range(LOGIN_TIMEOUT // 500):
                    cookies = await context.cookies()
                    if self._has_session_cookie(cookies):
                        # 登录成功，保存 cookie
                        await self._save_cookies(platform, cookies)
                        print(f"\n✅ {platform} 登录成功！")
                        return True
                    await asyncio.sleep(0.5)

                print(f"\n⏰ {platform} 登录超时，请重试")
                return False

            except Exception as e:
                print(f"\n❌ {platform} 登录失败: {e}")
                return False
            finally:
                await page.close()
                await context.close()
                await browser.close()

    # ================================
    # Cookie 管理
    # ================================
    async def _save_cookies(self, platform: str, cookies: list[dict]) -> None:
        """保存 cookie 到本地文件。"""
        path = self._cookie_path(platform)
        path.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 Cookie 已保存: {path}")

    async def load_cookies(self, platform: str) -> Optional[list[dict]]:
        """从本地文件加载 cookie。"""
        path = self._cookie_path(platform)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else None
        except (json.JSONDecodeError, ValueError):
            return None

    async def inject_cookies(self, context: BrowserContext, platform: str) -> bool:
        """向浏览器上下文注入 cookie。返回是否成功。"""
        cookies = await self.load_cookies(platform)
        if not cookies:
            return False
        await context.add_cookies(cookies)
        return True

    async def validate_cookies(self, context: BrowserContext, platform: str) -> bool:
        """校验当前浏览器上下文中的 cookie 是否有效。"""
        cookies = await context.cookies()
        return self._has_session_cookie(cookies)

    # ================================
    # 工具方法
    # ================================
    @staticmethod
    def _has_session_cookie(cookies: list[dict]) -> bool:
        """判断是否存在有效的会话 cookie。"""
        # Playwright 中 session cookie 的 expires 为 -1
        # 有 3 个以上 cookie 即认为已登录
        return len(cookies) >= 3
