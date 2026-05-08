"""Backtester sanity: returns valid, comparable structures."""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.backtest import backtest_golden_cross, backtest_rsi_oversold


def _synthetic_df(n: int = 600, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0005, 0.015, n)
    close = 100 * np.cumprod(1 + rets)
    high = close * 1.01
    low = close * 0.99
    idx = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
    return pd.DataFrame(
        {"Open": close, "High": high, "Low": low, "Close": close, "Volume": 1},
        index=idx,
    )


def test_rsi_backtest_produces_results():
    r = backtest_rsi_oversold(_synthetic_df(), "TEST")
    assert r.symbol == "TEST"
    assert r.n_signals >= 0
    assert isinstance(r.avg_30d_forward_return_pct, float)


def test_golden_cross_backtest_produces_results():
    r = backtest_golden_cross(_synthetic_df(), "TEST")
    assert r.symbol == "TEST"
    assert r.n_signals >= 0
