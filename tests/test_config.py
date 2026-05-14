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
                        "jd": {
                            "enabled": True,
                            "poll_interval": 30,
                            "value_threshold": 1.0,
                            "flash_sale_targets": [],
                        },
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
                        "jd": {
                            "enabled": True,
                            "poll_interval": 45,
                            "value_threshold": 2.5,
                            "flash_sale_targets": [
                                {"title": "iPhone", "url": "https://item.jd.com/123.html", "value": 99}
                            ],
                        },
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
            self.assertEqual(
                normalized["platforms"]["jd"]["flash_sale_targets"][0]["url"],
                "https://item.jd.com/123.html",
            )

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
                        "jd": {
                            "enabled": True,
                            "poll_interval": 0,
                            "value_threshold": -1,
                            "flash_sale_targets": [],
                        },
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
                        "jd": {
                            "enabled": False,
                            "poll_interval": 35,
                            "value_threshold": 1.5,
                            "flash_sale_targets": [
                                {"title": "茅台", "url": "https://item.jd.com/999.html", "value": 300}
                            ],
                        },
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
            self.assertEqual(
                stored["platforms"]["jd"]["flash_sale_targets"][0]["url"],
                "https://item.jd.com/999.html",
            )
            self.assertEqual(config.get("browser", "pool_size"), 4)

    def test_update_platform_enabled_does_not_require_email_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self.make_config_file(tmp)
            stored = yaml.safe_load(path.read_text(encoding="utf-8"))
            stored["notify"]["email"]["to_addr"] = ""
            stored["notify"]["email"]["from_addr"] = ""
            path.write_text(
                yaml.safe_dump(stored, allow_unicode=True, sort_keys=False),
                encoding="utf-8",
            )

            config = Config(path)
            config.load()

            updated = config.update_platform_enabled("taobao", True)

            self.assertTrue(updated["platforms"]["taobao"]["enabled"])
            persisted = yaml.safe_load(path.read_text(encoding="utf-8"))
            self.assertTrue(persisted["platforms"]["taobao"]["enabled"])
