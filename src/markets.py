"""Which market a ticker belongs to and what its prices are measured in.

Offline and deterministic: the market comes from the ticker's ending (7203.T is Tokyo), the same way
Yahoo Finance names them. Claude is told the market and currency in every tool result, so it never has
to guess whether a price is in dollars, yen or rupees.
"""

from __future__ import annotations

from dataclasses import dataclass

# ending -> (market name, currency). Endings are Yahoo Finance's. US tickers have none.
SUFFIXES: dict[str, tuple[str, str]] = {
    ".T": ("Tokyo", "JPY"),
    ".HK": ("Hong Kong", "HKD"),
    ".SS": ("Shanghai", "CNY"),
    ".SZ": ("Shenzhen", "CNY"),
    ".NS": ("India (NSE)", "INR"),
    ".BO": ("India (BSE)", "INR"),
    ".AX": ("Australia (ASX)", "AUD"),
    ".L": ("London", "GBP"),
    ".DE": ("Germany (Xetra)", "EUR"),
    ".PA": ("Paris", "EUR"),
    ".MI": ("Milan", "EUR"),
    ".SI": ("Singapore", "SGD"),
    ".BK": ("Thailand", "THB"),
    ".JK": ("Indonesia", "IDR"),
    ".KL": ("Malaysia", "MYR"),
}

# Market indexes are measured in points, not money. ticker -> (name, market).
INDEXES: dict[str, tuple[str, str]] = {
    "^DJI": ("Dow Jones Industrial Average", "US"),
    "^GSPC": ("S&P 500", "US"),
    "^IXIC": ("Nasdaq Composite", "US"),
    "^N225": ("Nikkei 225", "Tokyo"),
    "^HSI": ("Hang Seng Index", "Hong Kong"),
    "000001.SS": ("Shanghai Composite", "Shanghai"),
    "^NSEI": ("Nifty 50", "India (NSE)"),
    "^BSESN": ("BSE Sensex", "India (BSE)"),
    "^AXJO": ("S&P/ASX 200", "Australia (ASX)"),
    "^FTSE": ("FTSE 100", "London"),
    "^GDAXI": ("DAX", "Germany (Xetra)"),
    "^FCHI": ("CAC 40", "Paris"),
    "FTSEMIB.MI": ("FTSE MIB", "Milan"),
    "^STI": ("Straits Times Index", "Singapore"),
    "^SET.BK": ("SET Index", "Thailand"),
    "^JKSE": ("Jakarta Composite", "Indonesia"),
    "^KLSE": ("FTSE Bursa Malaysia KLCI", "Malaysia"),
}

# Some exchanges quote in a hundredth of the main unit (London in pence). Yahoo's code -> (main currency, divisor).
MINOR_UNITS: dict[str, tuple[str, int]] = {
    "GBp": ("GBP", 100),
    "ZAc": ("ZAR", 100),
    "ILA": ("ILS", 100),
}


@dataclass(frozen=True)
class Market:
    name: str
    currency: str | None  # None for an index: its level is in points, not money
    is_index: bool = False
    index_name: str | None = None


def to_main_unit(code: str | None) -> tuple[str | None, int]:
    """('GBp') -> ('GBP', 100): the main currency and how many of the quoted units make one."""
    if code in MINOR_UNITS:
        return MINOR_UNITS[code]
    return code, 1


def lookup(symbol: str) -> Market:
    sym = symbol.upper()
    if sym in INDEXES:
        name, market = INDEXES[sym]
        return Market(market, None, True, name)
    if sym.startswith("^"):
        return Market("unknown", None, True, sym)
    for ending, (name, currency) in SUFFIXES.items():
        if sym.endswith(ending):
            return Market(name, currency)
    # Anything else with a long ending (.TO, .KS) is a market we have not set up. A one-letter ending
    # (BRK.B) is just a US share class.
    _, dot, ending = sym.rpartition(".")
    if dot and len(ending) > 1:
        return Market("unsupported", None)
    return Market("US", "USD")
