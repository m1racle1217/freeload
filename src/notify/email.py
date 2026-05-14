# -*- coding: utf-8 -*-
"""SMTP email notification backend."""

import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from src.notify.base import BaseNotifier, NotifyMessage

logger = logging.getLogger("freeload")


class EmailNotifier(BaseNotifier):
    """Send HTML notification emails through SMTP."""

    def __init__(
        self,
        smtp_host: str = "smtp.qq.com",
        smtp_port: int = 465,
        use_ssl: bool = True,
        from_addr: Optional[str] = None,
        password: Optional[str] = None,
        to_addr: Optional[str] = None,
    ):
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.use_ssl = use_ssl
        self.from_addr = from_addr
        self.password = password
        self.to_addr = to_addr

    async def send(self, message: NotifyMessage) -> bool:
        """Send a notification email."""
        if not all([self.from_addr, self.password, self.to_addr]):
            logger.warning("[邮件] 邮箱配置不完整，跳过通知")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"[Freeload] {message.subject}"
            msg["From"] = self.from_addr
            msg["To"] = self.to_addr

            html = self._build_html(message)
            msg.attach(MIMEText(html, "html", "utf-8"))

            if self.use_ssl:
                with smtplib.SMTP_SSL(self.smtp_host, self.smtp_port, timeout=30) as server:
                    server.login(self.from_addr, self.password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=30) as server:
                    server.starttls()
                    server.login(self.from_addr, self.password)
                    server.send_message(msg)

            logger.info("[邮件] 发送成功: %s", message.subject)
            return True
        except Exception as exc:
            logger.error("[邮件] 发送失败: %s", exc)
            return False

    def _build_html(self, message: NotifyMessage) -> str:
        """Render a notification message as HTML."""
        level_color = {
            "success": "#22c55e",
            "warning": "#f59e0b",
            "critical": "#ef4444",
            "info": "#3b82f6",
        }
        color = level_color.get(message.level, "#3b82f6")

        return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"></head>
<body style="font-family: -apple-system, 'Segoe UI', sans-serif; max-width: 600px; margin: 0 auto; padding: 20px;">
    <div style="text-align: center; margin-bottom: 24px;">
        <span style="font-size: 32px;">F</span>
        <h2 style="margin: 8px 0; color: #1f2937;">{message.subject}</h2>
    </div>
    <div style="background: #f9fafb; border-left: 4px solid {color}; padding: 16px; border-radius: 8px;">
        <pre style="font-family: 'Courier New', monospace; font-size: 14px; line-height: 1.6; color: #374151; white-space: pre-wrap; margin: 0;">
{message.body}
        </pre>
    </div>
    <p style="color: #9ca3af; font-size: 12px; text-align: center; margin-top: 24px;">
        Freeload · 实时通知
    </p>
</body>
</html>"""
