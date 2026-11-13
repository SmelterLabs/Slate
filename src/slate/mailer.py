"""Plain SMTP for email notifications (service/email.py). Any SMTP server works; a hosted
sender such as Resend, Postmark or SES takes its SMTP credentials here. Port 465 is implicit
TLS; any other port upgrades with STARTTLS when the server offers it. Nothing else in Slate
sends mail."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from slate.settings import settings

TIMEOUT = 20.0


def enabled() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


def send(to: str, subject: str, text: str, html: str | None = None) -> str | None:
    """Send one message. Returns None on success, else a short error; never raises, so one
    bad message (a header the stdlib refuses, say) is that message's failure only."""
    try:
        msg = EmailMessage()
        msg["From"] = settings.smtp_from
        msg["To"] = to
        msg["Subject"] = " ".join(subject.split())  # a header value may not hold a line break
        msg.set_content(text)
        if html:
            msg.add_alternative(html, subtype="html")
        ctx = ssl.create_default_context()
        if settings.smtp_port == 465:
            conn: smtplib.SMTP = smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port, timeout=TIMEOUT, context=ctx
            )
        else:
            conn = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=TIMEOUT)
        with conn:
            if settings.smtp_port != 465:
                conn.ehlo()
                if conn.has_extn("starttls"):
                    conn.starttls(context=ctx)
                    conn.ehlo()
            if settings.smtp_user:
                conn.login(settings.smtp_user, settings.smtp_password)
            conn.send_message(msg)
    except Exception as exc:  # noqa: BLE001  # SMTP and socket errors, and bad header values
        return f"{type(exc).__name__}: {exc}"[:300]
    return None
