# -*- coding: utf-8 -*-
"""统一反检测模块 — 集成 playwright-stealth + 自定义伪装。"""

# ================================
# 导入依赖
# ================================
import sys
from pathlib import Path
from typing import Optional

from playwright.async_api import Browser, BrowserContext
from playwright_stealth import Stealth


# ================================
# 常量
# ================================
# Chrome 130 Windows 11 UA
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)
DEFAULT_VIEWPORT = {"width": 1280, "height": 800}
DEFAULT_LOCALE = "zh-CN"
STEALTH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-infobars",
    "--disable-dev-shm-usage",
    "--disable-component-update",
    "--disable-background-timer-throttling",
    "--disable-renderer-backgrounding",
]


# ================================
# 浏览器初始脚本
# ================================
INIT_SCRIPTS = [
    # 隐藏 webdriver
    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})",
    # 伪装 Chrome 运行环境
    "Object.defineProperty(navigator, 'platform', {get: () => 'Win32'})",
    "Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8})",
    "Object.defineProperty(navigator, 'deviceMemory', {get: () => 8})",
    # 伪装 languages
    "Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']})",
    # 移除 webdriver 痕迹
    "delete navigator.__proto__.webdriver",
    "window.chrome = {runtime: {}}",
]


# ================================
# 创建 Stealth 实例（模块级单例）
# ================================
_stealth = Stealth()


# ================================
# 浏览器通道检测
# ================================
import shutil


def _detect_available_channel() -> Optional[str]:
    """检测系统已安装的 Chrome/Edge，返回 Playwright channel 名称。"""
    # 优先级: chrome > msedge
    if shutil.which("chrome") or any(
        p.exists()
        for p in [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
        ]
    ):
        return "chrome"
    if shutil.which("msedge") or any(
        p.exists()
        for p in [
            Path("C:/Program Files/Microsoft/Edge/Application/msedge.exe"),
            Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
        ]
    ):
        return "msedge"
    return None


# ================================
# 公共 API
# ================================
def get_stealth_launch_args(
    headless: bool = True,
    use_system_chrome: bool = False,
) -> dict:
    """返回 playwright.chromium.launch() 的参数字典。

    如果 use_system_chrome=True 且系统安装了 Chrome/Edge，
    则使用系统浏览器（TLS 指纹不同，更难被检测）。
    """
    kwargs: dict = {
        "headless": headless,
        "args": list(STEALTH_ARGS),
    }
    if use_system_chrome:
        channel = _detect_available_channel()
        if channel:
            kwargs["channel"] = channel
    return kwargs


def get_stealth_context_kwargs() -> dict:
    """返回 browser.new_context() 的参数字典。"""
    return {
        "user_agent": DEFAULT_UA,
        "viewport": dict(DEFAULT_VIEWPORT),
        "locale": DEFAULT_LOCALE,
    }


def get_persistent_context_kwargs(
    headless: bool = True,
    use_system_chrome: bool = False,
) -> dict:
    """Return chromium.launch_persistent_context() kwargs."""
    kwargs = get_stealth_launch_args(headless, use_system_chrome)
    kwargs.update(get_stealth_context_kwargs())
    return kwargs


async def apply_stealth_to_page(page) -> None:
    """对已创建的 page 应用 init scripts。"""
    for script in INIT_SCRIPTS:
        await page.add_init_script(script)


async def apply_stealth_to_context(context: BrowserContext) -> None:
    """对已创建的 context 应用 stealth + init scripts。"""
    # playwright-stealth 的全局 stealth
    await _stealth.apply_stealth_async(context)
    # 额外 init scripts
    from playwright.async_api import Page
    context.on("page", lambda page: _on_page_created(page))


def _on_page_created(page) -> None:
    """为新 page 自动注入 init scripts。"""
    import asyncio
    asyncio.ensure_future(_inject_init_scripts(page))


async def _inject_init_scripts(page) -> None:
    """注入自定义 init scripts。"""
    for script in INIT_SCRIPTS:
        try:
            await page.add_init_script(script)
        except Exception:
            pass


async def create_stealth_browser(
    playwright,
    headless: bool = True,
    use_system_chrome: bool = False,
) -> Browser:
    """创建应用了反检测参数的浏览器实例。"""
    kwargs = get_stealth_launch_args(headless, use_system_chrome)
    browser = await playwright.chromium.launch(**kwargs)
    return browser


async def create_stealth_context(
    browser: Browser,
    *,
    inject_cookies: Optional[list[dict]] = None,
) -> BrowserContext:
    """创建应用了全方位反检测的浏览器上下文。

    Args:
        browser: 浏览器实例
        inject_cookies: 可选的 cookie 列表，创建后自动注入

    Returns:
        已应用 stealth 的 BrowserContext
    """
    context = await browser.new_context(**get_stealth_context_kwargs())

    # 应用 playwright-stealth（覆盖 20+ 检测向量）
    await _stealth.apply_stealth_async(context)

    # 应用自定义 init scripts
    context.on("page", _on_page_created)

    # 注入 cookie
    if inject_cookies:
        try:
            await context.add_cookies(inject_cookies)
        except Exception:
            pass

    return context


# ================================
# 人类行为模拟
# ================================
import asyncio
import random

JD_WARMUP_URL = "https://m.jd.com"


class HumanBehavior:
    """模拟人类操作行为，降低风控触发率。"""

    @staticmethod
    async def random_delay(min_ms: int, max_ms: int) -> None:
        await asyncio.sleep(random.randint(min_ms, max_ms) / 1000.0)

    @staticmethod
    async def mouse_jitter(page, moves: int = 2) -> None:
        for _ in range(moves):
            await page.mouse.move(random.randint(120, 900), random.randint(120, 600))
            await asyncio.sleep(random.uniform(0.1, 0.3))

    @staticmethod
    async def scroll(page) -> None:
        await page.evaluate(f"window.scrollBy(0, {random.randint(120, 480)})")
        await asyncio.sleep(random.uniform(0.4, 1.2))

    @staticmethod
    async def warm_jd(page) -> None:
        try:
            await page.goto(JD_WARMUP_URL, wait_until="domcontentloaded", timeout=10000)
            await asyncio.sleep(random.uniform(1.5, 3.5))
            await HumanBehavior.scroll(page)
        except Exception:
            pass
