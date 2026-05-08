"""Email delivery via Gmail SMTP.

Why Gmail SMTP: zero third-party signups, free, uses Python's stdlib
(`smtplib`, `email`). Works with any Gmail account that has 2-Step
Verification enabled and an App Password configured.

Setup:
1. Turn on 2-Step Verification at https://myaccount.google.com/security
2. Create an App Password at https://myaccount.google.com/apppasswords
   (or, if Google hides the link, search "App Passwords" in the search
   bar at the top of myaccount.google.com).
3. Paste the 16-character password into `.env` as `GMAIL_APP_PASSWORD`.
   Spaces are fine — they're stripped automatically.
"""

from __future__ import annotations

import logging
import os
import smtplib
import ssl
from email.message import EmailMessage

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465  # SSL


def send_email(
    subject: str,
    body: str,
    *,
    dry_run: bool | None = None,
) -> None:
    """Send a plain-text email via Gmail SMTP.

    If `DRY_RUN` env var is "true" (or `dry_run=True`), prints to stdout
    instead of sending. Raises `RuntimeError` if any required env var is
    missing in non-dry-run mode.
    """
    if dry_run is None:
        dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    if dry_run:
        print("=" * 60)
        print("[DRY RUN] Would send email:")
        print(f"Subject: {subject}")
        print("=" * 60)
        print(body)
        return

    user = os.environ.get("GMAIL_USER")
    # App passwords from Google sometimes display with spaces — strip them.
    pwd = (os.environ.get("GMAIL_APP_PASSWORD") or "").replace(" ", "")
    to = os.environ.get("EMAIL_TO")

    missing = [
        k
        for k, v in {
            "GMAIL_USER": user,
            "GMAIL_APP_PASSWORD": pwd,
            "EMAIL_TO": to,
        }.items()
        if not v
    ]
    if missing:
        raise RuntimeError(
            f"Missing Gmail env vars: {missing}. "
            "Set DRY_RUN=true to test without sending."
        )

    msg = EmailMessage()
    msg["From"] = user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as smtp:
        smtp.login(user, pwd)
        smtp.send_message(msg)
    logger.info("Sent email to %s (subject=%r)", to, subject)
