"""Chat tools: input validation, math on cached prices, error handling. No network."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from src.api import tools
from src.data_fetcher import PriceHistory
from tests.test_indicators import _synthetic_df


@pytest.fixture(autouse=True)
def fake_prices(monkeypatch):
    calls = []

    def fake_fetch(symbol, lookback_years=2):
        calls.append(symbol)
        if symbol == "NOPE":
            raise ValueError(f"No price data returned for {symbol}")
        return PriceHistory(symbol=symbol, df=_synthetic_df(n=520), fetched_at=datetime.now(UTC))

    tools.clear_cache()
    monkeypatch.setattr(tools, "fetch_history", fake_fetch)
    yield calls
    tools.clear_cache()


def _run(name, inp):
    content, is_error = tools.execute_tool(name, inp)
    return json.loads(content), is_error


def test_snapshot_returns_indicators():
    out, err = _run("get_stock_snapshot", {"symbol": "NVDA"})
    assert not err
    assert out["symbol"] == "NVDA"
    assert {"close", "rsi_14", "high_52w", "low_52w"} <= out.keys()


def test_price_trend_math_is_consistent():
    out, err = _run("get_price_trend", {"symbol": "NVDA", "period": "1y"})
    assert not err
    assert out["value_of_100_dollars"] == pytest.approx(100 * out["end_price"] / out["start_price"], abs=0.05)
    assert out["pct_change"] == pytest.approx(out["value_of_100_dollars"] - 100, abs=0.05)
    assert 11 <= len(out["monthly_closes"]) <= 14


def test_price_chart_returns_a_summary_for_claude_and_series_for_the_page():
    out, err = _run("get_price_chart", {"symbols": ["NVDA", "AAPL"], "period": "3mo"})
    assert not err
    assert out["chart_shown_to_user"] is True
    assert [s["symbol"] for s in out["chart"]["series"]] == ["NVDA", "AAPL"]
    for series, stock in zip(out["chart"]["series"], out["stocks"], strict=True):
        assert len(series["dates"]) == len(series["closes"]) == 63
        assert stock["start_price"] == series["closes"][0]
        assert stock["end_price"] == series["closes"][-1]
        assert stock["end_date"] == series["dates"][-1]
        assert stock["pct_change"] == pytest.approx((stock["end_price"] / stock["start_price"] - 1) * 100, abs=0.01)
        assert stock["low_price"] <= stock["end_price"] <= stock["high_price"]


@pytest.mark.parametrize(("period", "days"), [("3mo", 63), ("6mo", 126), ("1y", 252)])
def test_price_chart_period_sets_the_number_of_points(period, days):
    out, _ = _run("get_price_chart", {"symbols": ["NVDA"], "period": period})
    assert len(out["chart"]["series"][0]["closes"]) == days


def test_price_chart_drops_repeated_symbols_and_fetches_once(fake_prices):
    out, err = _run("get_price_chart", {"symbols": ["NVDA", "NVDA"]})
    assert not err
    assert len(out["chart"]["series"]) == 1
    assert fake_prices == ["NVDA"]


def test_price_chart_with_one_unknown_ticker_is_an_error_naming_it():
    out, err = _run("get_price_chart", {"symbols": ["NVDA", "NOPE"]})
    assert err
    assert "NOPE" in out["error"]


def test_backtest_strategies_map_to_rules():
    dip, _ = _run("run_backtest", {"symbol": "NVDA", "strategy": "dip_buying"})
    trend, _ = _run("run_backtest", {"symbol": "NVDA", "strategy": "trend_following"})
    assert "RSI" in dip["rule"]
    assert "golden cross" in trend["rule"]


def test_prices_are_cached_across_tools(fake_prices):
    _run("get_stock_snapshot", {"symbol": "NVDA"})
    _run("get_price_trend", {"symbol": "NVDA"})
    assert fake_prices == ["NVDA"]


@pytest.mark.parametrize(
    "name,inp",
    [
        ("get_stock_snapshot", {"symbol": "nvda; drop"}),
        ("get_stock_snapshot", {}),
        ("get_price_trend", {"symbol": "NVDA", "period": "10y"}),
        ("run_backtest", {"symbol": "NVDA", "strategy": "moon"}),
        ("get_stock_snapshot", {"symbol": "NVDA", "extra": 1}),
        ("get_stock_snapshot", "not a dict"),
        ("get_price_chart", {"symbols": []}),
        ("get_price_chart", {"symbols": ["A", "B", "C", "D", "E", "F"]}),
        ("get_price_chart", {"symbols": ["NVDA"], "period": "2y"}),
        ("get_price_chart", {"symbols": "NVDA"}),
        ("get_price_chart", {"symbols": ["nvda; drop"]}),
    ],
)
def test_invalid_input_is_an_error_result(name, inp):
    out, err = _run(name, inp)
    assert err
    assert out["error"] == "Invalid input"


def test_unknown_ticker_is_an_error_result():
    out, err = _run("get_stock_snapshot", {"symbol": "NOPE"})
    assert err
    assert "NOPE" in out["error"]


def test_unknown_tool():
    out, err = _run("delete_everything", {})
    assert err


def test_find_company_filters_to_stocks(monkeypatch):
    class FakeSearch:
        def __init__(self, query, max_results):
            self.quotes = [
                {"symbol": "NVDA", "longname": "NVIDIA Corporation", "exchDisp": "NASDAQ", "quoteType": "EQUITY"},
                {"symbol": "NVDA250117C", "quoteType": "OPTION"},
            ]

    monkeypatch.setattr(tools.yf, "Search", FakeSearch)
    out, err = _run("find_company", {"query": "Nvidia"})
    assert not err
    assert [m["symbol"] for m in out["matches"]] == ["NVDA"]


def test_tool_schemas_match_handlers():
    assert {t["name"] for t in tools.TOOLS} == set(tools._HANDLERS)
