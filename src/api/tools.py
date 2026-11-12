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

import yfinance as yf
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator

from ..backtest import backtest_golden_cross, backtest_rsi_oversold
from ..data_fetcher import PriceHistory, fetch_history
from ..indicators import compute_indicators

logger = logging.getLogger(__name__)

# US-style ticker: starts with a letter, then letters/digits/dot/dash (BRK.B, BF-B).
SYMBOL_PATTERN = r"^[A-Z][A-Z0-9.\-]{0,9}$"

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
            "type": q.get("quoteType"),
        }
        for q in quotes
        if q.get("quoteType") in {"EQUITY", "ETF"} and q.get("symbol")
    ]
    return {"query": inp.query, "matches": matches[:5]}


def get_stock_snapshot(inp: SymbolInput) -> dict:
    history = _get_history(inp.symbol)
    return compute_indicators(inp.symbol, history.df).to_dict()


_PERIOD_DAYS = {"6mo": 126, "1y": 252, "2y": 504}


def get_price_trend(inp: PriceTrendInput) -> dict:
    df = _get_history(inp.symbol).df.tail(_PERIOD_DAYS[inp.period])
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
        "value_of_100_dollars": round(100 * end / start, 2),
        "monthly_closes": [{"month": d.strftime("%Y-%m"), "close": round(float(v), 2)} for d, v in monthly.items()],
    }


_CHART_DAYS = {"3mo": 63, "6mo": 126, "1y": 252}


def get_price_chart(inp: ChartInput) -> dict:
    """Daily closes for the page to draw, plus a short summary for Claude.

    The `chart` key is for the page only: chat.py strips it before Claude sees the result,
    so the model never has to read (or pay for) hundreds of raw prices.
    """
    stocks, series = [], []
    for symbol in inp.symbols:
        close = _get_history(symbol).df["Close"].tail(_CHART_DAYS[inp.period])
        start, end = float(close.iloc[0]), float(close.iloc[-1])
        stocks.append(
            {
                "symbol": symbol,
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
                "dates": [d.strftime("%Y-%m-%d") for d in close.index],
                "closes": [round(float(v), 2) for v in close],
            }
        )
    return {
        "period": inp.period,
        "chart_shown_to_user": True,
        "stocks": stocks,
        "chart": {"period": inp.period, "series": series},
    }


def run_backtest(inp: BacktestInput) -> dict:
    df = _get_history(inp.symbol).df
    if inp.strategy == "dip_buying":
        result = backtest_rsi_oversold(df, inp.symbol)
    else:
        result = backtest_golden_cross(df, inp.symbol)
    return {**result.__dict__, "strategy": inp.strategy, "years_of_history": _LOOKBACK_YEARS}


# --------------------------------------------------------------------------
# Tool definitions sent to Claude
# --------------------------------------------------------------------------

_SYMBOL_PROP = {"type": "string", "description": "US stock ticker in capitals, e.g. NVDA"}

TOOLS: list[dict] = [
    {
        "name": "find_company",
        "description": (
            "Look up a company's stock ticker from its name, brand or a partial ticker. "
            "Use this whenever the user names a company rather than giving an exact ticker."
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
            "Get where a stock stands today versus its own history: latest price, how it compares to its "
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
            "and what $100 invested at the start would be worth now. Use for 'how has X done this year' "
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
                    "description": "US stock tickers in capitals, e.g. ['AAPL', 'MSFT']",
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
