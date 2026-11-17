"""A daily cap on chat requests, to put a ceiling on the Anthropic bill.

The cap is one shared bucket for the whole app, not per person. There are no accounts, and behind a
reverse proxy every request looks like it comes from the same address, so there is nothing reliable to
count per person yet. The day is a UTC day. The count lives in memory, so a restart or redeploy resets it.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta

DEFAULT_DAILY_LIMIT = 300


def configured_limit() -> int:
    """CHAT_DAILY_LIMIT from the environment. 0 turns the cap off. Anything unreadable gets the default."""
    raw = os.environ.get("CHAT_DAILY_LIMIT", "").strip()
    if not raw:
        return DEFAULT_DAILY_LIMIT
    try:
        return max(int(raw), 0)
    except ValueError:
        return DEFAULT_DAILY_LIMIT


class DailyLimit:
    def __init__(self, now: Callable[[], datetime] = lambda: datetime.now(UTC)):
        self._now = now
        self._lock = threading.Lock()
        self._day: date | None = None
        self._count = 0

    def try_take(self, limit: int) -> bool:
        """Count one request. False means today's cap is used up and the request must be refused.

        A refused request is not counted, and a limit of 0 or less means no cap.
        """
        if limit <= 0:
            return True
        with self._lock:
            today = self._now().astimezone(UTC).date()
            if today != self._day:
                self._day, self._count = today, 0
            if self._count >= limit:
                return False
            self._count += 1
            return True

    def resets_at(self) -> datetime:
        """The next UTC midnight."""
        today = self._now().astimezone(UTC).date()
        return datetime.combine(today + timedelta(days=1), time.min, tzinfo=UTC)

    def reset(self) -> None:
        with self._lock:
            self._day, self._count = None, 0


daily_limit = DailyLimit()
