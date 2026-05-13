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
            "taobao": "https://login.taobao.com/member/login.jhtml",
            "pdd": "https://mobile.yangkeduo.com/login.html",
            "miniapp": "https://open.weixin.qq.com/connect/qrconnect",
        }
        return domains.get(platform, f"https://{platform}.com")

    @staticmethod
    def _fallback_urls(platform: str) -> list[str]:
        """返回各平台备选登录 URL。"""
        fallbacks = {
            "taobao": [
                "https://login.taobao.com/",
                "https://login.m.taobao.com/login.htm",
                "https://www.taobao.com/",
            ],
            "pdd": [
                "https://www.pinduoduo.com/",
                "https://mobile.yangkeduo.com/",
            ],
        }
        return fallbacks.get(platform, [])

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
        print("✅ 登录完成后请回到本窗口按 Enter 键继续")
        print("⏳ 浏览器保持打开，不设超时\n")

        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-infobars",
                    "--disable-dev-shm-usage",
                ],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
            )
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            try:
                await page.goto(login_url, wait_until="domcontentloaded", timeout=30000)

                # ================================
                # 等待用户手动确认（扫码后按 Enter）
                # ================================
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, input)

                # ================================
                # 用户确认后，保存当前所有 cookie
                # ================================
                cookies = await context.cookies()
                if len(cookies) > 0:
                    await self._save_cookies(platform, cookies)
                    print(f"\n✅ {platform} 登录成功！")
                    return True
                else:
                    print(f"\n❌ {platform} 未检测到 cookie，登录可能失败")
                    return False

            except Exception as e:
                error_msg = str(e)
                # 如果主 URL 连接被拒，尝试备选 URL
                if "ERR_CONNECTION_CLOSED" in error_msg or "ERR_CONNECTION_REFUSED" in error_msg:
                    fallbacks = self._fallback_urls(platform)
                    for fb_url in fallbacks:
                        try:
                            print(f"🔁 连接被拒，尝试备选地址: {fb_url}")
                            await page.goto(fb_url, wait_until="domcontentloaded", timeout=30000)
                            print("✅ 备选地址可访问")
                            # 成功访问，继续等待用户扫码
                            loop = asyncio.get_event_loop()
                            await loop.run_in_executor(None, input)
                            cookies = await context.cookies()
                            if len(cookies) > 0:
                                await self._save_cookies(platform, cookies)
                                print(f"\n✅ {platform} 登录成功！")
                                return True
                            else:
                                print(f"\n❌ {platform} 未检测到 cookie，登录可能失败")
                                return False
                        except Exception as fb_e:
                            print(f"  ❌ 备选也失败: {fb_e}")
                print(f"\n❌ {platform} 登录失败: {e}")
                print(f"💡 提示: {platform} 可能暂时屏蔽了自动化浏览器")
                print(f"   请尝试用普通浏览器打开 {login_url} 确认页面可达")
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
        return self._has_session_cookie(cookies, platform)

    # ================================
    # 工具方法
    # ================================
    @staticmethod
    def _has_session_cookie(cookies: list[dict], platform: str = "", initial_count: int = 0) -> bool:
        """判断是否存在有效的登录态 cookie。

        检测策略：
        1. 优先检查平台特定的会话 cookie 名称
        2. 兜底：cookie 数量比初始加载时显著增加
        """
        # ================================
        # 各平台登录成功后的特征 cookie 名称
        # ================================
        session_cookies = {
            "jd": ["pt_key", "pt_pin"],              # 京东登录后的关键 cookie
            "taobao": ["_tb_token_", "cookie2"],      # 淘宝登录特征
            "pdd": ["_nano_fp", "pdd_user_id"],       # 拼多多登录特征
            "miniapp": ["session", "token"],           # 小程序通用特征
        }

        # 优先：检测特定 cookie 是否存在
        expected = session_cookies.get(platform, [])
        for name in expected:
            if any(c.get("name") == name for c in cookies):
                return True

        # 兜底：新出现大量 cookie（与初始加载时的差值 >= 5）
        return len(cookies) - initial_count >= 5
