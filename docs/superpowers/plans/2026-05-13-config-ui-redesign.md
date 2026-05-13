# Config UI Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a polished WebUI with system-aware theming and a fully form-based configuration editor that saves validated changes back to `config/config.yaml`.

**Architecture:** Keep the existing FastAPI + Jinja + vanilla JS stack. Add schema-aware config read/write helpers in `src/config.py`, expose them through focused config APIs in `src/web/server.py`, and rebuild the shared shell and config page in templates so the editor feels integrated rather than bolted on.

**Tech Stack:** Python 3, FastAPI, Jinja2, PyYAML, vanilla JavaScript, `unittest`, `fastapi.testclient`

---

## File Structure

- Modify: `src/config.py`
  Responsibility: config schema defaults, safe form payload, validation, save-to-YAML, env override metadata.

- Modify: `src/daemon.py`
  Responsibility: expose a reload path so saved config can be applied to in-memory services without rebuilding the whole process object manually.

- Modify: `src/web/server.py`
  Responsibility: render the redesigned config page and provide config read/write APIs.

- Modify: `src/web/templates/base.html`
  Responsibility: shared visual system, theme tokens, theme bootstrap script, shell layout, common controls.

- Modify: `src/web/templates/config.html`
  Responsibility: form-based config editor markup and client-side behavior.

- Modify: `src/web/templates/dashboard.html`
- Modify: `src/web/templates/platforms.html`
- Modify: `src/web/templates/logs.html`
  Responsibility: adopt the refreshed shell and tokenized page styles so the redesign is coherent.

- Create: `tests/test_config.py`
  Responsibility: config schema, validation, save semantics, secret preservation.

- Create: `tests/test_web_config.py`
  Responsibility: config API read/write behavior through FastAPI.

### Task 1: Lock Down Config Editing Semantics

**Files:**
- Create: `tests/test_config.py`
- Modify: `src/config.py`

- [ ] **Step 1: Write the failing config tests**

```python
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

from src.config import Config


class ConfigEditorTests(unittest.TestCase):
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
                        "taobao": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
                        "pdd": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
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

    def test_form_payload_masks_password_and_reports_overrides(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_config_file(tmp)
            with patch.dict("os.environ", {"FREELOAD_EMAIL_FROM": "env@example.com"}, clear=False):
                config = Config(path)
                config.load()

                payload = config.to_form_payload()

                self.assertEqual(payload["notify"]["email"]["password"], "")
                self.assertEqual(
                    payload["meta"]["overrides"]["notify.email.from_addr"]["env_var"],
                    "FREELOAD_EMAIL_FROM",
                )
                self.assertTrue(
                    payload["meta"]["overrides"]["notify.email.from_addr"]["active"]
                )

    def test_validate_update_preserves_existing_password_when_blank(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_config_file(tmp)
            config = Config(path)
            config.load()

            normalized = config.validate_update(
                {
                    "notify": {
                        "email": {
                            "smtp_host": "smtp.qq.com",
                            "smtp_port": 587,
                            "use_ssl": False,
                            "from_addr": "fresh@example.com",
                            "password": "",
                            "to_addr": "target@example.com",
                        }
                    },
                    "platforms": {
                        "jd": {"enabled": True, "poll_interval": 45, "value_threshold": 2.5},
                        "taobao": {"enabled": False, "poll_interval": 60, "value_threshold": 1.0},
                        "pdd": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
                        "miniapp": {"enabled": True, "poll_interval": 300, "value_threshold": 0.5},
                    },
                    "web": {"host": "127.0.0.1", "port": 9529},
                    "browser": {"pool_size": 3, "headless": True},
                }
            )

            self.assertEqual(normalized["notify"]["email"]["password"], "secret-token")
            self.assertEqual(normalized["notify"]["email"]["smtp_port"], 587)
            self.assertFalse(normalized["notify"]["email"]["use_ssl"])

    def test_validate_update_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_config_file(tmp)
            config = Config(path)
            config.load()

            with self.assertRaisesRegex(ValueError, "smtp_port"):
                config.validate_update(
                    {
                        "notify": {
                            "email": {
                                "smtp_host": "smtp.qq.com",
                                "smtp_port": 70000,
                                "use_ssl": True,
                                "from_addr": "bad-email",
                                "password": "",
                                "to_addr": "target@example.com",
                            }
                        },
                        "platforms": {
                            "jd": {"enabled": True, "poll_interval": 0, "value_threshold": -1},
                            "taobao": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
                            "pdd": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
                            "miniapp": {"enabled": True, "poll_interval": 300, "value_threshold": 0.5},
                        },
                        "web": {"host": "127.0.0.1", "port": 9528},
                        "browser": {"pool_size": 0, "headless": False},
                    }
                )

    def test_save_update_writes_yaml_and_reloads_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_config_file(tmp)
            config = Config(path)
            config.load()

            config.save_update(
                {
                    "notify": {
                        "email": {
                            "smtp_host": "smtp.163.com",
                            "smtp_port": 465,
                            "use_ssl": True,
                            "from_addr": "writer@example.com",
                            "password": "new-secret",
                            "to_addr": "reader@example.com",
                        }
                    },
                    "platforms": {
                        "jd": {"enabled": False, "poll_interval": 35, "value_threshold": 1.5},
                        "taobao": {"enabled": True, "poll_interval": 61, "value_threshold": 1.0},
                        "pdd": {"enabled": True, "poll_interval": 62, "value_threshold": 1.0},
                        "miniapp": {"enabled": True, "poll_interval": 301, "value_threshold": 0.8},
                    },
                    "web": {"host": "0.0.0.0", "port": 9530},
                    "browser": {"pool_size": 4, "headless": True},
                }
            )

            stored = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["notify"]["email"]["password"], "new-secret")
            self.assertEqual(stored["web"]["port"], 9530)
            self.assertFalse(stored["platforms"]["jd"]["enabled"])
            self.assertEqual(config.get("browser", "pool_size"), 4)
```

- [ ] **Step 2: Run the config tests to verify they fail**

Run: `python -m unittest tests.test_config -v`

Expected: `ERROR` because `Config` does not yet expose `to_form_payload`, `validate_update`, or `save_update`.

- [ ] **Step 3: Implement schema defaults, safe payload, validation, and save helpers in `src/config.py`**

```python
import copy
import os
import re
from pathlib import Path
from typing import Any

import yaml

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ENV_OVERRIDE_FIELDS = {
    ("notify", "email", "from_addr"): "FREELOAD_EMAIL_FROM",
    ("notify", "email", "password"): "FREELOAD_EMAIL_PASS",
    ("notify", "email", "to_addr"): "FREELOAD_EMAIL_TO",
}
DEFAULT_CONFIG = {
    "notify": {
        "email": {
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
            "use_ssl": True,
            "from_addr": "",
            "password": "",
            "to_addr": "",
        }
    },
    "platforms": {
        "jd": {"enabled": True, "poll_interval": 30, "value_threshold": 1.0},
        "taobao": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
        "pdd": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
        "miniapp": {"enabled": True, "poll_interval": 300, "value_threshold": 0.5},
    },
    "web": {"host": "127.0.0.1", "port": 9528},
    "browser": {"pool_size": 2, "headless": False},
}


class Config:
    def __init__(self, path: str | Path = CONFIG_PATH):
        self._path = Path(path)
        self._data: dict[str, Any] = {}

    def load(self) -> None:
        if not self._path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self._path}")
        with open(self._path, encoding="utf-8") as handle:
            loaded = yaml.safe_load(handle) or {}
        self._data = self._merge_defaults(loaded)
        self._apply_env_overrides()
        self._validate_runtime()

    def _merge_defaults(self, data: dict[str, Any]) -> dict[str, Any]:
        merged = copy.deepcopy(DEFAULT_CONFIG)
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
        for platform, defaults in DEFAULT_CONFIG["platforms"].items():
            current = merged["platforms"].get(platform, {})
            merged["platforms"][platform] = {**defaults, **current}
        return merged

    def _apply_env_overrides(self) -> None:
        email_cfg = self._data.get("notify", {}).get("email", {})
        if os.environ.get("FREELOAD_EMAIL_FROM"):
            email_cfg["from_addr"] = os.environ["FREELOAD_EMAIL_FROM"]
        if os.environ.get("FREELOAD_EMAIL_PASS"):
            email_cfg["password"] = os.environ["FREELOAD_EMAIL_PASS"]
        if os.environ.get("FREELOAD_EMAIL_TO"):
            email_cfg["to_addr"] = os.environ["FREELOAD_EMAIL_TO"]

    def _validate_runtime(self) -> None:
        email = self._data.get("notify", {}).get("email", {})
        if not email.get("from_addr") or not email.get("to_addr"):
            print("[WARN] 邮箱配置不完整，通知功能将不可用")
            print("   请配置 config/config.yaml 或设置环境变量")
            print("   FREELOAD_EMAIL_FROM / FREELOAD_EMAIL_PASS / FREELOAD_EMAIL_TO")

    def _build_override_meta(self) -> dict[str, dict[str, Any]]:
        meta: dict[str, dict[str, Any]] = {}
        for keys, env_var in ENV_OVERRIDE_FIELDS.items():
            dotted = ".".join(keys)
            meta[dotted] = {"env_var": env_var, "active": bool(os.environ.get(env_var))}
        return meta

    def to_form_payload(self) -> dict[str, Any]:
        payload = copy.deepcopy(self._data)
        payload["notify"]["email"]["password"] = ""
        payload["meta"] = {"overrides": self._build_override_meta()}
        return payload

    def validate_update(self, incoming: dict[str, Any]) -> dict[str, Any]:
        current = copy.deepcopy(self._data or DEFAULT_CONFIG)
        email = incoming["notify"]["email"]
        normalized = {
            "notify": {
                "email": {
                    "smtp_host": str(email["smtp_host"]).strip(),
                    "smtp_port": int(email["smtp_port"]),
                    "use_ssl": bool(email["use_ssl"]),
                    "from_addr": str(email["from_addr"]).strip(),
                    "password": str(email.get("password", "")),
                    "to_addr": str(email["to_addr"]).strip(),
                }
            },
            "platforms": {},
            "web": {
                "host": str(incoming["web"]["host"]).strip(),
                "port": int(incoming["web"]["port"]),
            },
            "browser": {
                "pool_size": int(incoming["browser"]["pool_size"]),
                "headless": bool(incoming["browser"]["headless"]),
            },
        }
        if not normalized["notify"]["email"]["smtp_host"]:
            raise ValueError("smtp_host is required")
        if not 1 <= normalized["notify"]["email"]["smtp_port"] <= 65535:
            raise ValueError("smtp_port must be between 1 and 65535")
        for field in ("from_addr", "to_addr"):
            value = normalized["notify"]["email"][field]
            if value and not EMAIL_RE.match(value):
                raise ValueError(f"{field} must be a valid email address")
        if not normalized["notify"]["email"]["to_addr"]:
            raise ValueError("to_addr is required")
        password = normalized["notify"]["email"]["password"]
        if not password:
            normalized["notify"]["email"]["password"] = current["notify"]["email"].get("password", "")

        for platform in ("jd", "taobao", "pdd", "miniapp"):
            platform_data = incoming["platforms"][platform]
            poll_interval = int(platform_data["poll_interval"])
            value_threshold = float(platform_data["value_threshold"])
            if poll_interval <= 0:
                raise ValueError(f"{platform}.poll_interval must be positive")
            if value_threshold < 0:
                raise ValueError(f"{platform}.value_threshold must be non-negative")
            normalized["platforms"][platform] = {
                "enabled": bool(platform_data["enabled"]),
                "poll_interval": poll_interval,
                "value_threshold": value_threshold,
            }

        if not normalized["web"]["host"]:
            raise ValueError("web.host is required")
        if not 1 <= normalized["web"]["port"] <= 65535:
            raise ValueError("web.port must be between 1 and 65535")
        if normalized["browser"]["pool_size"] <= 0:
            raise ValueError("browser.pool_size must be positive")

        return normalized

    def save_update(self, incoming: dict[str, Any]) -> dict[str, Any]:
        normalized = self.validate_update(incoming)
        temp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with open(temp_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(normalized, handle, allow_unicode=True, sort_keys=False)
        temp_path.replace(self._path)
        self.load()
        return normalized
```

- [ ] **Step 4: Run the config tests to verify they pass**

Run: `python -m unittest tests.test_config -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add tests/test_config.py src/config.py
git commit -m "feat: add editable config schema helpers"
```

### Task 2: Add Runtime Reload and Config APIs

**Files:**
- Create: `tests/test_web_config.py`
- Modify: `src/daemon.py`
- Modify: `src/web/server.py`

- [ ] **Step 1: Write the failing API tests**

```python
import tempfile
import unittest
from pathlib import Path

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
    def status_info(self):
        return {"processed": 0, "success": 0, "recent": []}


class DummyAuth:
    async def has_saved_session(self, platform):
        return False


class DummyDaemon:
    def __init__(self, config_path: Path):
        self.config = Config(config_path)
        self.config.load()
        self.storage = DummyStorage()
        self.executor = DummyExecutor()
        self.auth = DummyAuth()
        self._running = True
        self.reload_count = 0

    def get_watchers(self):
        return []

    def get_executor(self):
        return self.executor

    def reload_config(self):
        self.reload_count += 1
        self.config.load()


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
                        "taobao": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
                        "pdd": {"enabled": True, "poll_interval": 60, "value_threshold": 1.0},
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
```

- [ ] **Step 2: Run the API tests to verify they fail**

Run: `python -m unittest tests.test_web_config -v`

Expected: `FAIL` or `ERROR` because `/api/config` does not exist and `DummyDaemon.reload_config()` is never called.

- [ ] **Step 3: Add daemon reload support and config endpoints**

```python
# in src/daemon.py
class Daemon:
    ...
    def reload_config(self) -> None:
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

# in src/web/server.py
from fastapi import FastAPI, Query, Request


@app.get("/api/config")
async def api_get_config():
    return daemon.config.to_form_payload()


@app.post("/api/config")
async def api_save_config(request: Request):
    try:
        payload = await request.json()
        daemon.config.save_update(payload)
        if hasattr(daemon, "reload_config"):
            daemon.reload_config()
        return JSONResponse(
            {
                "ok": True,
                "message": "配置已保存。为确保所有任务使用新配置，请重启 daemon。",
                "config": daemon.config.to_form_payload(),
            }
        )
    except ValueError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=422)
    except FileNotFoundError as exc:
        return JSONResponse({"ok": False, "error": str(exc)}, status_code=404)
    except Exception as exc:
        logger.exception("saving config failed")
        return JSONResponse({"ok": False, "error": f"保存失败: {exc}"}, status_code=500)
```

- [ ] **Step 4: Run the API tests to verify they pass**

Run: `python -m unittest tests.test_web_config -v`

Expected: `OK`

- [ ] **Step 5: Commit**

```bash
git add tests/test_web_config.py src/daemon.py src/web/server.py
git commit -m "feat: add config editor api routes"
```

### Task 3: Rebuild the Shared Shell and Theme System

**Files:**
- Modify: `src/web/templates/base.html`
- Modify: `src/web/templates/dashboard.html`
- Modify: `src/web/templates/platforms.html`
- Modify: `src/web/templates/logs.html`

- [ ] **Step 1: Snapshot the current template behavior with a manual smoke check**

Run: `python src/daemon.py`

Expected: the existing dashboard, platforms, config, and logs pages render before template changes. Note the active port printed by the daemon.

- [ ] **Step 2: Replace the shared shell in `src/web/templates/base.html` with theme-aware tokens and toggle UI**

```html
<!DOCTYPE html>
<html lang="zh-CN" data-theme="system">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Freeload - {{ title }}</title>
    <style>
        :root {
            color-scheme: light dark;
            --font-ui: "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
            --radius: 14px;
            --sidebar-width: 248px;
        }
        html[data-theme="light"] {
            --bg: #edf2f7;
            --bg-accent: radial-gradient(circle at top left, rgba(18, 99, 204, 0.18), transparent 32%), linear-gradient(180deg, #f8fbff 0%, #edf2f7 100%);
            --surface: rgba(255, 255, 255, 0.88);
            --surface-strong: #ffffff;
            --surface-muted: #f7f9fc;
            --text: #172033;
            --text-secondary: #607089;
            --border: rgba(129, 147, 168, 0.22);
            --primary: #1263cc;
            --primary-soft: rgba(18, 99, 204, 0.12);
            --sidebar: #101721;
            --sidebar-hover: rgba(255, 255, 255, 0.08);
            --success: #14945a;
            --warning: #d28b18;
            --danger: #d14343;
            --shadow: 0 16px 40px rgba(13, 24, 45, 0.08);
        }
        html[data-theme="dark"] {
            --bg: #0d1420;
            --bg-accent: radial-gradient(circle at top left, rgba(77, 163, 255, 0.16), transparent 30%), linear-gradient(180deg, #111827 0%, #0d1420 100%);
            --surface: rgba(20, 28, 42, 0.88);
            --surface-strong: #182234;
            --surface-muted: #111827;
            --text: #e7eef8;
            --text-secondary: #98a8bc;
            --border: rgba(152, 168, 188, 0.18);
            --primary: #66a8ff;
            --primary-soft: rgba(102, 168, 255, 0.16);
            --sidebar: #0a1018;
            --sidebar-hover: rgba(255, 255, 255, 0.06);
            --success: #3ecf8e;
            --warning: #f0b24a;
            --danger: #ff7b7b;
            --shadow: 0 20px 48px rgba(0, 0, 0, 0.28);
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            font-family: var(--font-ui);
            background: var(--bg);
            background-image: var(--bg-accent);
            color: var(--text);
            display: flex;
            min-height: 100vh;
        }
        .sidebar { width: var(--sidebar-width); background: var(--sidebar); color: #fff; padding: 22px 16px; }
        .shell-main { flex: 1; min-width: 0; padding: 22px; }
        .page-topbar { display: flex; justify-content: space-between; gap: 16px; align-items: center; margin-bottom: 18px; }
        .theme-toggle { display: inline-flex; gap: 4px; padding: 4px; background: var(--surface); border: 1px solid var(--border); border-radius: 999px; box-shadow: var(--shadow); }
        .theme-toggle button { border: 0; background: transparent; color: var(--text-secondary); border-radius: 999px; padding: 8px 12px; cursor: pointer; }
        .theme-toggle button.active { background: var(--surface-strong); color: var(--text); }
        .panel, .card, .stat-card { background: var(--surface); border: 1px solid var(--border); box-shadow: var(--shadow); backdrop-filter: blur(12px); }
        @media (max-width: 900px) {
            body { display: block; }
            .sidebar { width: auto; padding: 14px; }
            .shell-main { padding: 14px; }
            .page-topbar { flex-direction: column; align-items: stretch; }
        }
    </style>
    <script>
        (function() {
            const stored = localStorage.getItem("freeload-theme") || "system";
            const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
            const theme = stored === "system" ? (prefersDark ? "dark" : "light") : stored;
            document.documentElement.dataset.theme = theme;
            document.documentElement.dataset.themePreference = stored;
        })();
    </script>
    {% block head_extra %}{% endblock %}
</head>
<body>
    <aside class="sidebar">...</aside>
    <main class="shell-main">
        <div class="page-topbar">
            <div>
                <h1 class="page-title">{{ title }}</h1>
            </div>
            <div class="theme-toggle" id="theme-toggle">
                <button data-theme-choice="system">系统</button>
                <button data-theme-choice="light">浅色</button>
                <button data-theme-choice="dark">深色</button>
            </div>
        </div>
        {% block content %}{% endblock %}
    </main>
    <script>
        document.addEventListener("DOMContentLoaded", function() {
            const toggle = document.getElementById("theme-toggle");
            const buttons = toggle ? Array.from(toggle.querySelectorAll("button")) : [];
            function applyTheme(choice) {
                const prefersDark = window.matchMedia("(prefers-color-scheme: dark)").matches;
                const resolved = choice === "system" ? (prefersDark ? "dark" : "light") : choice;
                document.documentElement.dataset.theme = resolved;
                document.documentElement.dataset.themePreference = choice;
                localStorage.setItem("freeload-theme", choice);
                buttons.forEach((button) => button.classList.toggle("active", button.dataset.themeChoice === choice));
            }
            const currentChoice = document.documentElement.dataset.themePreference || "system";
            buttons.forEach((button) => {
                button.addEventListener("click", () => applyTheme(button.dataset.themeChoice));
            });
            applyTheme(currentChoice);
        });
    </script>
</body>
</html>
```

- [ ] **Step 3: Update the dashboard, platforms, and logs templates to use the shared page-topbar and tokenized surfaces**

```html
<!-- top of each template body -->
<section class="panel" style="padding: 18px; margin-bottom: 16px;">
    <div style="display:flex; justify-content:space-between; gap:12px; align-items:center; flex-wrap:wrap;">
        <div>
            <h2 style="margin:0; font-size:1.2rem;">{{ title }}</h2>
            <p style="margin:6px 0 0; color:var(--text-secondary); font-size:0.9rem;">
                保持信息密度，但统一到新的主题和表面层级。
            </p>
        </div>
    </div>
</section>
```

- [ ] **Step 4: Run the daemon and manually verify the shared shell**

Run: `python src/daemon.py`

Expected:
- dashboard renders
- theme toggle appears on all pages
- switching between `system`, `浅色`, `深色` persists across refresh
- sidebar and cards remain readable on desktop and mobile-width browser

- [ ] **Step 5: Commit**

```bash
git add src/web/templates/base.html src/web/templates/dashboard.html src/web/templates/platforms.html src/web/templates/logs.html
git commit -m "feat: refresh shared web shell and theme system"
```

### Task 4: Replace the Config Page With a Form-Based Editor

**Files:**
- Modify: `src/web/templates/config.html`
- Modify: `src/web/server.py`

- [ ] **Step 1: Replace the read-only config page with a sectioned form shell**

```html
{% extends "base.html" %}

{% block content %}
<section class="panel" style="padding:20px;">
    <div style="display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; margin-bottom:18px;">
        <div>
            <h2 style="margin:0; font-size:1.15rem;">配置工作台</h2>
            <p style="margin:8px 0 0; color:var(--text-secondary);">
                直接在 WebUI 中修改配置。保存后会校验并写回 <code>config/config.yaml</code>。
            </p>
        </div>
        <div id="config-save-state" style="color:var(--text-secondary); font-size:0.9rem;">未修改</div>
    </div>

    <div class="config-layout" style="display:grid; grid-template-columns:180px minmax(0, 1fr) 220px; gap:16px;">
        <nav class="panel" style="padding:10px;" id="config-sections">
            <button type="button" data-section="notify" class="config-nav active">通知邮箱</button>
            <button type="button" data-section="platforms" class="config-nav">平台监控</button>
            <button type="button" data-section="web" class="config-nav">Web 面板</button>
            <button type="button" data-section="browser" class="config-nav">浏览器</button>
        </nav>

        <form id="config-form" class="panel" style="padding:18px;">
            <section data-config-section="notify">...</section>
            <section data-config-section="platforms" hidden>...</section>
            <section data-config-section="web" hidden>...</section>
            <section data-config-section="browser" hidden>...</section>
            <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:18px; padding-top:14px; border-top:1px solid var(--border);">
                <button type="button" id="config-reset">重置更改</button>
                <button type="submit" id="config-save">保存配置</button>
            </div>
        </form>

        <aside class="panel" style="padding:16px;">
            <h3 style="margin:0 0 10px; font-size:1rem;">运行提示</h3>
            <div id="config-override-list"></div>
            <p style="color:var(--text-secondary); font-size:0.9rem; line-height:1.6;">
                环境变量优先级高于 YAML。保存成功后请重启 daemon，确保全部运行组件应用新配置。
            </p>
        </aside>
    </div>
</section>
{% endblock %}
```

- [ ] **Step 2: Add client-side config loading, section switching, dirty tracking, and save/reset behavior**

```html
{% block head_extra %}
<style>
    .config-nav { width:100%; border:0; background:transparent; color:var(--text-secondary); text-align:left; padding:10px 12px; border-radius:10px; cursor:pointer; }
    .config-nav.active { background:var(--primary-soft); color:var(--text); font-weight:600; }
    .field-grid { display:grid; grid-template-columns:repeat(2, minmax(0, 1fr)); gap:14px; }
    .field-grid .full { grid-column:1 / -1; }
    .field { display:grid; gap:6px; }
    .field input { width:100%; padding:10px 12px; border-radius:10px; border:1px solid var(--border); background:var(--surface-muted); color:var(--text); }
    .toggle-row { display:flex; justify-content:space-between; align-items:center; padding:12px; border:1px solid var(--border); border-radius:10px; background:var(--surface-muted); }
    .platform-row { display:grid; grid-template-columns:1.3fr 100px 100px 72px; gap:10px; align-items:center; margin-bottom:10px; }
    .error-text { color:var(--danger); font-size:0.84rem; min-height:1.2em; }
    @media (max-width: 900px) { .config-layout, .field-grid, .platform-row { display:block; } }
</style>
{% endblock %}

<script>
document.addEventListener("DOMContentLoaded", function() {
    const form = document.getElementById("config-form");
    const saveState = document.getElementById("config-save-state");
    const overrideList = document.getElementById("config-override-list");
    const navButtons = Array.from(document.querySelectorAll("[data-section]"));
    const sections = Array.from(document.querySelectorAll("[data-config-section]"));
    let initialState = null;

    function showSection(name) {
        navButtons.forEach((button) => button.classList.toggle("active", button.dataset.section === name));
        sections.forEach((section) => { section.hidden = section.dataset.configSection !== name; });
    }

    function readForm() {
        return {
            notify: {
                email: {
                    smtp_host: form.smtp_host.value.trim(),
                    smtp_port: Number(form.smtp_port.value),
                    use_ssl: form.use_ssl.checked,
                    from_addr: form.from_addr.value.trim(),
                    password: form.password.value,
                    to_addr: form.to_addr.value.trim(),
                }
            },
            platforms: {
                jd: { enabled: form.jd_enabled.checked, poll_interval: Number(form.jd_poll_interval.value), value_threshold: Number(form.jd_value_threshold.value) },
                taobao: { enabled: form.taobao_enabled.checked, poll_interval: Number(form.taobao_poll_interval.value), value_threshold: Number(form.taobao_value_threshold.value) },
                pdd: { enabled: form.pdd_enabled.checked, poll_interval: Number(form.pdd_poll_interval.value), value_threshold: Number(form.pdd_value_threshold.value) },
                miniapp: { enabled: form.miniapp_enabled.checked, poll_interval: Number(form.miniapp_poll_interval.value), value_threshold: Number(form.miniapp_value_threshold.value) },
            },
            web: { host: form.web_host.value.trim(), port: Number(form.web_port.value) },
            browser: { pool_size: Number(form.browser_pool_size.value), headless: form.browser_headless.checked },
        };
    }

    function writeForm(config) {
        form.smtp_host.value = config.notify.email.smtp_host;
        form.smtp_port.value = config.notify.email.smtp_port;
        form.use_ssl.checked = config.notify.email.use_ssl;
        form.from_addr.value = config.notify.email.from_addr;
        form.password.value = "";
        form.to_addr.value = config.notify.email.to_addr;
        for (const platform of ["jd", "taobao", "pdd", "miniapp"]) {
            form[platform + "_enabled"].checked = config.platforms[platform].enabled;
            form[platform + "_poll_interval"].value = config.platforms[platform].poll_interval;
            form[platform + "_value_threshold"].value = config.platforms[platform].value_threshold;
        }
        form.web_host.value = config.web.host;
        form.web_port.value = config.web.port;
        form.browser_pool_size.value = config.browser.pool_size;
        form.browser_headless.checked = config.browser.headless;
        overrideList.innerHTML = Object.entries(config.meta.overrides)
            .map(([field, meta]) => `<div style="margin-bottom:8px;"><strong>${field}</strong><div style="color:var(--text-secondary); font-size:0.84rem;">${meta.active ? "环境变量生效: " + meta.env_var : "未被环境变量覆盖"}</div></div>`)
            .join("");
        initialState = JSON.stringify(readForm());
        saveState.textContent = "已加载";
    }

    function updateDirtyState() {
        saveState.textContent = JSON.stringify(readForm()) === initialState ? "未修改" : "有未保存修改";
    }

    async function loadConfig() {
        const response = await fetch("/api/config");
        const data = await response.json();
        writeForm(data);
    }

    navButtons.forEach((button) => button.addEventListener("click", () => showSection(button.dataset.section)));
    form.addEventListener("input", updateDirtyState);
    document.getElementById("config-reset").addEventListener("click", loadConfig);
    form.addEventListener("submit", async function(event) {
        event.preventDefault();
        const response = await fetch("/api/config", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(readForm()),
        });
        const result = await response.json();
        saveState.textContent = result.ok ? result.message : result.error;
        if (result.ok && result.config) {
            writeForm(result.config);
        }
    });

    showSection("notify");
    loadConfig();
});
</script>
```

- [ ] **Step 3: Fill in the actual form fields for notify, platforms, web, and browser**

```html
<section data-config-section="notify">
    <div class="field-grid">
        <label class="field">
            <span>SMTP 主机</span>
            <input name="smtp_host" type="text" required>
        </label>
        <label class="field">
            <span>SMTP 端口</span>
            <input name="smtp_port" type="number" min="1" max="65535" required>
        </label>
        <label class="field full">
            <div class="toggle-row">
                <span>使用 SSL</span>
                <input name="use_ssl" type="checkbox">
            </div>
        </label>
        <label class="field">
            <span>发件邮箱</span>
            <input name="from_addr" type="email">
        </label>
        <label class="field">
            <span>收件邮箱</span>
            <input name="to_addr" type="email" required>
        </label>
        <label class="field full">
            <span>邮箱授权码</span>
            <input name="password" type="password" placeholder="留空表示保留现有密码">
        </label>
    </div>
</section>
```

- [ ] **Step 4: Run the daemon and manually verify config editing**

Run: `python src/daemon.py`

Expected:
- `/config` shows a form, not JSON preview
- form loads current config values
- password field is blank/masked placeholder
- save updates `config/config.yaml`
- blank password save preserves existing secret
- success response tells the user to restart daemon

- [ ] **Step 5: Commit**

```bash
git add src/web/templates/config.html src/web/server.py
git commit -m "feat: rebuild config page as form editor"
```

### Task 5: Full Regression Pass

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_web_config.py`
- Modify: any templates or Python files touched above if fixes are needed

- [ ] **Step 1: Run the focused automated tests**

Run: `python -m unittest tests.test_config tests.test_web_config tests.test_auth -v`

Expected: all listed test modules pass.

- [ ] **Step 2: Run a full repository test pass**

Run: `python -m unittest discover -s tests -p "test_*.py" -v`

Expected: `OK`

- [ ] **Step 3: Perform manual browser verification**

Run: `python src/daemon.py`

Expected:
- dashboard, platforms, config, and logs pages all render
- theme toggle persists after refresh
- config editor works on a narrow/mobile-sized browser window
- environment override messages appear when `FREELOAD_EMAIL_FROM`, `FREELOAD_EMAIL_PASS`, or `FREELOAD_EMAIL_TO` are set
- invalid values produce a visible error instead of a server crash

- [ ] **Step 4: Commit final polish**

```bash
git add src/config.py src/daemon.py src/web/server.py src/web/templates/base.html src/web/templates/config.html src/web/templates/dashboard.html src/web/templates/platforms.html src/web/templates/logs.html tests/test_config.py tests/test_web_config.py
git commit -m "feat: ship redesigned config workbench"
```
