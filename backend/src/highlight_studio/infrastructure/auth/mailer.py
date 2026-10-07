from __future__ import annotations

from email.message import EmailMessage
import logging
import smtplib

from ...core.settings import SMTP_FROM, SMTP_HOST, SMTP_PASSWORD, SMTP_PORT, SMTP_USE_TLS, SMTP_USER

logger = logging.getLogger(__name__)


def smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_FROM)


def send_account_email(recipient: str, subject: str, text: str) -> bool:
    """Send an account email without breaking registration if SMTP is unavailable."""
    if not smtp_configured():
        return False
    message = EmailMessage()
    message["From"] = SMTP_FROM
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(text)
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as client:
            if SMTP_USE_TLS:
                client.starttls()
            if SMTP_USER:
                client.login(SMTP_USER, SMTP_PASSWORD)
            client.send_message(message)
        return True
    except (OSError, smtplib.SMTPException):
        logger.exception("Account email delivery failed")
        return False
