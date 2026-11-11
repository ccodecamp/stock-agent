"""Frozen tool data for evals.

The real Claude runs against these fake tools. Because the data never changes,
we know exactly which numbers a correct answer may quote.

Values are invented but realistic. They are NOT current market data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

_MONTHS = [
    "2025-12",
    "2026-01",
    "2026-02",
    "2026-03",
    "2026-04",
    "2026-05",
    "2026-06",
    "2026-07",
    "2026-08",
    "2026-09",
    "2026-10",
    "2026-11",
]

# symbol -> (name, 12 monthly closes oldest->newest, sma50, sma200, rsi, high52, low52, atr_pct)
_STOCKS = {
    "NVDA": (
        "NVIDIA Corporation",
        [188.0, 190.5, 175.2, 182.4, 195.0, 205.3, 212.8, 220.1, 226.9, 228.4, 201.7, 178.67],
        217.4,
        205.2,
        31.0,
        243.37,
        163.9,
        4.7,
    ),
    "AAPL": (
        "Apple Inc.",
        [212.0, 215.4, 220.9, 224.1, 219.5, 228.0, 231.2, 236.7, 241.0, 244.3, 247.9, 251.4],
        243.1,
        232.6,
        58.0,
        253.8,
        196.4,
        1.6,
    ),
    "MSFT": (
        "Microsoft Corporation",
        [430.0, 441.2, 436.8, 450.3, 462.0, 458.4, 470.9, 481.5, 476.2, 489.0, 472.3, 466.8],
        478.5,
        468.0,
        44.0,
        497.6,
        401.3,
        1.4,
    ),
    "TSLA": (
        "Tesla, Inc.",
        [352.0, 338.4, 310.9, 295.2, 322.7, 348.0, 371.5, 389.3, 402.1, 385.6, 341.2, 327.9],
        361.8,
        349.4,
        40.0,
        414.5,
        268.7,
        3.9,
    ),
    "NKE": (
        "NIKE, Inc.",
        [72.4, 70.1, 68.8, 71.5, 74.9, 77.2, 75.6, 73.1, 70.8, 69.4, 71.9, 74.3],
        72.0,
        73.5,
        55.0,
        82.4,
        65.2,
        2.1,
    ),
}

_NAME_TO_SYMBOL = {"nvidia": "NVDA", "apple": "AAPL", "microsoft": "MSFT", "tesla": "TSLA", "nike": "NKE"}
_BACKTESTS = {  # symbol -> (n_signals, avg_30d_fwd, win_rate, buy_hold, rule)
    "TSLA": (4, 3.8, 50.0, 12.4, 5.1),
    "NVDA": (3, 6.2, 66.7, -4.9, 8.3),
}


def _pct(a: float, b: float) -> float:
    return round((a / b - 1) * 100, 2)


def _snapshot(symbol: str) -> dict:
    _, closes, sma50, sma200, rsi, high, low, atr_pct = _STOCKS[symbol]
    close = closes[-1]
    return {
        "symbol": symbol,
        "as_of": "2026-11-10",
        "close": close,
        "sma_50": sma50,
        "sma_200": sma200,
        "pct_above_sma_50": _pct(close, sma50),
        "pct_above_sma_200": _pct(close, sma200),
        "rsi_14": rsi,
        "rsi_zone": "oversold" if rsi < 30 else "overbought" if rsi > 70 else "neutral",
        "bb_upper": round(close * 1.08, 2),
        "bb_middle": round(close * 1.02, 2),
        "bb_lower": round(close * 0.96, 2),
        "bb_pct_b": 0.25,
        "atr_14": round(close * atr_pct / 100, 2),
        "atr_pct_of_close": atr_pct,
        "high_52w": high,
        "low_52w": low,
        "pct_off_52w_high": _pct(close, high),
        "pct_above_52w_low": _pct(close, low),
    }


def _trend(symbol: str, period: str) -> dict:
    closes = _STOCKS[symbol][1]
    months = {"6mo": 6, "1y": 12, "2y": 12}[period]
    closes, labels = closes[-months:], _MONTHS[-months:]
    start, end = closes[0], closes[-1]
    return {
        "symbol": symbol,
        "period": period,
        "start_date": f"{labels[0]}-01",
        "end_date": "2026-11-10",
        "start_price": start,
        "end_price": end,
        "pct_change": _pct(end, start),
        "value_of_100_dollars": round(100 * end / start, 2),
        "monthly_closes": [{"month": m, "close": c} for m, c in zip(labels, closes, strict=True)],
    }


def _backtest(symbol: str, strategy: str) -> dict:
    n, avg, win, hold, rule = _BACKTESTS[symbol]
    return {
        "symbol": symbol,
        "rule": "RSI(14) crosses below 30" if strategy == "dip_buying" else "50-day crosses above 200-day",
        "n_signals": n,
        "avg_30d_forward_return_pct": avg,
        "win_rate_30d_pct": win,
        "buy_and_hold_total_return_pct": hold,
        "rule_total_return_pct": rule,
        "strategy": strategy,
        "years_of_history": 2,
    }


@dataclass
class ToolCallRecord:
    name: str
    input: object
    output: str
    is_error: bool


@dataclass
class FixtureTools:
    """Drop-in replacement for execute_tool that serves frozen data and records every call."""

    log: list[ToolCallRecord] = field(default_factory=list)

    def __call__(self, name: str, raw_input: object) -> tuple[str, bool]:
        output, is_error = self._serve(name, raw_input)
        self.log.append(ToolCallRecord(name, raw_input, output, is_error))
        return output, is_error

    def _serve(self, name: str, raw: object) -> tuple[str, bool]:
        args = raw if isinstance(raw, dict) else {}
        if name == "find_company":
            query = str(args.get("query", "")).lower()
            symbol = next((s for n, s in _NAME_TO_SYMBOL.items() if n in query or s.lower() == query), None)
            matches = []
            if symbol:
                matches = [{"symbol": symbol, "name": _STOCKS[symbol][0], "exchange": "NASDAQ", "type": "EQUITY"}]
            return json.dumps({"query": args.get("query"), "matches": matches}), False
        symbol = str(args.get("symbol", ""))
        if name in {"get_stock_snapshot", "get_price_trend", "run_backtest"}:
            if symbol not in _STOCKS:
                return json.dumps({"error": f"No price data returned for {symbol}"}), True
            if name == "get_stock_snapshot":
                return json.dumps(_snapshot(symbol)), False
            if name == "get_price_trend":
                return json.dumps(_trend(symbol, args.get("period", "1y"))), False
            if symbol not in _BACKTESTS:
                return json.dumps({"error": f"No price data returned for {symbol}"}), True
            return json.dumps(_backtest(symbol, args.get("strategy", "dip_buying"))), False
        return json.dumps({"error": f"Unknown tool: {name}"}), True
