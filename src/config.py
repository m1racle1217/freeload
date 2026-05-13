# -*- coding: utf-8 -*-
"""Configuration loading, validation, and persistence helpers."""

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
DEFAULT_CONFIG: dict[str, Any] = {
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
    """Read, validate, and persist application configuration."""

    def __init__(self, path: str | Path = CONFIG_PATH):
        self._path = Path(path)
        self._data: dict[str, Any] = {}

    def load(self) -> None:
        """Load configuration from YAML and apply runtime overrides."""
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
        """Override sensitive email fields with environment values when present."""
        email_cfg = self._data.get("notify", {}).get("email", {})
        if os.environ.get("FREELOAD_EMAIL_FROM"):
            email_cfg["from_addr"] = os.environ["FREELOAD_EMAIL_FROM"]
        if os.environ.get("FREELOAD_EMAIL_PASS"):
            email_cfg["password"] = os.environ["FREELOAD_EMAIL_PASS"]
        if os.environ.get("FREELOAD_EMAIL_TO"):
            email_cfg["to_addr"] = os.environ["FREELOAD_EMAIL_TO"]

    def _validate_runtime(self) -> None:
        """Warn when runtime email configuration is incomplete."""
        email = self._data.get("notify", {}).get("email", {})
        if not email.get("from_addr") or not email.get("to_addr"):
            print("[WARN] 邮箱配置不完整，通知功能将不可用")
            print("   请配置 config/config.yaml 或设置环境变量")
            print("   FREELOAD_EMAIL_FROM / FREELOAD_EMAIL_PASS / FREELOAD_EMAIL_TO")

    def get(self, *keys: str, default: Any = None) -> Any:
        """Safely read nested config values."""
        value: Any = self._data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
        return value if value is not None else default

    @property
    def data(self) -> dict[str, Any]:
        return self._data

    def to_dict(self) -> dict[str, Any]:
        """Return a masked config snapshot for debug views."""
        snapshot = copy.deepcopy(self._data)
        email = snapshot.get("notify", {}).get("email", {})
        if email.get("password"):
            email["password"] = "******"
        return snapshot

    def _build_override_meta(self) -> dict[str, dict[str, Any]]:
        meta: dict[str, dict[str, Any]] = {}
        for keys, env_var in ENV_OVERRIDE_FIELDS.items():
            dotted = ".".join(keys)
            meta[dotted] = {
                "env_var": env_var,
                "active": bool(os.environ.get(env_var)),
            }
        return meta

    def to_form_payload(self) -> dict[str, Any]:
        """Return a UI-safe form payload without leaking secrets."""
        payload = copy.deepcopy(self._data)
        payload.setdefault("notify", {}).setdefault("email", {})["password"] = ""
        payload["meta"] = {"overrides": self._build_override_meta()}
        return payload

    def validate_update(self, incoming: dict[str, Any]) -> dict[str, Any]:
        """Normalize and validate a config update payload."""
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
        if not normalized["notify"]["email"]["password"]:
            normalized["notify"]["email"]["password"] = (
                current.get("notify", {}).get("email", {}).get("password", "")
            )

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
        """Persist a validated update and reload in-memory config."""
        normalized = self.validate_update(incoming)
        temp_path = self._path.with_suffix(self._path.suffix + ".tmp")
        with open(temp_path, "w", encoding="utf-8") as handle:
            yaml.safe_dump(normalized, handle, allow_unicode=True, sort_keys=False)
        temp_path.replace(self._path)
        self.load()
        return normalized
