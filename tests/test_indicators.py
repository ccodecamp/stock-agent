"""Indicator math should be deterministic and bound to known ranges."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.indicators import compute_indicators


def _synthetic_df(n: int = 400, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    # Geometric brownian motion-ish series, positive prices.
    rets = rng.normal(0.0005, 0.015, n)
    close = 100 * np.cumprod(1 + rets)
    high = close * (1 + np.abs(rng.normal(0.005, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0.005, 0.003, n)))
    open_ = close * (1 + rng.normal(0, 0.002, n))
    vol = rng.integers(1_000_000, 5_000_000, n)
    idx = pd.date_range(end=pd.Timestamp.today(), periods=n, freq="B")
    return pd.DataFrame(
        {"Open": open_, "High": high, "Low": low, "Close": close, "Volume": vol},
        index=idx,
    )


def test_compute_indicators_basic_ranges():
    df = _synthetic_df()
    snap = compute_indicators("TEST", df)

    assert 0 <= snap.rsi_14 <= 100
    assert snap.bb_lower <= snap.bb_middle <= snap.bb_upper
    assert snap.atr_14 > 0
    assert snap.high_52w >= snap.low_52w
    assert snap.close > 0
    assert snap.rsi_zone in {"oversold", "neutral", "overbought"}


def test_zone_classification():
    df = _synthetic_df()
    snap = compute_indicators("TEST", df, rsi_oversold=999, rsi_overbought=-1)
    # impossible thresholds → always overbought
    assert snap.rsi_zone == "overbought"


def test_pct_b_in_unit_interval_for_normal_data():
    df = _synthetic_df()
    snap = compute_indicators("TEST", df)
    # Synthetic random walks usually stay inside bands; allow a small overshoot.
    assert -0.5 <= snap.bb_pct_b <= 1.5


def test_empty_df_raises():
    with pytest.raises(ValueError):
        compute_indicators("EMPTY", pd.DataFrame())
