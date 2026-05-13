# -*- coding: utf-8 -*-
"""邮件通知 — 通过 SMTP 发送 HTML 格式的薅羊毛消息。"""

# ================================
# 导入依赖
# ================================
import smtplib
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional

from src.notify.base import BaseNotifier, NotifyMessage

logger = logging.getLogger("freeload")


# ================================
# 邮件通知器
# ================================
class EmailNotifier(BaseNotifier):
    """通过 SMTP 发送邮件通知。

    支持 QQ邮箱 / 163 / Gmail 等标准 SMTP 服务。
    """

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

    # ================================
    # 发送邮件
    # ================================
    async def send(self, message: NotifyMessage) -> bool:
        """发送一封通知邮件。

        Args:
            message: 通知消息体

        Returns:
            是否发送成功
        """
        if not all([self.from_addr, self.password, self.to_addr]):
            logger.warning("[邮件] 邮箱配置不完整，跳过通知")
            return False

        try:
            msg = MIMEMultipart("alternative")
            msg["Subject"] = f"🐑 {message.subject}"
            msg["From"] = self.from_addr
            msg["To"] = self.to_addr

            # ================================
            # 构建 HTML 正文
            # ================================
            html = self._build_html(message)
            msg.attach(MIMEText(html, "html", "utf-8"))

            # ================================
            # 发送
            # ================================
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

        except Exception as e:
            logger.error("[邮件] 发送失败: %s", e)
            return False

    # ================================
    # HTML 模板
    # ================================
    def _build_html(self, message: NotifyMessage) -> str:
        """将通知消息渲染为 HTML。"""
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
        <span style="font-size: 32px;">🐑</span>
        <h2 style="margin: 8px 0; color: #1f2937;">{message.subject}</h2>
    </div>
    <div style="background: #f9fafb; border-left: 4px solid {color}; padding: 16px; border-radius: 8px;">
        <pre style="font-family: 'Courier New', monospace; font-size: 14px; line-height: 1.6; color: #374151; white-space: pre-wrap; margin: 0;">
{message.body}
        </pre>
    </div>
    <p style="color: #9ca3af; font-size: 12px; text-align: center; margin-top: 24px;">
        薅羊毛自动化 · 实时通知
    </p>
</body>
</html>"""
