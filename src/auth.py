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
    async def _wait_for_enter_and_save(
        self, platform: str, context: BrowserContext
    ) -> bool:
        """等待用户按 Enter 后获取 cookie 并保存。"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input)
        cookies = await context.cookies()
        if len(cookies) > 0:
            await self._save_cookies(platform, cookies)
            print(f"\n✅ {platform} 登录成功！")
            return True
        print(f"\n❌ {platform} 未检测到 cookie，登录可能失败")
        return False

    async def login_platform(self, platform: str) -> bool:
        """弹出可见浏览器（带反检测伪装），等待用户扫码登录。

        流程：
        1. 先用 Playwright 内置 Chromium + stealth 尝试
        2. 全部 URL 都被拦截时，尝试用系统 Chrome/Edge（TLS 指纹不同）
        3. 给出手动导入 cookie 的提示
        """
        from src.stealth import (
            create_stealth_browser, create_stealth_context, _detect_available_channel,
        )

        login_url = self._platform_domain(platform)
        fallbacks = self._fallback_urls(platform)
        urls_to_try = [login_url] + fallbacks
        system_channel = _detect_available_channel()

        print(f"\n{'='*40}")
        print(f"  平台: {platform}")
        print(f"{'='*40 if False else ''}")
        print(f"🖥️  正在尝试访问 {login_url}...")
        print("📱 请在浏览器中扫码或输入账号密码完成登录")
        print("✅ 登录完成后请回到本窗口按 Enter 键继续")
        print("⏳ 浏览器保持打开，不设超时\n")

        async with async_playwright() as p:
            last_error = ""
            for attempt, use_system in [(1, False), (2, True)]:
                if attempt == 2 and not system_channel:
                    break  # 没有系统浏览器可用

                mode = f"Playwright Chromium" if not use_system else f"系统 {system_channel}"
                print(f"\n--- 尝试方式: {mode} ---")

                browser = await create_stealth_browser(
                    p, headless=False, use_system_chrome=use_system,
                )
                context = await create_stealth_context(browser)
                page = await context.new_page()

                for idx, url in enumerate(urls_to_try):
                    try:
                        if idx > 0:
                            print(f"🔁 备选 {idx}/{len(urls_to_try)-1}: {url}")
                        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        success = await self._wait_for_enter_and_save(platform, context)
                        if success:
                            return True
                        # 有 response 但无 cookie → 尝试下一个 URL
                        continue
                    except Exception as e:
                        last_error = str(e)
                        if "ERR_CONNECTION_CLOSED" in last_error or "ERR_CONNECTION_REFUSED" in last_error:
                            if idx == 0 and fallbacks:
                                pass  # 会尝试 fallback
                        elif idx > 0:
                            print(f"  ❌ 备选失败: {e}")
                        continue

                await page.close()
                await context.close()
                await browser.close()

                # 如果内置 Chromium 失败且系统浏览器可用，自动重试
                if attempt == 1 and system_channel:
                    print(f"\n🔄 Playwright 内置浏览器被拦截，尝试使用系统 {system_channel}...")

            print(f"\n❌ {platform} 登录失败: {last_error}")
            if platform in ("taobao", "pdd"):
                print(f"💡 提示: {platform} 对自动化浏览器限制较严")
                print(f"   1. 手动复制 cookie 到 cookies/{platform}.json")
                print(f"   2. 用普通浏览器打开 {login_url} 手动登录后导出 cookie")
            else:
                print(f"💡 请检查网络或重试")
            return False

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
