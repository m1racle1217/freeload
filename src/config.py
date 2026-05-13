# -*- coding: utf-8 -*-
"""配置加载与校验。"""

# ================================
# 导入依赖
# ================================
import os
from pathlib import Path
from typing import Any

import yaml


# ================================
# 常量
# ================================
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "config.yaml"


# ================================
# 配置管理器
# ================================
class Config:
    """读取并校验项目配置。

    加载 config/config.yaml，使用环境变量覆盖敏感字段（邮箱密码等）。
    """

    def __init__(self, path: str | Path = CONFIG_PATH):
        self._path = Path(path)
        self._data: dict[str, Any] = {}

    # ================================
    # 加载配置
    # ================================
    def load(self) -> None:
        """从 YAML 文件加载配置，环境变量覆盖敏感字段。"""
        if not self._path.exists():
            raise FileNotFoundError(f"配置文件不存在: {self._path}")

        with open(self._path, encoding="utf-8") as f:
            self._data = yaml.safe_load(f) or {}

        # ================================
        # 环境变量覆盖
        # ================================
        self._apply_env_overrides()

        self._validate()

    def _apply_env_overrides(self) -> None:
        """用环境变量覆盖敏感的配置项。"""
        email_cfg = self._data.get("notify", {}).get("email", {})
        if os.environ.get("FREELOAD_EMAIL_FROM"):
            email_cfg["from_addr"] = os.environ["FREELOAD_EMAIL_FROM"]
        if os.environ.get("FREELOAD_EMAIL_PASS"):
            email_cfg["password"] = os.environ["FREELOAD_EMAIL_PASS"]
        if os.environ.get("FREELOAD_EMAIL_TO"):
            email_cfg["to_addr"] = os.environ["FREELOAD_EMAIL_TO"]

    def _validate(self) -> None:
        """校验必要配置是否完整。"""
        email = self._data.get("notify", {}).get("email", {})
        if not email.get("from_addr") or not email.get("to_addr"):
            print("[WARN] 邮箱配置不完整，通知功能将不可用")
            print("   请配置 config/config.yaml 或设置环境变量:")
            print("   FREELOAD_EMAIL_FROM / FREELOAD_EMAIL_PASS / FREELOAD_EMAIL_TO")

    # ================================
    # 读取配置
    # ================================
    def get(self, *keys: str, default: Any = None) -> Any:
        """安全地逐级读取嵌套配置。"""
        value: Any = self._data
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key)
            else:
                return default
        return value if value is not None else default

    @property
    def data(self) -> dict:
        return self._data

    # ================================
    # 配置转字典（供 Web 面板使用）
    # ================================
    def to_dict(self) -> dict:
        """返回配置快照（屏蔽密码）。"""
        import copy

        d = copy.deepcopy(self._data)
        email = d.get("notify", {}).get("email", {})
        if "password" in email and email["password"]:
            email["password"] = "******"
        return d
