"""Frozen tool data for evals.

The real Claude runs against these fake tools. Because the data never changes,
we know exactly which numbers a correct answer may quote.

Values are invented but realistic. They are NOT current market data.
The shapes match what the real tools return (src/api/tools.py), including the market and
currency on every result; a test compares the two so they cannot drift apart.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from src import markets

# The last day of data for US and European stocks. A high or low can never be dated after a stock's own end date.
END_DATE = "2026-11-10"
# Asia-Pacific markets close hours before Europe and the US, so on the same morning they are a day ahead.
ASIA_END_DATE = "2026-11-11"

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
    # ---- other markets: invented numbers, in each stock's own currency (see _market_fields) ----
    "7203.T": (  # Tokyo, yen
        "Toyota Motor Corporation",
        [3050.0, 3120.0, 3010.0, 2950.0, 3080.0, 3190.0, 3260.0, 3310.0, 3380.0, 3350.0, 3440.0, 3414.0],
        3302.5,
        3185.0,
        57.0,
        3875.0,
        2641.0,
        2.8,
    ),
    "TM": (  # Toyota's US-listed shares (an ADR), dollars
        "Toyota Motor Corporation",
        [185.0, 189.2, 183.4, 180.1, 188.7, 197.5, 203.4, 209.8, 214.0, 212.6, 220.3, 223.45],
        214.0,
        205.0,
        58.0,
        231.2,
        168.6,
        1.9,
    ),
    "0700.HK": (  # Hong Kong dollars
        "Tencent Holdings Limited",
        [520.0, 548.2, 571.5, 560.4, 538.9, 492.5, 470.3, 455.8, 440.1, 412.6, 389.4, 376.0],
        410.2,
        478.9,
        33.0,
        654.0,
        362.4,
        2.9,
    ),
    "SHEL.L": (  # London, pounds (the real tool converts Yahoo's pence to pounds)
        "Shell plc",
        [29.4, 30.1, 30.8, 31.2, 32.0, 33.5, 34.9, 36.1, 38.4, 39.8, 38.9, 36.85],
        37.9,
        34.2,
        47.0,
        41.1,
        27.9,
        1.9,
    ),
    "RELIANCE.NS": (  # India NSE, rupees
        "Reliance Industries Limited",
        [1285.0, 1310.4, 1342.8, 1398.5, 1452.0, 1510.9, 1560.3, 1604.4, 1575.2, 1498.6, 1421.7, 1368.7],
        1458.3,
        1395.1,
        38.0,
        1604.4,
        1152.0,
        2.0,
    ),
    "BHP.AX": (  # Australia, Australian dollars
        "BHP Group Limited",
        [46.2, 47.5, 49.0, 50.3, 52.1, 55.4, 58.9, 61.2, 60.1, 57.3, 55.8, 54.38],
        57.2,
        55.0,
        42.0,
        67.3,
        44.9,
        1.8,
    ),
    "SAP.DE": (  # Germany (Xetra), euros
        "SAP SE",
        [248.0, 241.5, 236.2, 229.8, 222.4, 218.1, 226.9, 233.0, 227.4, 216.3, 212.5, 209.65],
        220.3,
        232.7,
        36.0,
        273.4,
        205.8,
        1.7,
    ),
    "ENEL.MI": (  # Milan, euros
        "Enel SpA",
        [6.52, 6.61, 6.70, 6.82, 6.95, 7.04, 7.10, 7.21, 7.33, 7.28, 7.36, 7.42],
        7.27,
        7.04,
        61.0,
        7.5,
        6.31,
        1.1,
    ),
    "D05.SI": (  # Singapore, Singapore dollars
        "DBS Group Holdings Ltd",
        [41.2, 42.0, 43.1, 44.6, 45.9, 47.3, 46.1, 44.8, 43.2, 41.9, 40.5, 39.7],
        42.0,
        43.9,
        39.0,
        48.1,
        38.9,
        1.3,
    ),
    "PTT.BK": (  # Thailand, baht
        "PTT Public Company Limited",
        [34.5, 35.0, 35.75, 36.5, 37.25, 38.0, 38.5, 38.25, 38.0, 38.75, 38.5, 38.7],
        38.3,
        36.9,
        54.0,
        39.5,
        33.25,
        1.5,
    ),
    "SHOP": (  # Shopify's US listing, dollars
        "Shopify Inc.",
        [118.0, 122.5, 128.3, 135.0, 140.2, 151.9, 163.4, 171.2, 176.8, 168.9, 155.3, 147.81],
        153.0,
        130.0,
        41.0,
        180.88,
        94.0,
        3.1,
    ),
    "SHOP.TO": (  # Shopify in Toronto: a market the app does not cover, though the data tool would still answer
        "Shopify Inc.",
        [165.2, 171.5, 179.6, 189.0, 196.3, 212.7, 228.8, 239.7, 247.5, 236.5, 217.4, 206.93],
        214.2,
        182.0,
        41.0,
        253.2,
        131.6,
        3.1,
    ),
    # ---- indexes: levels are in points, not money ----
    "^DJI": (
        "Dow Jones Industrial Average",
        [44100.0, 45200.0, 44650.0, 46100.0, 47300.0, 48400.0, 49900.0, 51200.0, 52800.0, 54100.0, 53200.0, 51608.17],
        52300.0,
        50100.0,
        41.0,
        54744.33,
        43905.0,
        1.3,
    ),
    "^N225": (
        "Nikkei 225",
        [40100.0, 41500.0, 42300.0, 43800.0, 45200.0, 47600.0, 49100.0, 50900.0, 53400.0, 56200.0, 57800.0, 58956.67],
        56400.0,
        51200.0,
        66.0,
        59800.0,
        38900.0,
        1.6,
    ),
}

# Words in a search -> the tickers a real search would return, best match first.
# "toyota" has two listings and "shopify" has one the app does not cover, as on the real Yahoo search.
_NAME_TO_SYMBOLS = {
    "nvidia": ["NVDA"], "apple": ["AAPL"], "microsoft": ["MSFT"], "tesla": ["TSLA"], "nike": ["NKE"],
    "toyota": ["TM", "7203.T"], "tencent": ["0700.HK"], "shell": ["SHEL.L"], "reliance": ["RELIANCE.NS"],
    "bhp": ["BHP.AX"], "sap": ["SAP.DE"], "enel": ["ENEL.MI"], "dbs": ["D05.SI"], "ptt": ["PTT.BK"],
    "shopify": ["SHOP", "SHOP.TO"], "dow": ["^DJI"], "nikkei": ["^N225"],
}  # fmt: skip

# Where an end date differs from END_DATE.
_END_DATES = {s: ASIA_END_DATE for s in ("7203.T", "0700.HK", "RELIANCE.NS", "BHP.AX", "D05.SI", "PTT.BK", "^N225")}
# The currency the data source reports for a market we do not support, which the market table cannot know.
_CURRENCY_OVERRIDES = {"SHOP.TO": "CAD"}
# The exchange shown in find_company results (the older US stocks all say NASDAQ).
_EXCHANGES = {
    "TM": "NYSE", "7203.T": "Tokyo", "0700.HK": "Hong Kong", "SHEL.L": "London", "RELIANCE.NS": "NSE",
    "BHP.AX": "ASX", "SAP.DE": "XETRA", "ENEL.MI": "Milan", "D05.SI": "Singapore", "PTT.BK": "Thailand",
    "SHOP.TO": "Toronto", "^DJI": "DJI", "^N225": "Osaka",
}  # fmt: skip

_BACKTESTS = {  # symbol -> (n_signals, avg_30d_fwd, win_rate, buy_hold, rule)
    "TSLA": (4, 3.8, 50.0, 12.4, 5.1),
    "NVDA": (3, 6.2, 66.7, -4.9, 8.3),
}


def _pct(a: float, b: float) -> float:
    return round((a / b - 1) * 100, 2)


def _end_date(symbol: str) -> str:
    return _END_DATES.get(symbol, END_DATE)


def _market_fields(symbol: str) -> dict:
    """The market and currency fields the real tools add to every result (src/api/tools.py)."""
    market = markets.lookup(symbol)
    if market.is_index:
        return {
            "market": market.name,
            "currency": None,
            "kind": "index",
            "index_name": market.index_name,
            "note": "An index level is in points, not money.",
        }
    return {"market": market.name, "currency": _CURRENCY_OVERRIDES.get(symbol, market.currency)}


def _snapshot(symbol: str) -> dict:
    _, closes, sma50, sma200, rsi, high, low, atr_pct = _STOCKS[symbol]
    close = closes[-1]
    return {
        "symbol": symbol,
        "as_of": _end_date(symbol),
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
        **_market_fields(symbol),
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
        "end_date": _end_date(symbol),
        "start_price": start,
        "end_price": end,
        "pct_change": _pct(end, start),
        "value_of_100": round(100 * end / start, 2),
        "monthly_closes": [{"month": m, "close": c} for m, c in zip(labels, closes, strict=True)],
        **_market_fields(symbol),
    }


def _chart(symbols: object, period: str) -> tuple[dict, bool]:
    """What Claude gets back from get_price_chart: summaries only. The real tool also sends the page a
    `chart` series, which chat.py strips before the model sees it, so it is not part of the fixture."""
    if not isinstance(symbols, list) or not 1 <= len(symbols) <= 5 or period not in {"3mo", "6mo", "1y"}:
        return {"error": "Invalid input"}, True
    unknown = [s for s in symbols if s not in _STOCKS]
    if unknown:
        return {"error": f"No price data returned for {unknown[0]}"}, True
    months = {"3mo": 3, "6mo": 6, "1y": 12}[period]
    stocks = []
    for symbol in dict.fromkeys(symbols):
        closes, labels = _STOCKS[symbol][1][-months:], _MONTHS[-months:]
        hi, lo = max(range(months), key=closes.__getitem__), min(range(months), key=closes.__getitem__)
        end = _end_date(symbol)
        fields = _market_fields(symbol)
        stocks.append(
            {
                "symbol": symbol,
                "market": fields["market"],
                "currency": fields["currency"],
                "start_date": f"{labels[0]}-01",
                "end_date": end,
                "start_price": closes[0],
                "end_price": closes[-1],
                "pct_change": _pct(closes[-1], closes[0]),
                "high_price": closes[hi],
                "high_date": min(f"{labels[hi]}-15", end),
                "low_price": closes[lo],
                "low_date": min(f"{labels[lo]}-15", end),
            }
        )
    return {"period": period, "chart_shown_to_user": True, "stocks": stocks}, False


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
        **_market_fields(symbol),
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
            symbols = [s for word, found in _NAME_TO_SYMBOLS.items() if word in query for s in found]
            symbols += [s for s in _STOCKS if s.lower() == query]
            matches = [
                {
                    "symbol": s,
                    "name": _STOCKS[s][0],
                    "exchange": _EXCHANGES.get(s, "NASDAQ"),
                    "market": markets.lookup(s).name,
                    "type": "INDEX" if s.startswith("^") else "EQUITY",
                }
                for s in dict.fromkeys(symbols)
            ]
            return json.dumps({"query": args.get("query"), "matches": matches[:5]}), False
        if name == "get_price_chart":
            result, is_error = _chart(args.get("symbols"), args.get("period", "6mo"))
            return json.dumps(result), is_error
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
