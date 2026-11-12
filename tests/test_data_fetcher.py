"""fetch_history must not drop the newest bar. No network: yf.download is stubbed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd

from src import data_fetcher


def test_fetch_history_asks_yahoo_through_tomorrow(monkeypatch):
    # yfinance's `end` is exclusive. Ending at today's date hid today's close after the US market closed.
    seen = {}

    def fake_download(symbol, start, end, **kwargs):
        seen["end"] = end
        return pd.DataFrame({"Close": [1.0]}, index=pd.to_datetime(["2026-11-11"]))

    monkeypatch.setattr(data_fetcher.yf, "download", fake_download)
    data_fetcher.fetch_history("NVDA")

    tomorrow = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%d")
    assert seen["end"] == tomorrow
