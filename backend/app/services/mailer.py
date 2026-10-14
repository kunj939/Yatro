"""Sends one email through the configured SMTP server (SMTP_HOST, SMTP_USER, SMTP_PASSWORD, SMTP_FROM).

Used for the plan emails. Sign-up codes have their own sender (services/verification_codes.py), which also falls back to showing the code on
screen in development; nothing here ever pretends to send: no mail server set means `configured()` is False and the caller skips.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, parseaddr

from app.core.config import get_settings

log = logging.getLogger("tripai.mailer")


class MailError(Exception):
    """The mail server could not be reached or refused the message."""


def configured() -> bool:
    return bool(get_settings().smtp_host)


def send_mail(to: str, subject: str, text: str, html: str | None = None) -> None:
    """Send one message. Raises MailError on any failure (never a raw smtplib error), so callers have one thing to catch."""
    s = get_settings()
    if not s.smtp_host:
        raise MailError("no mail server is configured")
    msg = EmailMessage()
    msg["Subject"] = subject
    sender = s.smtp_from or s.smtp_user or f"no-reply@{s.smtp_host}"
    name, addr = parseaddr(sender)
    msg["From"] = formataddr((name or "Yaitro", addr)) if addr else sender
    msg["To"] = to
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    try:
        if s.smtp_port == 465:
            with smtplib.SMTP_SSL(s.smtp_host, s.smtp_port, timeout=20, context=ssl.create_default_context()) as smtp:
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                smtp.send_message(msg)
        else:
            with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
                smtp.starttls(context=ssl.create_default_context())
                if s.smtp_user:
                    smtp.login(s.smtp_user, s.smtp_password or "")
                smtp.send_message(msg)
    except (smtplib.SMTPException, OSError) as exc:
        raise MailError(f"{type(exc).__name__}: {exc}") from exc
