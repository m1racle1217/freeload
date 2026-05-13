# -*- coding: utf-8 -*-
"""Main daemon process for watchers, executor, and web UI."""

import asyncio
import logging
import signal
import socket
import sys
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.auth import AuthManager
from src.browser import BrowserPool
from src.config import Config
from src.event import EventQueue
from src.executor import Executor
from src.notify.email import EmailNotifier
from src.storage import Storage


LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOG_DIR / "freeload.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger("freeload")


class Daemon:
    """Manage all long-running components."""

    def __init__(self, config_path: str | None = None):
        self.config = Config(config_path) if config_path else Config()
        self.config.load()

        self.event_queue = EventQueue()
        self.auth = AuthManager()
        self.storage = Storage()
        self.browser_pool = BrowserPool(
            pool_size=self.config.get("browser", "pool_size", default=2),
            headless=self.config.get("browser", "headless", default=True),
        )
        self.executor = Executor(self.event_queue, self.browser_pool)

        email_cfg = self.config.get("notify", "email", default={})
        self.notifier = EmailNotifier(
            smtp_host=email_cfg.get("smtp_host", "smtp.qq.com"),
            smtp_port=email_cfg.get("smtp_port", 465),
            use_ssl=email_cfg.get("use_ssl", True),
            from_addr=email_cfg.get("from_addr"),
            password=email_cfg.get("password"),
            to_addr=email_cfg.get("to_addr"),
        )

        self._watchers: list = []
        self._web_server = None
        self._running = False
        self._actual_web_port = None

    async def start(self) -> None:
        """Start all managed services."""
        print(f"\n{'=' * 50}")
        print("  Freeload 自动化 v1.0")
        print(f"{'=' * 50}\n")

        await self.storage.initialize()
        await self.browser_pool.start()
        await self._check_logins()
        await self._register_watchers()
        self._register_handlers()

        asyncio.create_task(self.executor.run())
        await self._start_web()
        asyncio.create_task(self._status_reporter())

        self._running = True
        web_host = self.config.get("web", "host", default="127.0.0.1")
        web_port = self.config.get("web", "port", default=9528)
        print(f"\n{'=' * 50}")
        print("  守护进程已启动")
        if hasattr(self, "_actual_web_port") and self._actual_web_port != web_port:
            print(
                f"  Web 面板: http://{web_host}:{self._actual_web_port} "
                f"(配置端口 {web_port} 已被占用)"
            )
        else:
            print(f"  Web 面板: http://{web_host}:{web_port}")
        print(f"{'=' * 50}\n")

    async def shutdown(self, sig: signal.Signals | None = None) -> None:
        """Shut down all managed services."""
        signame = sig.name if sig else "manual"
        print(f"\n收到 {signame} 信号，正在关闭...")

        self._running = False

        if self._web_server:
            self._web_server.should_exit = True

        await self.executor.stop()
        await self.browser_pool.stop()
        await self.storage.close()

        print("已安全退出")
        sys.exit(0)

    async def _check_logins(self) -> None:
        """Check whether saved cookies are valid for each platform."""
        for platform in ["jd", "taobao", "pdd", "miniapp"]:
            cookies = await self.auth.load_cookies(platform)
            if cookies and self.auth._has_session_cookie(cookies, platform):
                logger.info("[登录] %s: cookie 有效 (%d 条)", platform, len(cookies))
            else:
                logger.warning(
                    "[登录] %s: 未登录，请运行 python src/login.py -p %s",
                    platform,
                    platform,
                )

    async def _register_watchers(self) -> None:
        """Start enabled platform watchers from config."""
        from src.watchers.jd_watcher import JDWatcher

        jd_enabled = self.config.get("platforms", "jd", "enabled", default=True)
        jd_interval = self.config.get("platforms", "jd", "poll_interval", default=30)
        if jd_enabled:
            watcher = JDWatcher(
                self.event_queue,
                poll_interval=jd_interval,
                browser_pool=self.browser_pool,
            )
            self._watchers.append(watcher)
            asyncio.create_task(watcher.run())
            logger.info("[Watcher] 京东监控器已注册 (间隔 %ds)", jd_interval)

        logger.info("共注册 %d 个 Watcher", len(self._watchers))

    def _register_handlers(self) -> None:
        """Register task handlers with the executor."""
        from src.handlers import JDCouponHandler, JDFlashSaleHandler, JDSignInHandler

        self.executor.register_handler("jd:sign_in", JDSignInHandler(self.browser_pool))
        self.executor.register_handler("jd:flash_sale", JDFlashSaleHandler(self.browser_pool))
        self.executor.register_handler("jd:coupon", JDCouponHandler(self.browser_pool))
        logger.info("[处理器] 京东任务处理器已注册 (签到/秒杀/领券)")

    async def _start_web(self) -> None:
        """Start the FastAPI web service."""
        try:
            import uvicorn

            from src.web.server import create_app

            host = self.config.get("web", "host", default="127.0.0.1")
            port = self.config.get("web", "port", default=9528)

            actual_port = await self._find_free_port(host, port, max_attempts=10)
            if actual_port != port:
                logger.warning("[Web] 端口 %d 已被占用，已切换至 %d", port, actual_port)
            self._actual_web_port = actual_port

            app = create_app(self)
            config = uvicorn.Config(
                app,
                host=host,
                port=actual_port,
                log_level="warning",
                access_log=False,
            )
            self._web_server = uvicorn.Server(config)
            asyncio.create_task(self._web_server.serve())
        except Exception as exc:
            logger.warning("[Web] Web 面板启动失败: %s", exc)

    @staticmethod
    async def _find_free_port(host: str, start_port: int, max_attempts: int = 10) -> int:
        """Find the first available port from a starting point."""
        loop = asyncio.get_event_loop()
        for port in range(start_port, start_port + max_attempts):
            try:
                sock = await loop.run_in_executor(
                    None, socket.socket, socket.AF_INET, socket.SOCK_STREAM
                )
                try:
                    sock.settimeout(1)
                    result = await loop.run_in_executor(None, sock.connect_ex, (host, port))
                    if result != 0:
                        return port
                finally:
                    sock.close()
            except Exception:
                return port
        return start_port

    async def _status_reporter(self) -> None:
        """Write periodic status summaries to the log."""
        while self._running:
            await asyncio.sleep(3600)
            logger.info(
                "[状态] Watchers: %d | 已处理事件: %d | 成功: %d | 池可用: %d",
                len(self._watchers),
                self.executor.status_info()["processed"],
                self.executor.status_info()["success"],
                await self.browser_pool.available_count(),
            )

    def get_watchers(self) -> list:
        return list(self._watchers)

    def get_executor(self) -> Executor:
        return self.executor

    def get_event_queue(self) -> EventQueue:
        return self.event_queue

    def reload_config(self) -> None:
        """Reload config and refresh components that read it at runtime."""
        self.config.load()
        email_cfg = self.config.get("notify", "email", default={})
        self.notifier.smtp_host = email_cfg.get("smtp_host", "smtp.qq.com")
        self.notifier.smtp_port = email_cfg.get("smtp_port", 465)
        self.notifier.use_ssl = email_cfg.get("use_ssl", True)
        self.notifier.from_addr = email_cfg.get("from_addr")
        self.notifier.password = email_cfg.get("password")
        self.notifier.to_addr = email_cfg.get("to_addr")
        self.browser_pool.pool_size = self.config.get("browser", "pool_size", default=2)
        self.browser_pool.headless = self.config.get("browser", "headless", default=True)


async def _auto_restart() -> None:
    """Restart an older freeload instance if it already owns the default ports."""
    ports = [9527, 9528]
    for port in ports:
        try:
            loop = asyncio.get_event_loop()
            sock = await loop.run_in_executor(
                None, socket.socket, socket.AF_INET, socket.SOCK_STREAM
            )
            try:
                sock.settimeout(1)
                result = await loop.run_in_executor(
                    None, sock.connect_ex, ("127.0.0.1", port)
                )
                if result != 0:
                    continue
            finally:
                sock.close()

            import urllib.request

            request = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/status", method="GET"
            )
            try:
                response = await loop.run_in_executor(
                    None, lambda: urllib.request.urlopen(request, timeout=3)
                )
                body = response.read().decode()
                if '"running":true' in body or '"running": true' in body:
                    pid = None
                    if sys.platform == "win32":
                        import subprocess

                        result = await loop.run_in_executor(
                            None,
                            lambda: subprocess.run(
                                ["netstat", "-ano"], capture_output=True, text=True
                            ),
                        )
                        for line in result.stdout.splitlines():
                            if f"127.0.0.1:{port}" in line and "LISTENING" in line:
                                parts = line.strip().split()
                                if parts:
                                    pid = parts[-1]
                                    break
                        if pid:
                            logger.info(
                                "[启动] 检测到旧 freeload 实例 (PID %s, 端口 %d)，正在重启...",
                                pid,
                                port,
                            )
                            await loop.run_in_executor(
                                None,
                                lambda: subprocess.run(
                                    ["taskkill", "/F", "/PID", str(pid)],
                                    capture_output=True,
                                    text=True,
                                ),
                            )
                    else:
                        import subprocess

                        result = await loop.run_in_executor(
                            None,
                            lambda: subprocess.run(
                                ["lsof", "-ti", f"tcp:{port}"],
                                capture_output=True,
                                text=True,
                            ),
                        )
                        pid = result.stdout.strip()
                        if pid:
                            logger.info(
                                "[启动] 检测到旧 freeload 实例 (PID %s, 端口 %d)，正在重启...",
                                pid,
                                port,
                            )
                            await loop.run_in_executor(
                                None,
                                lambda: subprocess.run(["kill", "-9", pid], capture_output=True),
                            )
                    await asyncio.sleep(2)
                    logger.info("[启动] 旧实例已清理")
            except Exception:
                pass
        except Exception:
            pass


async def main():
    await _auto_restart()
    daemon = Daemon()

    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(daemon.shutdown(s)))

    try:
        await daemon.start()
        while daemon._running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await daemon.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
