"""The daily chat cap. No network, no API key."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from src.api import app as app_module
from src.api.limits import DEFAULT_DAILY_LIMIT, DailyLimit, configured_limit, daily_limit

BODY = {"messages": [{"role": "user", "content": "How's Apple?"}]}


class Clock:
    def __init__(self, start: datetime):
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def _clock(hour=12, day=10) -> Clock:
    return Clock(datetime(2026, 11, day, hour, 0, tzinfo=UTC))


# ---- the counter ----


def test_allows_exactly_the_limit_then_refuses():
    cap = DailyLimit(_clock())
    assert [cap.try_take(3) for _ in range(5)] == [True, True, True, False, False]


def test_a_refused_request_is_not_counted():
    clock = _clock()
    cap = DailyLimit(clock)
    for _ in range(4):
        cap.try_take(2)
    clock.now += timedelta(days=1)
    assert [cap.try_take(2) for _ in range(3)] == [True, True, False]


def test_the_count_starts_again_at_utc_midnight():
    clock = _clock(hour=23, day=10)
    cap = DailyLimit(clock)
    assert cap.try_take(1) and not cap.try_take(1)
    clock.now = datetime(2026, 11, 11, 0, 0, tzinfo=UTC)
    assert cap.try_take(1)


def test_the_day_is_utc_even_when_the_clock_has_another_timezone():
    # 23:30 on Nov 10 in UTC-5 is already Nov 11 in UTC.
    clock = Clock(datetime(2026, 11, 10, 23, 30, tzinfo=timezone(timedelta(hours=-5))))
    cap = DailyLimit(clock)
    assert cap.try_take(1) and not cap.try_take(1)
    assert cap.resets_at() == datetime(2026, 11, 12, 0, 0, tzinfo=UTC)


def test_zero_or_negative_means_no_cap():
    cap = DailyLimit(_clock())
    assert all(cap.try_take(0) for _ in range(1000))
    assert all(cap.try_take(-1) for _ in range(10))


def test_resets_at_is_the_next_utc_midnight():
    assert DailyLimit(_clock(hour=0, day=10)).resets_at() == datetime(2026, 11, 11, 0, 0, tzinfo=UTC)
    assert DailyLimit(_clock(hour=23, day=10)).resets_at() == datetime(2026, 11, 11, 0, 0, tzinfo=UTC)


def test_reset_clears_the_count():
    cap = DailyLimit(_clock())
    cap.try_take(1)
    cap.reset()
    assert cap.try_take(1)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, DEFAULT_DAILY_LIMIT), ("", DEFAULT_DAILY_LIMIT), ("  ", DEFAULT_DAILY_LIMIT), ("50", 50), (" 50 ", 50),
     ("0", 0), ("-5", 0), ("lots", DEFAULT_DAILY_LIMIT), ("2.5", DEFAULT_DAILY_LIMIT)],
)
def test_configured_limit_reads_the_environment_safely(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("CHAT_DAILY_LIMIT", raising=False)
    else:
        monkeypatch.setenv("CHAT_DAILY_LIMIT", raw)
    assert configured_limit() == expected


# ---- through the HTTP layer ----


@pytest.fixture
def api(monkeypatch):
    calls = []

    def fake_stream(messages):
        calls.append(messages)
        return iter([{"type": "text", "text": "hi"}])

    monkeypatch.setattr(app_module, "stream_chat", fake_stream)
    app_module.limiter.reset()
    daily_limit.reset()
    client = TestClient(app_module.app)
    client.calls = calls
    yield client
    daily_limit.reset()


def test_chat_is_refused_with_a_clear_reason_once_the_day_is_used_up(api, monkeypatch):
    monkeypatch.setenv("CHAT_DAILY_LIMIT", "2")
    codes = [api.post("/api/chat", json=BODY).status_code for _ in range(3)]
    assert codes == [200, 200, 429]
    refused = api.post("/api/chat", json=BODY)
    data = refused.json()
    assert data["code"] == "daily_limit"
    assert datetime.fromisoformat(data["resets_at"]).tzinfo is not None
    assert int(refused.headers["retry-after"]) > 0


def test_a_refused_chat_never_reaches_the_model(api, monkeypatch):
    monkeypatch.setenv("CHAT_DAILY_LIMIT", "1")
    api.post("/api/chat", json=BODY)
    api.post("/api/chat", json=BODY)
    assert len(api.calls) == 1


def test_invalid_requests_do_not_use_up_the_day(api, monkeypatch):
    monkeypatch.setenv("CHAT_DAILY_LIMIT", "1")
    assert api.post("/api/chat", json={"messages": []}).status_code == 422
    assert api.post("/api/chat", json=BODY).status_code == 200


def test_the_per_minute_limit_is_still_told_apart_and_does_not_use_up_the_day(api, monkeypatch):
    monkeypatch.setenv("CHAT_RATE_LIMIT", "1/minute")
    monkeypatch.setenv("CHAT_DAILY_LIMIT", "5")
    assert api.post("/api/chat", json=BODY).status_code == 200
    too_fast = api.post("/api/chat", json=BODY)
    assert too_fast.status_code == 429
    assert too_fast.json().get("code") != "daily_limit"
    # Only the first request reached the daily count, so four of the five are left.
    monkeypatch.setenv("CHAT_RATE_LIMIT", "1000/minute")
    app_module.limiter.reset()
    assert [api.post("/api/chat", json=BODY).status_code for _ in range(5)] == [200, 200, 200, 200, 429]


def test_zero_turns_the_cap_off(api, monkeypatch):
    monkeypatch.setenv("CHAT_DAILY_LIMIT", "0")
    monkeypatch.setenv("CHAT_RATE_LIMIT", "1000/minute")
    assert all(api.post("/api/chat", json=BODY).status_code == 200 for _ in range(20))


def test_the_page_and_health_still_work_when_the_day_is_used_up(api, monkeypatch):
    monkeypatch.setenv("CHAT_DAILY_LIMIT", "1")
    api.post("/api/chat", json=BODY)
    assert api.post("/api/chat", json=BODY).status_code == 429
    assert api.get("/api/health").status_code == 200
    assert api.get("/").status_code == 200
