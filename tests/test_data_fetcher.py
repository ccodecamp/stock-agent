"""fetch_history: keep the newest real bar, drop empty ones, label the currency. No network: yfinance is stubbed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src import data_fetcher


def _stub_currency(monkeypatch, currency):
    """What Yahoo says the prices are in. An Exception instance makes the lookup fail."""

    def fake_ticker(symbol):
        if isinstance(currency, Exception):
            raise currency
        return SimpleNamespace(history_metadata={"currency": currency} if currency else {})

    monkeypatch.setattr(data_fetcher.yf, "Ticker", fake_ticker)


def _stub_download(monkeypatch, closes, highs=None):
    dates = pd.to_datetime([f"2026-11-{10 + i}" for i in range(len(closes))])
    df = pd.DataFrame(
        {
            "Open": closes,
            "High": highs or closes,
            "Low": closes,
            "Close": closes,
            "Volume": [1] * len(closes),
        },
        index=dates,
    )
    monkeypatch.setattr(data_fetcher.yf, "download", lambda *a, **k: df)


def test_fetch_history_asks_yahoo_through_tomorrow(monkeypatch):
    # yfinance's `end` is exclusive. Ending at today's date hid today's close after the US market closed.
    seen = {}

    def fake_download(symbol, start, end, **kwargs):
        seen["end"] = end
        return pd.DataFrame({"Close": [1.0]}, index=pd.to_datetime(["2026-11-11"]))

    monkeypatch.setattr(data_fetcher.yf, "download", fake_download)
    _stub_currency(monkeypatch, "USD")
    data_fetcher.fetch_history("NVDA")

    tomorrow = (datetime.now(UTC) + timedelta(days=1)).strftime("%Y-%m-%d")
    assert seen["end"] == tomorrow


def test_an_empty_latest_row_is_dropped_so_it_is_never_read_as_the_latest_close(monkeypatch):
    # Seen with SAP.DE, SHEL.L, MC.PA and ENEL.MI before their markets had closed: today's row existed, but empty.
    _stub_download(monkeypatch, [212.5, 213.4, np.nan])
    _stub_currency(monkeypatch, "EUR")
    history = data_fetcher.fetch_history("SAP.DE")
    assert history.latest_close == 213.4
    assert history.latest_date.strftime("%Y-%m-%d") == "2026-11-11"
    assert len(history.df) == 2


def test_a_symbol_with_only_empty_rows_is_a_no_data_error(monkeypatch):
    _stub_download(monkeypatch, [np.nan, np.nan])
    _stub_currency(monkeypatch, "EUR")
    with pytest.raises(ValueError, match="No price data"):
        data_fetcher.fetch_history("SAP.DE")


def test_london_prices_in_pence_become_pounds(monkeypatch):
    _stub_download(monkeypatch, [2750.0, 2800.0], highs=[2760.0, 2810.0])
    _stub_currency(monkeypatch, "GBp")
    history = data_fetcher.fetch_history("SHEL.L")
    assert history.currency == "GBP"
    assert history.latest_close == pytest.approx(28.0)
    assert history.df["High"].iloc[-1] == pytest.approx(28.10)
    assert history.df["Volume"].iloc[-1] == 1  # only prices are scaled


@pytest.mark.parametrize(("symbol", "quoted", "expected"), [
    ("AAPL", "USD", "USD"),
    ("7203.T", "JPY", "JPY"),
    ("0700.HK", "HKD", "HKD"),
    ("RELIANCE.NS", "INR", "INR"),
])
def test_the_currency_is_what_yahoo_says(monkeypatch, symbol, quoted, expected):
    _stub_download(monkeypatch, [100.0, 101.0])
    _stub_currency(monkeypatch, quoted)
    history = data_fetcher.fetch_history(symbol)
    assert history.currency == expected
    assert history.latest_close == 101.0  # not scaled


def test_when_yahoo_cannot_say_we_fall_back_to_our_table_and_london_is_assumed_to_be_pence(monkeypatch):
    _stub_download(monkeypatch, [100.0, 101.0])
    _stub_currency(monkeypatch, RuntimeError("down"))
    assert data_fetcher.fetch_history("7203.T").currency == "JPY"
    assert data_fetcher.fetch_history("NVDA").currency == "USD"
    london = data_fetcher.fetch_history("SHEL.L")
    assert london.currency == "GBP"
    assert london.latest_close == pytest.approx(1.01)


def test_an_empty_currency_answer_also_falls_back(monkeypatch):
    _stub_download(monkeypatch, [100.0, 101.0])
    _stub_currency(monkeypatch, None)
    assert data_fetcher.fetch_history("BHP.AX").currency == "AUD"


def test_an_index_has_no_currency_whatever_yahoo_says(monkeypatch):
    _stub_download(monkeypatch, [51000.0, 51600.0])
    _stub_currency(monkeypatch, "USD")  # Yahoo labels ^DJI "USD", but its level is in points
    assert data_fetcher.fetch_history("^DJI").currency is None
