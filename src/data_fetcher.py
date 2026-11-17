"""Pulls OHLCV price history from Yahoo Finance.

We use yfinance because it's free, key-less, and good enough for monthly
batch analysis. For higher reliability, swap in Alpha Vantage or Polygon.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd
import yfinance as yf

from . import markets

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceHistory:
    """Wrapper around a ticker's OHLCV DataFrame."""

    symbol: str
    df: pd.DataFrame
    fetched_at: datetime
    # Main currency the prices are in (pence are already converted to pounds). None for an index or when unknown.
    currency: str | None = None

    @property
    def latest_close(self) -> float:
        return float(self.df["Close"].iloc[-1])

    @property
    def latest_date(self) -> datetime:
        return self.df.index[-1].to_pydatetime()


def fetch_history(symbol: str, lookback_years: int = 2) -> PriceHistory:
    """Fetch daily OHLCV bars for `symbol` going back `lookback_years` years.

    Raises:
        ValueError: if the response is empty (bad ticker, network issue).
    """
    end = datetime.now(UTC)
    start = end - timedelta(days=int(lookback_years * 365.25))

    logger.info("Fetching %s from %s to %s", symbol, start.date(), end.date())
    df = yf.download(
        symbol,
        start=start.strftime("%Y-%m-%d"),
        # yfinance treats `end` as exclusive: ending at today's UTC date drops today's bar, so
        # after the US close (21:00 UTC) the newest close went missing. Ask through tomorrow.
        end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=True,
    )
    # yfinance occasionally returns multi-index columns when downloading
    # a single ticker; flatten that for downstream simplicity.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    # A market that has not closed yet can come back with today's row present but empty (seen in Europe).
    # Left in, it would be read as the latest close.
    df = df.dropna(subset=["Close"])

    if df.empty:
        raise ValueError(f"No price data returned for {symbol}")

    currency, divisor = _currency_and_divisor(symbol)
    if divisor != 1:
        price_columns = [c for c in ("Open", "High", "Low", "Close") if c in df.columns]
        df = df.copy()
        df[price_columns] = df[price_columns] / divisor

    return PriceHistory(symbol=symbol, df=df, fetched_at=datetime.now(UTC), currency=currency)


def _currency_and_divisor(symbol: str) -> tuple[str | None, int]:
    """What the prices are in. Yahoo's own answer wins; our table is the fallback if it can't be reached."""
    market = markets.lookup(symbol)
    if market.is_index:
        return None, 1
    quoted = None
    try:
        quoted = yf.Ticker(symbol).history_metadata.get("currency")
    except Exception as e:  # noqa: BLE001 — a missing label must not lose the prices
        logger.warning("No currency from Yahoo for %s: %s", symbol, e)
    if not quoted:
        # Without Yahoo's answer, assume London is quoted in pence, which it nearly always is.
        quoted = "GBp" if symbol.upper().endswith(".L") else market.currency
    return markets.to_main_unit(quoted)


def fetch_many(symbols: list[str], lookback_years: int = 2) -> dict[str, PriceHistory]:
    """Fetch many tickers, skipping any that fail with a logged warning."""
    out: dict[str, PriceHistory] = {}
    for sym in symbols:
        try:
            out[sym] = fetch_history(sym, lookback_years=lookback_years)
        except Exception as e:  # noqa: BLE001 — we want to keep going
            logger.warning("Failed to fetch %s: %s", sym, e)
    return out
