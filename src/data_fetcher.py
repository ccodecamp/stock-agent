"""Pulls OHLCV price history from Yahoo Finance.

We use yfinance because it's free, key-less, and good enough for monthly
batch analysis. For higher reliability, swap in Alpha Vantage or Polygon.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceHistory:
    """Wrapper around a ticker's OHLCV DataFrame."""

    symbol: str
    df: pd.DataFrame
    fetched_at: datetime

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
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(lookback_years * 365.25))

    logger.info("Fetching %s from %s to %s", symbol, start.date(), end.date())
    df = yf.download(
        symbol,
        start=start.strftime("%Y-%m-%d"),
        end=end.strftime("%Y-%m-%d"),
        progress=False,
        auto_adjust=True,
    )
    # yfinance occasionally returns multi-index columns when downloading
    # a single ticker; flatten that for downstream simplicity.
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    if df.empty:
        raise ValueError(f"No price data returned for {symbol}")

    return PriceHistory(symbol=symbol, df=df, fetched_at=datetime.now(timezone.utc))


def fetch_many(symbols: list[str], lookback_years: int = 2) -> dict[str, PriceHistory]:
    """Fetch many tickers, skipping any that fail with a logged warning."""
    out: dict[str, PriceHistory] = {}
    for sym in symbols:
        try:
            out[sym] = fetch_history(sym, lookback_years=lookback_years)
        except Exception as e:  # noqa: BLE001 — we want to keep going
            logger.warning("Failed to fetch %s: %s", sym, e)
    return out
