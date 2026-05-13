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
STRICT_PAGE_VERIFICATION_PLATFORMS = {"jd", "taobao"}


SESSION_COOKIE_RULES = {
    "jd": {
        "web": [
            {"pt_key", "pt_pin"},
            {"thor", "pin"},
            {"thor", "unick"},
        ],
        "mobile_sign": [
            {"pt_key", "pt_pin"},
        ],
    },
    "taobao": {
        "web": [
            {"_tb_token_", "cookie2"},
            {"unb", "cookie2"},
            {"lgc", "tracknick"},
        ],
    },
    "pdd": {
        "web": [
            {"pdd_user_id"},
            {"PDDAccessToken"},
            {"api_uid", "pdd_user_uin"},
        ],
    },
    "miniapp": {
        "web": [
            {"session"},
            {"token"},
            {"sessionid"},
        ],
    },
}


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

    @staticmethod
    def cli_login_commands(platform: str) -> dict[str, str]:
        """Return cwd-safe CLI login commands for user-facing hints."""
        return {
            "repo_root": f"python src/login.py -p {platform}",
            "src_dir": f"python login.py -p {platform}",
        }

    @classmethod
    def cli_login_hint(cls, platform: str) -> str:
        """Return a user-facing login command hint that works from common cwd values."""
        commands = cls.cli_login_commands(platform)
        return (
            f"在仓库根目录运行 `{commands['repo_root']}`；"
            f"如果当前就在 `src` 目录，运行 `{commands['src_dir']}`"
        )

    # ================================
    # 弹窗扫码登录
    # ================================
    async def _wait_for_enter_and_save(
        self, platform: str, context: BrowserContext, page=None
    ) -> bool:
        """等待用户按 Enter 后获取 cookie 并保存。"""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, input)
        probe = await self.inspect_context_login(platform, context, preferred_page=page)
        if probe["confirmed"]:
            await self._save_cookies(
                platform,
                probe["cookies"],
                metadata=probe["metadata"],
            )
            print(f"\n✅ {platform} 登录成功！")
            return True
        print(f"\n❌ {platform} 未检测到有效登录态 cookie，登录可能失败")
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
                        success = await self._wait_for_enter_and_save(platform, context, page)
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
    async def _save_cookies(
        self,
        platform: str,
        cookies: list[dict],
        metadata: Optional[dict] = None,
    ) -> None:
        """保存 cookie 到本地文件。"""
        path = self._cookie_path(platform)
        payload: object = cookies
        if metadata:
            payload = {"cookies": cookies, "metadata": metadata}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"💾 Cookie 已保存: {path}")

    async def load_cookies(self, platform: str) -> Optional[list[dict]]:
        """从本地文件加载 cookie。"""
        path = self._cookie_path(platform)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("cookies"), list):
                return data["cookies"]
            return None
        except (json.JSONDecodeError, ValueError):
            return None

    async def load_session_metadata(self, platform: str) -> dict:
        """Load persisted session metadata when available."""
        path = self._cookie_path(platform)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("metadata"), dict):
                return data["metadata"]
            return {}
        except (json.JSONDecodeError, ValueError):
            return {}

    async def has_saved_session(self, platform: str) -> bool:
        """Return True only when saved cookies contain a real login session."""
        state = await self.get_saved_session_state(platform)
        return state["logged_in"]

    async def get_saved_session_state(self, platform: str) -> dict[str, object]:
        """Return a nuanced saved-session state for UI/logging decisions."""
        cookies = await self.load_cookies(platform)
        metadata = await self.load_session_metadata(platform)
        commands = self.cli_login_commands(platform)
        default = {
            "platform": platform,
            "logged_in": False,
            "verified": False,
            "cookie_count": len(cookies or []),
            "label": "未登录",
            "detail": self.cli_login_hint(platform),
            "login_command": commands["repo_root"],
            "login_commands": commands,
        }
        if not cookies:
            return default

        verified = metadata.get("login_verified") is True
        has_web = self._has_session_cookie(cookies, platform)
        has_mobile = self.has_required_cookie_group(cookies, platform, "mobile_sign")

        if platform == "jd":
            if has_mobile:
                return {
                    **default,
                    "logged_in": True,
                    "verified": True,
                    "label": "可自动执行",
                    "detail": "已具备 pt_key/pt_pin，可执行京东签到等移动任务",
                }
            if verified and has_web:
                return {
                    **default,
                    "logged_in": True,
                    "verified": True,
                    "label": "仅网页态",
                    "detail": "已验证网页登录，但缺少 pt_key/pt_pin，无法自动签到",
                }
            if has_web:
                return {
                    **default,
                    "label": "待验证",
                    "detail": "检测到旧京东网页登录 cookie，但尚未验证真实可用性，请重新登录一次",
                }
            return {
                **default,
                "label": "无效 cookie",
                "detail": "检测到京东 cookie，但不满足登录要求，请重新登录",
            }

        if platform in STRICT_PAGE_VERIFICATION_PLATFORMS:
            if verified and has_web:
                return {
                    **default,
                    "logged_in": True,
                    "verified": True,
                    "label": "已验证",
                    "detail": "已通过页面状态确认登录",
                }
            if has_web:
                return {
                    **default,
                    "label": "待验证",
                    "detail": "检测到网页登录 cookie，但未验证是否真实登录成功，请重新登录一次",
                }
            return {
                **default,
                "label": "无效 cookie",
                "detail": "检测到 cookie，但尚未形成可用登录态",
            }

        if verified or has_web:
            return {
                **default,
                "logged_in": True,
                "verified": verified,
                "label": "已登录" if verified else "会话可用",
                "detail": "已保存可用登录会话",
            }

        return {
            **default,
            "label": "无效 cookie",
            "detail": "检测到 cookie，但尚未形成可用登录态",
        }

    async def has_saved_capability(self, platform: str, capability: str) -> bool:
        """Return True when saved cookies satisfy a named platform capability."""
        cookies = await self.load_cookies(platform)
        if not cookies:
            return False
        return self.has_required_cookie_group(cookies, platform, capability)

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

    async def inspect_context_login(
        self,
        platform: str,
        context: BrowserContext,
        *,
        preferred_page=None,
    ) -> dict[str, object]:
        """Inspect cookies plus every live page in a context to confirm login."""
        cookies = await context.cookies()
        pages = self._unique_context_pages(context, preferred_page=preferred_page)
        fallback_page = preferred_page

        if self._has_session_cookie(cookies, platform) and platform not in STRICT_PAGE_VERIFICATION_PLATFORMS:
            page_url = getattr(preferred_page, "url", "") if preferred_page else ""
            page_text = await self._safe_page_text(preferred_page) if preferred_page else ""
            return {
                "confirmed": True,
                "cookies": cookies,
                "page": preferred_page,
                "page_url": page_url,
                "page_text": page_text,
                "metadata": self._build_session_metadata(platform, cookies, page_url, page_text),
            }

        for page in pages:
            page_url = getattr(page, "url", "") or ""
            page_text = await self._safe_page_text(page)
            if page_url or page_text:
                fallback_page = page
            if self.is_login_confirmed(platform, cookies, page_url=page_url, page_text=page_text):
                return {
                    "confirmed": True,
                    "cookies": cookies,
                    "page": page,
                    "page_url": page_url,
                    "page_text": page_text,
                    "metadata": self._build_session_metadata(platform, cookies, page_url, page_text),
                }

        fallback_url = getattr(fallback_page, "url", "") if fallback_page else ""
        fallback_text = await self._safe_page_text(fallback_page) if fallback_page else ""
        return {
            "confirmed": False,
            "cookies": cookies,
            "page": fallback_page,
            "page_url": fallback_url,
            "page_text": fallback_text,
            "metadata": self._build_session_metadata(platform, cookies, fallback_url, fallback_text),
        }

    # ================================
    # 工具方法
    # ================================
    @staticmethod
    def _has_session_cookie(cookies: list[dict], platform: str = "", initial_count: int = 0) -> bool:
        """Return whether cookies contain a platform-specific authenticated session."""
        return AuthManager.has_required_cookie_group(cookies, platform, "web")

    @staticmethod
    def has_required_cookie_group(cookies: list[dict], platform: str, capability: str) -> bool:
        """Return whether cookies satisfy a platform capability cookie group."""
        cookie_names = {c.get("name") for c in cookies if c.get("name")}
        capability_groups = SESSION_COOKIE_RULES.get(platform, {}).get(capability, [])
        for group in capability_groups:
            if group.issubset(cookie_names):
                return True

        return False

    @staticmethod
    def _unique_context_pages(context: BrowserContext, preferred_page=None) -> list:
        """Return de-duplicated context pages, keeping the preferred page first."""
        ordered: list = []
        if preferred_page is not None:
            ordered.append(preferred_page)
        pages = getattr(context, "pages", []) or []
        if callable(pages):
            pages = pages()
        seen: set[int] = set()
        unique: list = []
        for page in ordered + list(pages):
            if page is None:
                continue
            marker = id(page)
            if marker in seen:
                continue
            seen.add(marker)
            unique.append(page)
        return unique

    @staticmethod
    def login_challenge_reason(platform: str, page_url: str = "", page_text: str = "") -> str:
        """Return a human-readable reason when the current page is a login challenge."""
        compact = "".join(page_text.split())
        if platform == "jd":
            markers = ("验证一下", "购物无忧", "快速验证")
            if any(marker in compact for marker in markers):
                return "京东触发风控验证，请先在浏览器中完成“快速验证”"
        return ""

    @staticmethod
    def is_authenticated_page(platform: str, page_url: str = "", page_text: str = "") -> bool:
        """Return whether page content indicates an authenticated session."""
        compact = "".join(page_text.split())
        if platform == "pdd":
            login_markers = ("手机登录", "扫码登录", "发送验证码", "同意服务协议与隐私政策")
            if "login.html" in page_url or all(marker in compact for marker in login_markers[:3]):
                return False
            success_markers = ("拼小圈", "新提醒", "限时秒杀", "充值中心", "补贴多人团", "免费领水果")
            return any(marker in compact for marker in success_markers)

        if platform == "jd":
            if AuthManager.login_challenge_reason(platform, page_url=page_url, page_text=page_text):
                return False
            if "passport.jd.com" in page_url and ("扫码登录" in compact or "账户登录" in compact):
                return False
            success_markers = ("我的京东", "我的订单", "京豆", "收货地址", "退出登录")
            return any(marker in compact for marker in success_markers)

        if platform == "taobao":
            if "login.taobao.com" in page_url and ("扫码登录" in compact or "密码登录" in compact):
                return False
            success_markers = ("我的淘宝", "已买到的宝贝", "购物车", "收藏夹", "我的订单")
            return any(marker in compact for marker in success_markers)

        return False

    @classmethod
    def is_login_confirmed(
        cls,
        platform: str,
        cookies: list[dict],
        *,
        page_url: str = "",
        page_text: str = "",
    ) -> bool:
        """Return whether login is confirmed by cookies or page state."""
        if cls._has_session_cookie(cookies, platform) and platform not in STRICT_PAGE_VERIFICATION_PLATFORMS:
            return True
        if platform == "jd" and cls.has_required_cookie_group(cookies, platform, "mobile_sign"):
            return True
        return cls.is_authenticated_page(platform, page_url=page_url, page_text=page_text)

    @staticmethod
    async def _safe_page_text(page) -> str:
        """Best-effort page text extraction for login-state detection."""
        if page is None:
            return ""
        try:
            return await page.locator("body").inner_text(timeout=2000)
        except Exception:
            return ""

    @staticmethod
    def _build_session_metadata(
        platform: str,
        cookies: list[dict],
        page_url: str = "",
        page_text: str = "",
    ) -> dict:
        """Build persisted metadata for sessions confirmed by page state."""
        metadata: dict[str, object] = {}
        if AuthManager.is_authenticated_page(platform, page_url=page_url, page_text=page_text):
            metadata["login_verified"] = True
            metadata["verified_by"] = "page_state"
            metadata["page_url"] = page_url
        return metadata
