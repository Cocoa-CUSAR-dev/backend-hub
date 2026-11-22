"""Sending email over SMTP: the ONLY file that imports smtplib (CONVENTIONS.md §12).

Used for the forgot-password code. Any SMTP provider works; the host, port and
login come from the settings. `smtplib` blocks, so it runs in a worker thread.
"""

import asyncio
import smtplib
import ssl
from email.message import EmailMessage

from src.config import settings
from src.exceptions import Unavailable

_TIMEOUT_SECONDS = 15
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _login_and_send(smtp: smtplib.SMTP, message: EmailMessage) -> None:
    if settings.SMTP_USER:
        smtp.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
    smtp.send_message(message)


def _send(message: EmailMessage) -> None:
    host, port = settings.SMTP_HOST, settings.SMTP_PORT
    context = ssl.create_default_context()
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=_TIMEOUT_SECONDS, context=context) as smtp:
            _login_and_send(smtp, message)
        return
    with smtplib.SMTP(host, port, timeout=_TIMEOUT_SECONDS) as smtp:
        smtp.ehlo()
        # A relay on this machine (a mail catcher in development) may not offer
        # TLS; every other server has to, or nothing is sent in the clear.
        if host not in _LOCAL_HOSTS or smtp.has_extn("starttls"):
            smtp.starttls(context=context)
            smtp.ehlo()
        _login_and_send(smtp, message)


async def send_email(to: str, subject: str, body: str) -> None:
    """Plain-text email. Raises Unavailable when email is not set up or the
    server refuses or cannot be reached; callers decide what that means.
    """
    if not settings.email_configured:
        raise Unavailable("Email is not set up")
    message = EmailMessage()
    message["From"] = settings.MAIL_FROM
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)
    try:
        await asyncio.to_thread(_send, message)
    except (OSError, smtplib.SMTPException) as exc:
        raise Unavailable("Could not send the email") from exc
