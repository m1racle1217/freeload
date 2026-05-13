# -*- coding: utf-8 -*-
"""主守护进程 — 启动 Watcher、Executor、Web 面板。"""

# ================================
# 导入依赖
# ================================
import asyncio
import socket
import signal
import sys
import logging

# ================================
# 修复 Windows 终端编码（支持 emoji 显示）
# ================================
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore
from pathlib import Path

# ================================
# 确保能找到 src 包
# ================================
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ================================
# 项目模块导入
# ================================
from src.config import Config
from src.event import EventQueue
from src.auth import AuthManager
from src.browser import BrowserPool
from src.executor import Executor
from src.storage import Storage
from src.notify.email import EmailNotifier


# ================================
# 日志配置
# ================================
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


# ================================
# 守护进程
# ================================
class Daemon:
    """守护进程，管理所有子组件。"""

    def __init__(self, config_path: str | None = None):
        # ================================
        # 配置
        # ================================
        self.config = Config(config_path) if config_path else Config()
        self.config.load()

        # ================================
        # 核心组件
        # ================================
        self.event_queue = EventQueue()
        self.auth = AuthManager()
        self.storage = Storage()
        self.browser_pool = BrowserPool(
            pool_size=self.config.get("browser", "pool_size", default=2),
            headless=self.config.get("browser", "headless", default=True),
        )
        self.executor = Executor(self.event_queue, self.browser_pool)

        # ================================
        # 通知器
        # ================================
        email_cfg = self.config.get("notify", "email", default={})
        self.notifier = EmailNotifier(
            smtp_host=email_cfg.get("smtp_host", "smtp.qq.com"),
            smtp_port=email_cfg.get("smtp_port", 465),
            use_ssl=email_cfg.get("use_ssl", True),
            from_addr=email_cfg.get("from_addr"),
            password=email_cfg.get("password"),
            to_addr=email_cfg.get("to_addr"),
        )

        # ================================
        # Watchers
        # ================================
        self._watchers: list = []
        self._web_server = None
        self._running = False
        self._actual_web_port = None

    # ================================
    # 启动
    # ================================
    async def start(self) -> None:
        """启动所有组件。"""
        print(f"\n{'='*50}")
        print("  🐑 薅羊毛自动化 v1.0")
        print(f"{'='*50}\n")

        # 1. 数据库
        await self.storage.initialize()

        # 2. 浏览器池
        await self.browser_pool.start()

        # 3. 检查各平台登录状态
        await self._check_logins()

        # 4. 注册 Watchers
        await self._register_watchers()

        # 5. 启动执行引擎
        asyncio.create_task(self.executor.run())

        # 6. 启动 Web 面板
        await self._start_web()

        # 7. 启动状态上报
        asyncio.create_task(self._status_reporter())

        self._running = True
        web_host = self.config.get("web", "host", default="127.0.0.1")
        web_port = self.config.get("web", "port", default=9528)
        print(f"\n{'='*50}")
        print("  ✅ 守护进程已启动")
        if hasattr(self, '_actual_web_port') and self._actual_web_port != web_port:
            print(f"  🌐 Web 面板: http://{web_host}:{self._actual_web_port} (配置端口 {web_port} 被占用)")
        else:
            print(f"  🌐 Web 面板: http://{web_host}:{web_port}")
        print(f"{'='*50}\n")

    async def shutdown(self, sig: signal.Signals | None = None) -> None:
        """优雅关闭所有组件。"""
        signame = sig.name if sig else "manual"
        print(f"\n🛑 收到 {signame} 信号，正在关闭...")

        self._running = False

        # 停止 Web 服务
        if self._web_server:
            self._web_server.should_exit = True

        # 停止执行引擎
        await self.executor.stop()

        # 停止浏览器池
        await self.browser_pool.stop()

        # 关闭数据库
        await self.storage.close()

        print("👋 已安全退出")
        sys.exit(0)

    # ================================
    # 子组件管理
    # ================================
    async def _check_logins(self) -> None:
        """检查各平台 cookie 是否有效。"""
        platforms = ["jd", "taobao", "pdd", "miniapp"]
        for platform in platforms:
            cookies = await self.auth.load_cookies(platform)
            if cookies:
                count = len(cookies)
                logger.info("[登录] %s: cookie 有效 (%d 条)", platform, count)
            else:
                logger.warning("[登录] %s: 未登录，请运行 python src/login.py -p %s", platform, platform)

    async def _register_watchers(self) -> None:
        """根据配置启动各平台 Watcher。"""
        # 延迟导入避免循环依赖
        from src.watchers.jd_watcher import JDWatcher

        # 京东
        jd_enabled = self.config.get("platforms", "jd", "enabled", default=True)
        jd_interval = self.config.get("platforms", "jd", "poll_interval", default=30)
        if jd_enabled:
            watcher = JDWatcher(self.event_queue, poll_interval=jd_interval)
            self._watchers.append(watcher)
            asyncio.create_task(watcher.run())
            logger.info("[Watcher] 京东监控器已注册 (间隔 %ds)", jd_interval)

        logger.info("共注册 %d 个 Watcher", len(self._watchers))

    async def _start_web(self) -> None:
        """启动 FastAPI Web 服务。"""
        try:
            import uvicorn
            from src.web.server import create_app

            host = self.config.get("web", "host", default="127.0.0.1")
            port = self.config.get("web", "port", default=9528)

            # 端口被占用时自动 +1 重试，最多试 10 次
            actual_port = await self._find_free_port(host, port, max_attempts=10)
            if actual_port != port:
                logger.warning("[Web] 端口 %d 被占用，已切换至 %d", port, actual_port)
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

        except Exception as e:
            logger.warning("[Web] Web 面板启动失败: %s", e)

    @staticmethod
    async def _find_free_port(host: str, start_port: int, max_attempts: int = 10) -> int:
        """从 start_port 开始检测，返回第一个可用端口。"""
        loop = asyncio.get_event_loop()
        for port in range(start_port, start_port + max_attempts):
            try:
                sock = await loop.run_in_executor(None, socket.socket, socket.AF_INET, socket.SOCK_STREAM)
                try:
                    sock.settimeout(1)
                    # connect_ex 返回 0 表示端口被占用
                    result = await loop.run_in_executor(None, sock.connect_ex, (host, port))
                    if result != 0:
                        return port
                finally:
                    sock.close()
            except Exception:
                return port
        return start_port

    async def _status_reporter(self) -> None:
        """定时在日志中输出运行状态。"""
        while self._running:
            await asyncio.sleep(3600)  # 每小时
            logger.info(
                "[状态] Watchers: %d | 已处理事件: %d | 成功: %d | 池可用: %d",
                len(self._watchers),
                self.executor.status_info()["processed"],
                self.executor.status_info()["success"],
                await self.browser_pool.available_count(),
            )

    # ================================
    # Watcher / Executor 访问
    # ================================
    def get_watchers(self) -> list:
        return list(self._watchers)

    def get_executor(self) -> Executor:
        return self.executor

    def get_event_queue(self) -> EventQueue:
        return self.event_queue


# ================================
# 入口
# ================================
async def main():
    daemon = Daemon()

    # ================================
    # 注册信号处理（Windows 不支持 signal handler）
    # ================================
    if sys.platform != "win32":
        loop = asyncio.get_event_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, lambda s=sig: asyncio.create_task(daemon.shutdown(s)))

    try:
        await daemon.start()
        # 保持运行
        while daemon._running:
            await asyncio.sleep(1)
    except KeyboardInterrupt:
        await daemon.shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
