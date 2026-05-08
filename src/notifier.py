"""WhatsApp delivery via Twilio.

Why Twilio: the easiest way to send WhatsApp messages programmatically
without setting up a Meta Business account. Free sandbox is enough for
personal monthly digests.

Setup:
1. Create a Twilio account, enable the WhatsApp sandbox.
2. Join the sandbox from your phone (one WhatsApp message).
3. Put the credentials in `.env`.
"""

from __future__ import annotations

import logging
import os

from twilio.rest import Client

logger = logging.getLogger(__name__)

# WhatsApp body limit is ~1600 chars; we keep margin for safety.
MAX_CHUNK_CHARS = 1400


def _chunk(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split a long message on paragraph boundaries to fit WhatsApp limits."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for para in text.split("\n\n"):
        p = para.strip()
        if not p:
            continue
        if size + len(p) + 2 > limit and buf:
            chunks.append("\n\n".join(buf))
            buf = [p]
            size = len(p)
        else:
            buf.append(p)
            size += len(p) + 2
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def send_whatsapp(message: str, *, dry_run: bool | None = None) -> None:
    """Send `message` to the configured WhatsApp number.

    If DRY_RUN env var is "true" (or `dry_run=True`), prints to stdout instead.
    Raises RuntimeError if credentials are missing in non-dry-run mode.
    """
    if dry_run is None:
        dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    chunks = _chunk(message)

    if dry_run:
        print("=" * 60)
        print(f"[DRY RUN] Would send {len(chunks)} WhatsApp message(s):")
        print("=" * 60)
        for i, c in enumerate(chunks, 1):
            print(f"\n--- chunk {i}/{len(chunks)} ---\n{c}")
        return

    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    token = os.environ.get("TWILIO_AUTH_TOKEN")
    from_ = os.environ.get("TWILIO_WHATSAPP_FROM")
    to = os.environ.get("USER_WHATSAPP_TO")

    missing = [k for k, v in {
        "TWILIO_ACCOUNT_SID": sid,
        "TWILIO_AUTH_TOKEN": token,
        "TWILIO_WHATSAPP_FROM": from_,
        "USER_WHATSAPP_TO": to,
    }.items() if not v]
    if missing:
        raise RuntimeError(
            f"Missing Twilio env vars: {missing}. "
            "Set DRY_RUN=true to test without sending."
        )

    client = Client(sid, token)
    for i, chunk in enumerate(chunks, 1):
        prefix = f"[{i}/{len(chunks)}] " if len(chunks) > 1 else ""
        msg = client.messages.create(from_=from_, to=to, body=prefix + chunk)
        logger.info("Sent WhatsApp chunk %d/%d (sid=%s)", i, len(chunks), msg.sid)
