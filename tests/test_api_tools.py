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
    assert out["value_of_100"] == pytest.approx(100 * out["end_price"] / out["start_price"], abs=0.05)
    assert out["pct_change"] == pytest.approx(out["value_of_100"] - 100, abs=0.05)
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


def test_single_stock_chart_also_carries_open_high_low_for_candles():
    out, err = _run("get_price_chart", {"symbols": ["NVDA"], "period": "3mo"})
    assert not err
    series = out["chart"]["series"][0]
    expected = _synthetic_df(n=520).tail(63)
    assert series["opens"] == [round(float(v), 2) for v in expected["Open"]]
    assert series["highs"] == [round(float(v), 2) for v in expected["High"]]
    assert series["lows"] == [round(float(v), 2) for v in expected["Low"]]
    assert len(series["opens"]) == len(series["closes"])


def test_multi_stock_chart_stays_a_plain_line_chart():
    out, err = _run("get_price_chart", {"symbols": ["NVDA", "AAPL"], "period": "3mo"})
    assert not err
    for series in out["chart"]["series"]:
        assert not {"opens", "highs", "lows"} & series.keys()


def test_chart_without_complete_open_prices_falls_back_to_line_only(monkeypatch):
    def fake_fetch(symbol, lookback_years=2):
        df = _synthetic_df(n=520)
        df.loc[df.index[-5], "Open"] = float("nan")  # some indexes have gaps in Open
        return PriceHistory(symbol=symbol, df=df, fetched_at=datetime.now(UTC))

    monkeypatch.setattr(tools, "fetch_history", fake_fetch)
    tools.clear_cache()
    out, err = _run("get_price_chart", {"symbols": ["^DJI"], "period": "3mo"})
    assert not err
    assert "opens" not in out["chart"]["series"][0]


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


def test_unknown_tool_returns_error():
    result, is_error = _run("does_not_exist", {})
    assert is_error is True
    assert "Unknown tool" in result["error"]


# ---- other markets ----


@pytest.mark.parametrize(
    "symbol",
    ["NVDA", "BRK-B", "BRK.B", "7203.T", "0700.HK", "600519.SS", "000001.SZ", "RELIANCE.NS", "BAJAJ-AUTO.NS",
     "SHEL.L", "SAP.DE", "D05.SI", "1155.KL", "^DJI", "^N225", "^SET.BK", "FTSEMIB.MI"],
)
def test_tickers_from_every_market_are_accepted(symbol):
    out, err = _run("get_stock_snapshot", {"symbol": symbol})
    assert not err, out
    assert out["symbol"] == symbol


@pytest.mark.parametrize(
    "symbol",
    ["", "^", "^^DJI", "D^JI", "nvda", "7203.t", "AAPL;DROP", "AA PL", "../etc", "A" * 18, ".T", "-X", "$AAPL"],
)
def test_malformed_tickers_are_still_rejected(symbol):
    out, err = _run("get_stock_snapshot", {"symbol": symbol})
    assert err
    assert out["error"] == "Invalid input"


def test_every_result_says_which_market_and_currency(fake_prices):
    snap, _ = _run("get_stock_snapshot", {"symbol": "7203.T"})
    assert (snap["market"], snap["currency"]) == ("Tokyo", "JPY")
    trend, _ = _run("get_price_trend", {"symbol": "0700.HK"})
    assert (trend["market"], trend["currency"]) == ("Hong Kong", "HKD")
    test_, _ = _run("run_backtest", {"symbol": "RELIANCE.NS", "strategy": "dip_buying"})
    assert (test_["market"], test_["currency"]) == ("India (NSE)", "INR")
    us, _ = _run("get_stock_snapshot", {"symbol": "NVDA"})
    assert (us["market"], us["currency"]) == ("US", "USD")


def test_the_currency_yahoo_reported_wins_over_the_table(monkeypatch):
    def fake_fetch(symbol, lookback_years=2):
        return PriceHistory(symbol=symbol, df=_synthetic_df(n=520), fetched_at=datetime.now(UTC), currency="USD")

    monkeypatch.setattr(tools, "fetch_history", fake_fetch)
    tools.clear_cache()
    out, _ = _run("get_stock_snapshot", {"symbol": "SHEL.L"})  # a London listing quoted in dollars
    assert out["currency"] == "USD"


def test_an_index_is_described_as_points_not_money():
    snap, err = _run("get_stock_snapshot", {"symbol": "^DJI"})
    assert not err
    assert snap["kind"] == "index"
    assert snap["currency"] is None
    assert snap["index_name"] == "Dow Jones Industrial Average"
    assert "points" in snap["note"]
    trend, _ = _run("get_price_trend", {"symbol": "^N225"})
    assert trend["currency"] is None and trend["market"] == "Tokyo"
    assert "kind" not in _run("get_stock_snapshot", {"symbol": "NVDA"})[0]


def test_chart_results_carry_currency_for_the_axis_and_the_summary():
    out, err = _run("get_price_chart", {"symbols": ["7203.T", "^DJI", "NVDA"], "period": "3mo"})
    assert not err
    assert [s["currency"] for s in out["chart"]["series"]] == ["JPY", None, "USD"]
    assert [s["currency"] for s in out["stocks"]] == ["JPY", None, "USD"]
    assert [s["market"] for s in out["stocks"]] == ["Tokyo", "US", "US"]


def test_the_dollar_key_is_gone_so_a_yen_answer_cannot_say_dollars():
    out, _ = _run("get_price_trend", {"symbol": "7203.T"})
    assert "value_of_100" in out
    assert not any("dollar" in key for key in out)


def test_find_company_returns_the_market_and_includes_indexes(monkeypatch):
    class FakeSearch:
        def __init__(self, query, max_results):
            self.quotes = [
                {"symbol": "TM", "longname": "Toyota Motor Corporation", "exchDisp": "NYSE", "quoteType": "EQUITY"},
                {"symbol": "7203.T", "longname": "Toyota Motor", "exchDisp": "Tokyo", "quoteType": "EQUITY"},
                {"symbol": "^DJI", "shortname": "Dow Jones", "exchDisp": "DJI", "quoteType": "INDEX"},
                {"symbol": "TM250117C", "quoteType": "OPTION"},
            ]

    monkeypatch.setattr(tools.yf, "Search", FakeSearch)
    out, err = _run("find_company", {"query": "Toyota"})
    assert not err
    assert [(m["symbol"], m["market"]) for m in out["matches"]] == [("TM", "US"), ("7203.T", "Tokyo"), ("^DJI", "US")]


# ---- too little history (some BSE tickers, new listings) ----


@pytest.fixture
def short_history(monkeypatch):
    def fake_fetch(symbol, lookback_years=2):
        n = {"TINY.BO": 3, "RECENT": 100}[symbol]
        return PriceHistory(symbol=symbol, df=_synthetic_df(n=n), fetched_at=datetime.now(UTC))

    tools.clear_cache()
    monkeypatch.setattr(tools, "fetch_history", fake_fetch)


def test_a_snapshot_with_too_little_history_is_a_plain_error_not_a_crash(short_history):
    out, err = _run("get_stock_snapshot", {"symbol": "TINY.BO"})
    assert err
    assert "only 3 trading days" in out["error"]
    assert "TINY.BO" in out["error"]


def test_trend_following_needs_more_history_than_dip_buying(short_history):
    out, err = _run("run_backtest", {"symbol": "RECENT", "strategy": "trend_following"})
    assert err and "only 100 trading days" in out["error"]
    out, err = _run("run_backtest", {"symbol": "RECENT", "strategy": "dip_buying"})
    assert not err, out


def test_a_trend_and_a_chart_still_work_on_a_short_history(short_history):
    trend, err = _run("get_price_trend", {"symbol": "TINY.BO"})
    assert not err and trend["start_date"] <= trend["end_date"]
    chart, err = _run("get_price_chart", {"symbols": ["TINY.BO"]})
    assert not err and len(chart["chart"]["series"][0]["dates"]) == 3
