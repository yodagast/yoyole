"""邮件发送模块：通过 SMTP 发信，未配置 SMTP 时退化为打印日志（开发模式）"""
from __future__ import annotations

import logging

from app.config import settings

logger = logging.getLogger("mailer")


async def send_email(to: str, subject: str, body: str, html: str | None = None) -> tuple[bool, str | None]:
    """发送邮件。

    返回 (sent, error)：
    - sent=True 表示已通过 SMTP 真实发送
    - sent=False 且 error=None 表示开发模式（未配置 SMTP，仅打印日志）
    - sent=False 且 error=... 表示发送失败
    """
    if not settings.SMTP_HOST or settings.SMTP_HOST == "localhost" and not settings.SMTP_USER:
        # 开发模式：未配置 SMTP 账号，直接打印邮件内容
        logger.info(
            "[mailer][dev] 模拟发送邮件\n  To: %s\n  Subject: %s\n%s",
            to, subject, html or body,
        )
        return False, None

    # 已配置 SMTP：真实发送
    import smtplib
    from email.header import Header
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText
    from email.utils import formataddr

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(settings.SMTP_FROM_NAME, "utf-8")), settings.SMTP_FROM))
    msg["To"] = to
    if html:
        msg.attach(MIMEText(html, "html", "utf-8"))
    else:
        msg.attach(MIMEText(body, "plain", "utf-8"))

    server = None
    try:
        if settings.SMTP_USE_SSL:
            server = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
        else:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
            if settings.SMTP_USE_TLS:
                server.starttls()
        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
        server.sendmail(settings.SMTP_FROM, [to], msg.as_string())
        return True, None
    except Exception as exc:  # noqa: BLE001 发送失败时业务层决定是否降级
        logger.error("[mailer] SMTP 发送失败: %s", exc)
        return False, str(exc)
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:  # noqa: BLE001
                pass