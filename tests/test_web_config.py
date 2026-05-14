import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import yaml
from fastapi.testclient import TestClient

from src.config import Config
from src.web.server import create_app


class DummyStorage:
    async def get_today_earnings(self):
        return 0

    async def get_total_earnings(self):
        return 0

    async def get_recent_tasks(self, limit=50):
        return []


class DummyExecutor:
    def __init__(self):
        self._running = True
        self.last_event = None

    def status_info(self):
        return {"running": self._running, "processed": 0, "success": 0, "recent": []}

    async def _execute_event(self, event):
        self.last_event = event
        return {"success": True, "detail": f"executed:{event.event_type}", "value": event.value}


class DummyAuth:
    async def has_saved_session(self, platform):
        return False

    async def get_saved_session_state(self, platform):
        state = {
            "platform": platform,
            "logged_in": False,
            "verified": False,
            "label": "未登录",
            "detail": "请重新登录",
            "cookie_count": 0,
            "capabilities": {},
            "available_actions": {},
        }
        if platform == "jd":
            state["logged_in"] = True
            state["verified"] = True
            state["label"] = "仅网页态"
            state["detail"] = "已验证网页登录，但缺少 pt_key/pt_pin"
            state["capabilities"] = {"web": True, "mobile_sign": False}
            state["available_actions"] = {
                "sign_in": {"available": False, "reason": "缺少 pt_key/pt_pin"},
                "coupon": {"available": True, "reason": ""},
            }
        return state

    @staticmethod
    def _has_session_cookie(cookies, platform):
        names = {cookie.get("name") for cookie in cookies}
        if platform == "pdd":
            return False
        return False

    @staticmethod
    def is_login_confirmed(platform, cookies, page_url="", page_text=""):
        names = {cookie.get("name") for cookie in cookies}
        return (
            platform == "pdd"
            and {"api_uid", "pdd_vds"}.issubset(names)
            and "拼小圈" in page_text
        )

    @staticmethod
    def _build_session_metadata(platform, cookies, page_url="", page_text=""):
        return {"login_verified": True, "verified_by": "page_state"}

    @staticmethod
    async def _safe_page_text(page):
        return await page.locator("body").inner_text()

    async def _save_cookies(self, platform, cookies, metadata=None):
        self.saved = (platform, cookies, metadata)

    async def inspect_context_login(self, platform, context, preferred_page=None):
        cookies = await context.cookies()
        page = preferred_page
        page_text = await self._safe_page_text(page)
        return {
            "confirmed": self.is_login_confirmed(
                platform,
                cookies,
                page_url=page.url,
                page_text=page_text,
            ),
            "cookies": cookies,
            "page": page,
            "page_url": page.url,
            "page_text": page_text,
            "metadata": self._build_session_metadata(
                platform,
                cookies,
                page_url=page.url,
                page_text=page_text,
            ),
        }


class DummyDaemon:
    def __init__(self, config_path: Path):
        self.config = Config(config_path)
        self.config.load()
        self.storage = DummyStorage()
        self.executor = DummyExecutor()
        self.auth = DummyAuth()
        self._running = True
        self.reload_count = 0
        self.platform_updates = []
        self.runtime_updates = []

    def get_watchers(self):
        return []

    def get_executor(self):
        return self.executor

    def reload_config(self):
        self.reload_count += 1
        self.config.load()

    def get_watcher(self, platform):
        return None

    async def set_platform_enabled(self, platform, enabled):
        self.platform_updates.append((platform, enabled))
        return {"platform": platform, "enabled": enabled, "watcher_registered": False}

    async def set_runtime_enabled(self, enabled):
        self.runtime_updates.append(enabled)
        self.executor._running = enabled
        return {"enabled": enabled, "executor_running": enabled}


class DummyLocator:
    def __init__(self, text):
        self._text = text

    async def inner_text(self, timeout=None):
        return self._text


class DummyPage:
    def __init__(self, url, text):
        self.url = url
        self._text = text

    def locator(self, selector):
        return DummyLocator(self._text)


class ConfigApiTests(unittest.TestCase):
    def make_config_file(self, directory: str) -> Path:
        path = Path(directory) / "config.yaml"
        path.write_text(
            yaml.safe_dump(
                {
                    "notify": {
                        "email": {
                            "smtp_host": "smtp.qq.com",
                            "smtp_port": 465,
                            "use_ssl": True,
                            "from_addr": "from@example.com",
                            "password": "secret-token",
                            "to_addr": "to@example.com",
                        }
                    },
                    "platforms": {
                        "jd": {"enabled": True, "poll_interval": 30, "value_threshold": 1.0},
                        "taobao": {"enabled": False, "poll_interval": 60, "value_threshold": 1.0},
                        "pdd": {"enabled": False, "poll_interval": 60, "value_threshold": 1.0},
                        "miniapp": {"enabled": True, "poll_interval": 300, "value_threshold": 0.5},
                    },
                    "web": {"host": "127.0.0.1", "port": 9528},
                    "browser": {"pool_size": 2, "headless": False},
                },
                allow_unicode=True,
                sort_keys=False,
            ),
            encoding="utf-8",
        )
        return path

    def test_get_api_config_returns_safe_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = DummyDaemon(self.make_config_file(tmp))
            client = TestClient(create_app(daemon))

            response = client.get("/api/config")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["notify"]["email"]["password"], "")

    def test_post_api_config_saves_and_reloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = self.make_config_file(tmp)
            daemon = DummyDaemon(config_path)
            client = TestClient(create_app(daemon))

            payload = {
                "notify": {
                    "email": {
                        "smtp_host": "smtp.163.com",
                        "smtp_port": 465,
                        "use_ssl": True,
                        "from_addr": "saved@example.com",
                        "password": "",
                        "to_addr": "target@example.com",
                    }
                },
                "platforms": {
                    "jd": {"enabled": True, "poll_interval": 35, "value_threshold": 1.1},
                    "taobao": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
                    "pdd": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
                    "miniapp": {"enabled": True, "poll_interval": 300, "value_threshold": 0.5},
                },
                "web": {"host": "127.0.0.1", "port": 9528},
                "browser": {"pool_size": 2, "headless": False},
            }

            response = client.post("/api/config", json=payload)

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            self.assertEqual(daemon.reload_count, 1)
            stored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["notify"]["email"]["password"], "secret-token")
            self.assertEqual(stored["platforms"]["jd"]["poll_interval"], 35)

    def test_post_api_config_returns_field_error_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = DummyDaemon(self.make_config_file(tmp))
            client = TestClient(create_app(daemon))

            response = client.post(
                "/api/config",
                json={
                    "notify": {
                        "email": {
                            "smtp_host": "",
                            "smtp_port": 70000,
                            "use_ssl": True,
                            "from_addr": "bad",
                            "password": "",
                            "to_addr": "",
                        }
                    },
                    "platforms": {
                        "jd": {"enabled": True, "poll_interval": 0, "value_threshold": -1},
                        "taobao": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
                        "pdd": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
                        "miniapp": {"enabled": True, "poll_interval": 300, "value_threshold": 0.5},
                    },
                    "web": {"host": "127.0.0.1", "port": 9528},
                    "browser": {"pool_size": 2, "headless": False},
                },
            )

            self.assertEqual(response.status_code, 422)
            self.assertFalse(response.json()["ok"])
            self.assertIn("error", response.json())

    def test_login_status_saves_pdd_cookie_and_returns_logged_in(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = DummyDaemon(self.make_config_file(tmp))
            daemon.auth = DummyAuth()
            client = TestClient(create_app(daemon))

            cookies = [
                {"name": "api_uid", "value": "x", "domain": ".yangkeduo.com", "path": "/"},
                {"name": "pdd_vds", "value": "y", "domain": ".yangkeduo.com", "path": "/"},
            ]
            context = AsyncMock()
            context.cookies = AsyncMock(return_value=cookies)
            page = DummyPage(
                "https://mobile.yangkeduo.com/index.html",
                "拼小圈 新提醒 限时秒杀",
            )

            app = client.app
            status_handler = next(
                route.endpoint
                for route in app.router.routes
                if getattr(route, "path", None) == "/api/login/{platform}/status"
            )
            login_sessions = status_handler.__closure__[2].cell_contents
            login_sessions["pdd_test"] = {
                "page": page,
                "context": context,
                "browser": AsyncMock(),
                "playwright": AsyncMock(),
                "started_at": datetime.now(timezone.utc),
                "logged_in": False,
                "platform": "pdd",
            }

            response = client.get("/api/login/pdd/status", params={"session_id": "pdd_test"})

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["logged_in"])
            self.assertEqual(daemon.auth.saved[0], "pdd")

    def test_set_platform_enabled_endpoint_updates_daemon(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = DummyDaemon(self.make_config_file(tmp))
            client = TestClient(create_app(daemon))

            response = client.post("/api/platforms/taobao/enabled", json={"enabled": True})

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            self.assertEqual(daemon.platform_updates, [("taobao", True)])

    def test_run_jd_action_endpoint_executes_real_action(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = DummyDaemon(self.make_config_file(tmp))
            client = TestClient(create_app(daemon))

            response = client.post("/api/platforms/jd/run", json={"action": "coupon"})

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            self.assertEqual(response.json()["result"]["detail"], "executed:coupon")

    def test_run_jd_flash_sale_endpoint_passes_target_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = DummyDaemon(self.make_config_file(tmp))
            client = TestClient(create_app(daemon))

            response = client.post(
                "/api/platforms/jd/run",
                json={
                    "action": "flash_sale",
                    "url": "https://item.jd.com/123.html",
                    "title": "指定商品秒杀",
                    "value": 88.0,
                },
            )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            self.assertEqual(daemon.executor.last_event.url, "https://item.jd.com/123.html")
            self.assertEqual(daemon.executor.last_event.title, "指定商品秒杀")
            self.assertEqual(daemon.executor.last_event.value, 88.0)

    def test_run_jd_sign_in_endpoint_rejects_when_capability_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = DummyDaemon(self.make_config_file(tmp))
            client = TestClient(create_app(daemon))

            response = client.post("/api/platforms/jd/run", json={"action": "sign_in"})

            self.assertEqual(response.status_code, 409)
            self.assertFalse(response.json()["ok"])
            self.assertIn("pt_key/pt_pin", response.json()["error"])

    def test_run_jd_sign_in_endpoint_allows_persistent_profile_state(self):
        class PersistentDummyAuth(DummyAuth):
            async def get_saved_session_state(self, platform):
                state = await super().get_saved_session_state(platform)
                if platform == "jd":
                    state["label"] = "持久会话"
                    state["detail"] = "已存在可复用浏览器持久会话"
                    state["available_actions"]["sign_in"] = {"available": True, "reason": ""}
                return state

        with tempfile.TemporaryDirectory() as tmp:
            daemon = DummyDaemon(self.make_config_file(tmp))
            daemon.auth = PersistentDummyAuth()
            client = TestClient(create_app(daemon))

            response = client.post("/api/platforms/jd/run", json={"action": "sign_in"})

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            self.assertEqual(response.json()["result"]["detail"], "executed:sign_in")

    def test_set_runtime_enabled_endpoint_updates_daemon(self):
        with tempfile.TemporaryDirectory() as tmp:
            daemon = DummyDaemon(self.make_config_file(tmp))
            client = TestClient(create_app(daemon))

            response = client.post("/api/runtime/enabled", json={"enabled": False})

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["ok"])
            self.assertEqual(daemon.runtime_updates, [False])
