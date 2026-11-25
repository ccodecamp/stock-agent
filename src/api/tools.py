"""Tools Claude can call from the chat endpoint.

Each tool is a thin wrapper over the existing src/ modules. All math stays in
Python: Claude only ever sees computed numbers, never raw daily prices.

Every tool input is validated with pydantic before it runs. Inputs come from
the model, so treat them as untrusted.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Annotated, Literal

import pandas as pd
import yfinance as yf
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from .. import markets
from ..backtest import backtest_golden_cross, backtest_rsi_oversold
from ..data_fetcher import PriceHistory, fetch_history
from ..indicators import compute_indicators

logger = logging.getLogger(__name__)

# Yahoo Finance ticker: an optional leading ^ for an index (^DJI), then letters, digits, dot and dash.
# Covers NVDA, BRK-B, 7203.T (Tokyo), 0700.HK, 600519.SS, RELIANCE.NS, BAJAJ-AUTO.NS and so on.
SYMBOL_PATTERN = r"^\^?[A-Z0-9][A-Z0-9.\-]{0,15}$"

_CACHE_TTL_SECONDS = 15 * 60
_LOOKBACK_YEARS = 2


# --------------------------------------------------------------------------
# Price cache: yfinance rate-limits aggressive callers, and a chat about one
# stock often calls 2-3 tools on the same symbol.
# --------------------------------------------------------------------------

_cache: dict[str, tuple[float, PriceHistory]] = {}
_cache_lock = threading.Lock()


def _get_history(symbol: str) -> PriceHistory:
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(symbol)
        if hit and now - hit[0] < _CACHE_TTL_SECONDS:
            return hit[1]
    history = fetch_history(symbol, lookback_years=_LOOKBACK_YEARS)
    with _cache_lock:
        _cache[symbol] = (now, history)
    return history


def _need_history(history: PriceHistory, rows: int, what: str) -> None:
    """Refuse, in plain words, when the data source has too few past prices for the maths.

    Seen with some Indian BSE tickers (RELIANCE.BO has 3 days of data on Yahoo while TCS.BO has two
    years), and it applies to any recent listing. Left alone, the indicator code fails with an index error.
    """
    have = len(history.df)
    if have < rows:
        raise ValueError(
            f"{history.symbol} has only {have} trading days of price history available, "
            f"which is too few for {what}. The data source may be missing most of its past prices."
        )


def _market_fields(history: PriceHistory) -> dict:
    """Where the stock trades and what its prices are in, so Claude never has to guess."""
    market = markets.lookup(history.symbol)
    if market.is_index:
        return {
            "market": market.name,
            "currency": None,
            "kind": "index",
            "index_name": market.index_name,
            "note": "An index level is in points, not money.",
        }
    return {"market": market.name, "currency": history.currency or market.currency}


def symbol_exists(symbol: str) -> bool:
    """True if the data source has prices for this ticker. Used before saving a favourite.

    Raises (rather than answering False) when the data source itself fails, so a network
    blip is reported as "try again" instead of "that stock doesn't exist".
    """
    try:
        _get_history(symbol)
    except ValueError:  # fetch_history's "no price data returned"
        return False
    return True


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


# --------------------------------------------------------------------------
# Input models
# --------------------------------------------------------------------------


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FindCompanyInput(_Strict):
    query: str = Field(min_length=1, max_length=80)


class SymbolInput(_Strict):
    symbol: str = Field(pattern=SYMBOL_PATTERN)


class PriceTrendInput(SymbolInput):
    period: Literal["6mo", "1y", "2y"] = "1y"


class BacktestInput(SymbolInput):
    strategy: Literal["dip_buying", "trend_following"]


MAX_CHART_SYMBOLS = 5


class ChartInput(_Strict):
    symbols: list[Annotated[str, StringConstraints(pattern=SYMBOL_PATTERN)]] = Field(
        min_length=1, max_length=MAX_CHART_SYMBOLS
    )
    period: Literal["3mo", "6mo", "1y"] = "6mo"

    @field_validator("symbols")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))  # keep order, drop repeats


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------


def find_company(inp: FindCompanyInput) -> dict:
    quotes = yf.Search(inp.query, max_results=8).quotes
    matches = [
        {
            "symbol": q.get("symbol"),
            "name": q.get("longname") or q.get("shortname"),
            "exchange": q.get("exchDisp") or q.get("exchange"),
            "market": markets.lookup(q["symbol"]).name,
            "type": q.get("quoteType"),
        }
        for q in quotes
        if q.get("quoteType") in {"EQUITY", "ETF", "INDEX"} and q.get("symbol")
    ]
    return {"query": inp.query, "matches": matches[:5]}


def get_stock_snapshot(inp: SymbolInput) -> dict:
    history = _get_history(inp.symbol)
    _need_history(history, _SNAPSHOT_MIN_DAYS, "a snapshot")
    return {**compute_indicators(inp.symbol, history.df).to_dict(), **_market_fields(history)}


_SNAPSHOT_MIN_DAYS = 200  # the longest average in the snapshot looks back 200 trading days
_BACKTEST_MIN_DAYS = {"dip_buying": 60, "trend_following": 250}
_PERIOD_DAYS = {"6mo": 126, "1y": 252, "2y": 504}


def get_price_trend(inp: PriceTrendInput) -> dict:
    history = _get_history(inp.symbol)
    df = history.df.tail(_PERIOD_DAYS[inp.period])
    close = df["Close"]
    start, end = float(close.iloc[0]), float(close.iloc[-1])
    monthly = close.resample("ME").last()
    return {
        "symbol": inp.symbol,
        "period": inp.period,
        "start_date": df.index[0].strftime("%Y-%m-%d"),
        "end_date": df.index[-1].strftime("%Y-%m-%d"),
        "start_price": round(start, 2),
        "end_price": round(end, 2),
        "pct_change": round((end / start - 1) * 100, 2),
        "value_of_100": round(100 * end / start, 2),  # what 100 of the stock's own currency became
        "monthly_closes": [{"month": d.strftime("%Y-%m"), "close": round(float(v), 2)} for d, v in monthly.items()],
        **_market_fields(history),
    }


_CHART_DAYS = {"3mo": 63, "6mo": 126, "1y": 252}


def _candle_prices(window: pd.DataFrame) -> dict:
    """Open, high and low for each day, so the page can draw candles.

    Returns nothing if any of them is missing (some indexes have gaps), and the page then
    shows the line chart only.
    """
    columns = ("Open", "High", "Low")
    if not set(columns) <= set(window.columns) or window[list(columns)].isna().any().any():
        return {}
    return {name.lower() + "s": [round(float(v), 2) for v in window[name]] for name in columns}


def get_price_chart(inp: ChartInput) -> dict:
    """Daily closes for the page to draw, plus a short summary for Claude.

    The `chart` key is for the page only: chat.py strips it before Claude sees the result,
    so the model never has to read (or pay for) hundreds of raw prices.
    """
    stocks, series = [], []
    for symbol in inp.symbols:
        history = _get_history(symbol)
        fields = _market_fields(history)
        window = history.df.tail(_CHART_DAYS[inp.period])
        close = window["Close"]
        start, end = float(close.iloc[0]), float(close.iloc[-1])
        stocks.append(
            {
                "symbol": symbol,
                "market": fields["market"],
                "currency": fields["currency"],
                "start_date": close.index[0].strftime("%Y-%m-%d"),
                "end_date": close.index[-1].strftime("%Y-%m-%d"),
                "start_price": round(start, 2),
                "end_price": round(end, 2),
                "pct_change": round((end / start - 1) * 100, 2),
                "high_price": round(float(close.max()), 2),
                "high_date": close.idxmax().strftime("%Y-%m-%d"),
                "low_price": round(float(close.min()), 2),
                "low_date": close.idxmin().strftime("%Y-%m-%d"),
            }
        )
        series.append(
            {
                "symbol": symbol,
                "currency": fields["currency"],  # the page uses it to label the price axis
                "dates": [d.strftime("%Y-%m-%d") for d in close.index],
                "closes": [round(float(v), 2) for v in close],
                # Candles are only drawn for a single stock, so only then do we send the extra prices.
                **(_candle_prices(window) if len(inp.symbols) == 1 else {}),
            }
        )
    return {
        "period": inp.period,
        "chart_shown_to_user": True,
        "stocks": stocks,
        "chart": {"period": inp.period, "series": series},
    }


def run_backtest(inp: BacktestInput) -> dict:
    history = _get_history(inp.symbol)
    _need_history(history, _BACKTEST_MIN_DAYS[inp.strategy], "a strategy test")
    if inp.strategy == "dip_buying":
        result = backtest_rsi_oversold(history.df, inp.symbol)
    else:
        result = backtest_golden_cross(history.df, inp.symbol)
    return {
        **result.__dict__,
        "strategy": inp.strategy,
        "years_of_history": _LOOKBACK_YEARS,
        **_market_fields(history),
    }


# --------------------------------------------------------------------------
# Tool definitions sent to Claude
# --------------------------------------------------------------------------

_SYMBOL_PROP = {
    "type": "string",
    "description": "Yahoo Finance ticker in capitals, with the exchange ending outside the US, e.g. NVDA, 7203.T, ^DJI",
}

TOOLS: list[dict] = [
    {
        "name": "find_company",
        "description": (
            "Look up a stock or market index ticker from its name, brand or a partial ticker. Each match says "
            "which market it trades in. Use this whenever the user names a company rather than giving an "
            "exact ticker, and when the same name could be listed in more than one place."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "Company or brand name, e.g. 'Nvidia'"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_stock_snapshot",
        "description": (
            "Get where a stock or index stands today versus its own history: latest price, how it compares to its "
            "typical price over the last 50 and 200 trading days, recent buying pressure, how much it swings "
            "day to day, and its 52-week high and low. Use for 'how is X doing' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": _SYMBOL_PROP},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_price_trend",
        "description": (
            "Get a stock's price path over a period as monthly closing prices, the total percentage change, "
            "and what 100 (of the stock's own currency) invested at the start would be worth now. Use for "
            "'how has X done this year' "
            "or 'what if I had invested' questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": _SYMBOL_PROP,
                "period": {"type": "string", "enum": ["6mo", "1y", "2y"], "description": "Look-back period"},
            },
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_price_chart",
        "description": (
            "Draw a price chart for the user: one stock, or up to 5 stocks side by side, over the last 3 "
            "months, 6 months or 1 year. The chart appears on the user's screen by itself; you get back only "
            "each stock's start, end, high and low with dates. Use when the user asks to see, show, plot, "
            "graph or compare prices over time."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbols": {
                    "type": "array",
                    "items": _SYMBOL_PROP,
                    "minItems": 1,
                    "maxItems": MAX_CHART_SYMBOLS,
                    "description": "Yahoo Finance tickers in capitals, e.g. ['AAPL', 'MSFT'] or ['^DJI', '^N225']",
                },
                "period": {"type": "string", "enum": ["3mo", "6mo", "1y"], "description": "Look-back period"},
            },
            "required": ["symbols"],
            "additionalProperties": False,
        },
    },
    {
        "name": "run_backtest",
        "description": (
            "Test how a simple, well-known trading rule would have worked on this stock over the last two "
            "years, compared with just buying and holding. 'dip_buying' buys after a sharp sell-off; "
            "'trend_following' buys when the short-term trend moves above the long-term trend. Each trade "
            "is held 30 trading days. Use when the user asks whether a strategy or timing approach works."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "symbol": _SYMBOL_PROP,
                "strategy": {"type": "string", "enum": ["dip_buying", "trend_following"]},
            },
            "required": ["symbol", "strategy"],
            "additionalProperties": False,
        },
    },
]

_HANDLERS: dict[str, tuple[type[BaseModel], Callable]] = {
    "find_company": (FindCompanyInput, find_company),
    "get_stock_snapshot": (SymbolInput, get_stock_snapshot),
    "get_price_trend": (PriceTrendInput, get_price_trend),
    "get_price_chart": (ChartInput, get_price_chart),
    "run_backtest": (BacktestInput, run_backtest),
}


def execute_tool(name: str, raw_input: object) -> tuple[str, bool]:
    """Run one tool call. Returns (JSON string content, is_error).

    Never raises: any failure becomes an error result Claude can read and
    recover from (e.g. by asking the user to check the ticker).
    """
    entry = _HANDLERS.get(name)
    if entry is None:
        return json.dumps({"error": f"Unknown tool: {name}"}), True
    model, handler = entry
    try:
        inp = model.model_validate(raw_input)
    except ValidationError as e:
        return json.dumps({"error": "Invalid input", "details": e.errors(include_url=False)}, default=str), True
    try:
        return json.dumps(handler(inp), default=str), False
    except ValueError as e:  # fetch_history raises ValueError for unknown tickers
        return json.dumps({"error": str(e)}), True
    except Exception:  # noqa: BLE001 — network / yfinance failures must not crash the chat
        logger.exception("Tool %s failed", name)
        return json.dumps({"error": "Data source failed; try again shortly."}), True
