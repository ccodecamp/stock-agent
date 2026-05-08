"""Technical indicators we feed to the LLM as structured signals.

These are NOT predictions — they're descriptive statistics about where the
stock sits relative to its own history. The LLM interprets them in context.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator
from ta.volatility import AverageTrueRange, BollingerBands


@dataclass
class IndicatorSnapshot:
    """All indicators computed at the most recent close."""

    symbol: str
    as_of: str  # ISO date
    close: float

    sma_50: float
    sma_200: float
    pct_above_sma_50: float
    pct_above_sma_200: float

    rsi_14: float
    rsi_zone: str  # "oversold" | "neutral" | "overbought"

    bb_upper: float
    bb_middle: float
    bb_lower: float
    bb_pct_b: float  # 0 = at lower band, 1 = at upper band

    atr_14: float
    atr_pct_of_close: float  # volatility as % of price

    high_52w: float
    low_52w: float
    pct_off_52w_high: float
    pct_above_52w_low: float

    def to_dict(self) -> dict:
        return asdict(self)


def _safe_last(series: pd.Series) -> float:
    return float(series.dropna().iloc[-1])


def compute_indicators(
    symbol: str,
    df: pd.DataFrame,
    *,
    rsi_oversold: float = 30,
    rsi_overbought: float = 70,
    bb_window: int = 20,
    bb_std: int = 2,
    sma_short: int = 50,
    sma_long: int = 200,
) -> IndicatorSnapshot:
    """Compute the full indicator stack for a single ticker."""
    if df.empty:
        raise ValueError(f"Empty DataFrame for {symbol}")

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    sma_s = SMAIndicator(close=close, window=sma_short).sma_indicator()
    sma_l = SMAIndicator(close=close, window=sma_long).sma_indicator()
    rsi = RSIIndicator(close=close, window=14).rsi()
    bb = BollingerBands(close=close, window=bb_window, window_dev=bb_std)
    atr = AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()

    last_close = _safe_last(close)
    last_sma_s = _safe_last(sma_s)
    last_sma_l = _safe_last(sma_l)
    last_rsi = _safe_last(rsi)
    last_bb_u = _safe_last(bb.bollinger_hband())
    last_bb_m = _safe_last(bb.bollinger_mavg())
    last_bb_l = _safe_last(bb.bollinger_lband())
    last_atr = _safe_last(atr)

    band_width = max(last_bb_u - last_bb_l, 1e-9)
    pct_b = (last_close - last_bb_l) / band_width

    one_year = df.tail(252)
    high_52 = float(one_year["High"].max())
    low_52 = float(one_year["Low"].min())

    if last_rsi >= rsi_overbought:
        zone = "overbought"
    elif last_rsi <= rsi_oversold:
        zone = "oversold"
    else:
        zone = "neutral"

    return IndicatorSnapshot(
        symbol=symbol,
        as_of=df.index[-1].strftime("%Y-%m-%d"),
        close=round(last_close, 2),
        sma_50=round(last_sma_s, 2),
        sma_200=round(last_sma_l, 2),
        pct_above_sma_50=round((last_close / last_sma_s - 1) * 100, 2),
        pct_above_sma_200=round((last_close / last_sma_l - 1) * 100, 2),
        rsi_14=round(last_rsi, 2),
        rsi_zone=zone,
        bb_upper=round(last_bb_u, 2),
        bb_middle=round(last_bb_m, 2),
        bb_lower=round(last_bb_l, 2),
        bb_pct_b=round(pct_b, 3),
        atr_14=round(last_atr, 2),
        atr_pct_of_close=round(last_atr / last_close * 100, 2),
        high_52w=round(high_52, 2),
        low_52w=round(low_52, 2),
        pct_off_52w_high=round((last_close / high_52 - 1) * 100, 2),
        pct_above_52w_low=round((last_close / low_52 - 1) * 100, 2),
    )
