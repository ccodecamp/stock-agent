"""A *deliberately humble* backtester.

The goal here is not to "find an edge" — it's to stress-test naive rules
against history so the user sees how often they'd actually have worked.
If a rule loses to buy-and-hold, the user knows to distrust it.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from ta.momentum import RSIIndicator
from ta.trend import SMAIndicator


@dataclass
class BacktestResult:
    symbol: str
    rule: str
    n_signals: int
    avg_30d_forward_return_pct: float
    win_rate_30d_pct: float
    buy_and_hold_total_return_pct: float
    rule_total_return_pct: float


def backtest_rsi_oversold(df: pd.DataFrame, symbol: str, threshold: float = 30) -> BacktestResult:
    """Buy when RSI(14) crosses below `threshold`, hold 30 trading days."""
    close = df["Close"].copy()
    rsi = RSIIndicator(close=close, window=14).rsi()

    crossings = (rsi.shift(1) >= threshold) & (rsi < threshold)
    signal_dates = close.index[crossings.fillna(False)]

    forward_returns: list[float] = []
    for d in signal_dates:
        idx = close.index.get_loc(d)
        if idx + 30 >= len(close):
            continue
        entry = float(close.iloc[idx])
        exit_ = float(close.iloc[idx + 30])
        forward_returns.append((exit_ / entry - 1) * 100)

    n = len(forward_returns)
    avg = sum(forward_returns) / n if n else 0.0
    win_rate = (sum(1 for r in forward_returns if r > 0) / n * 100) if n else 0.0

    bh = (float(close.iloc[-1]) / float(close.iloc[0]) - 1) * 100
    rule_total = sum(forward_returns)  # naive: sum of independent 30d trades

    return BacktestResult(
        symbol=symbol,
        rule=f"RSI(14) crosses below {threshold}, hold 30d",
        n_signals=n,
        avg_30d_forward_return_pct=round(avg, 2),
        win_rate_30d_pct=round(win_rate, 1),
        buy_and_hold_total_return_pct=round(bh, 2),
        rule_total_return_pct=round(rule_total, 2),
    )


def backtest_golden_cross(df: pd.DataFrame, symbol: str) -> BacktestResult:
    """Buy on 50-day SMA crossing above 200-day SMA, hold 30d."""
    close = df["Close"].copy()
    sma50 = SMAIndicator(close=close, window=50).sma_indicator()
    sma200 = SMAIndicator(close=close, window=200).sma_indicator()

    cross_up = (sma50.shift(1) <= sma200.shift(1)) & (sma50 > sma200)
    signal_dates = close.index[cross_up.fillna(False)]

    forward_returns: list[float] = []
    for d in signal_dates:
        idx = close.index.get_loc(d)
        if idx + 30 >= len(close):
            continue
        entry = float(close.iloc[idx])
        exit_ = float(close.iloc[idx + 30])
        forward_returns.append((exit_ / entry - 1) * 100)

    n = len(forward_returns)
    avg = sum(forward_returns) / n if n else 0.0
    win_rate = (sum(1 for r in forward_returns if r > 0) / n * 100) if n else 0.0
    bh = (float(close.iloc[-1]) / float(close.iloc[0]) - 1) * 100

    return BacktestResult(
        symbol=symbol,
        rule="50d SMA crosses above 200d SMA (golden cross), hold 30d",
        n_signals=n,
        avg_30d_forward_return_pct=round(avg, 2),
        win_rate_30d_pct=round(win_rate, 1),
        buy_and_hold_total_return_pct=round(bh, 2),
        rule_total_return_pct=round(sum(forward_returns), 2),
    )
