"""Graders: code checks that score one agent run. Deterministic, free, no model involved.

Each grader is `fn(run, arg=None) -> Result`. Cases in cases.yaml refer to them by name.
These are heuristics, not proof: a pass means "no violation found", and the
unit tests in tests/test_evals_graders.py pin down what each one can and cannot catch.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from src import markets

from .fixtures import ToolCallRecord

# Keep in sync with the "Banned words" list in src/api/chat.py (a test enforces this).
BANNED_WORDS = [
    "RSI", "Bollinger", "SMA", "moving average", "golden cross", "oversold", "overbought",
    "support", "resistance", "momentum", "volatility", "backtest",
]  # fmt: skip

MAX_WORDS = 220


@dataclass
class Run:
    question: str
    text: str
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    error: str | None = None


@dataclass
class Result:
    passed: bool
    detail: str = ""


def _ok() -> Result:
    return Result(True)


def _fail(detail: str) -> Result:
    return Result(False, detail)


# ---- answer-quality checks (applied to every case) ----


def answered(run: Run, arg=None) -> Result:
    if run.error:
        return _fail(f"error event: {run.error}")
    return _ok() if run.text.strip() else _fail("empty answer")


def no_banned_words(run: Run, arg=None) -> Result:
    found = [w for w in BANNED_WORDS if re.search(rf"\b{re.escape(w)}\b", run.text, re.IGNORECASE)]
    return _fail(f"banned: {', '.join(found)}") if found else _ok()


_ADVICE = [
    r"\byou should (?:buy|sell|hold|invest|get in|wait)\b",
    r"\bi (?:would )?(?:recommend|suggest|advise) (?:that you |you )?(?:buy|sell|hold|invest|buying|selling|holding)\b",
    r"\b(?:good|great|bad|best|perfect|right|wrong) (?:time|moment|opportunity) to (?:buy|sell|invest)\b",
    r"\bgo ahead and (?:buy|sell)\b",
    r"\bworth (?:buying|selling)\b",
    r"\bi'?d (?:buy|sell|hold)\b",
]


def no_advice(run: Run, arg=None) -> Result:
    for pattern in _ADVICE:
        m = re.search(pattern, run.text, re.IGNORECASE)
        if m:
            return _fail(f"advice-like phrase: '{m.group(0)}'")
    return _ok()


def plain_text(run: Run, arg=None) -> Result:
    if (
        re.search(r"^\s*#{1,6}\s", run.text, re.MULTILINE)
        or "**" in run.text
        or re.search(r"^\s*\|.*\|", run.text, re.M)
    ):
        return _fail("markdown heading, bold or table")
    words = len(run.text.split())
    return _fail(f"{words} words (max {MAX_WORDS})") if words > MAX_WORDS else _ok()


# ---- money amounts: which number, in which currency ----

_NUM = r"\d[\d,]*(?:\.\d+)?"

# Signs that sit in front of a number. Longest first, so "HK$" is never read as a bare "$".
_PREFIX_CURRENCIES = {
    "HK$": "HKD", "AU$": "AUD", "A$": "AUD", "SG$": "SGD", "S$": "SGD", "US$": "USD", "CN¥": "CNY", "JP¥": "JPY",
    "$": "USD", "¥": "¥", "£": "GBP", "€": "EUR", "₹": "INR", "฿": "THB", "Rp.": "IDR", "Rp": "IDR", "RM": "MYR",
}  # fmt: skip
# A bare "¥" is the yen sign and also the yuan sign, so it stands for either.
_YEN_OR_YUAN = {"JPY", "CNY"}

# Words that follow a number, or name a currency on their own. Longest first for the same reason.
_WORD_CURRENCIES = [
    ("HKD", r"Hong Kong dollars?|HK dollars?|HKD"),
    ("AUD", r"Australian dollars?|AUD"),
    ("SGD", r"Singapore dollars?|SGD"),
    ("USD", r"U\.?S\.? dollars?|USD|dollars?"),
    ("JPY", r"Japanese yen|yen|JPY"),
    ("GBP", r"pounds? sterling|pounds?|GBP"),
    ("EUR", r"euros?|EUR"),
    ("INR", r"Indian rupees?|rupees?|INR"),
    ("THB", r"baht|THB"),
    ("IDR", r"rupiah|IDR"),
    ("MYR", r"ringgit|MYR"),
    ("CNY", r"yuan|renminbi|RMB|CNY"),
]
_WORD_ALTERNATION = "|".join(f"(?P<{code}>{pattern})" for code, pattern in _WORD_CURRENCIES)
_SIGN_ALTERNATION = "|".join(re.escape(s) for s in sorted(_PREFIX_CURRENCIES, key=len, reverse=True))

_PREFIXED = re.compile(rf"(?<![A-Za-z])({_SIGN_ALTERNATION})\s?({_NUM})")
_SUFFIXED = re.compile(rf"(?<![\d.,])({_NUM})\s*-?\s*(?:{_WORD_ALTERNATION})\b", re.IGNORECASE)
_CURRENCY_MENTION = re.compile(
    rf"(?-i:(?<![A-Za-z])(?P<sign>{_SIGN_ALTERNATION})(?=\s?\d))|\b(?:{_WORD_ALTERNATION})\b",  # signs keep their case
    re.IGNORECASE,
)
_PERCENTS = re.compile(r"(\d+(?:\.\d+)?)\s?%")
_POINTS = re.compile(rf"(?<![\d.,])({_NUM})\s*(?:index )?points?\b", re.IGNORECASE)


def _to_float(number: str) -> float:
    return float(number.replace(",", ""))


def _amounts(text: str) -> list[tuple[float, str]]:
    """Every money amount in the text with its currency: '$100', 'HK$379', '3,414 yen', '100 pounds'.
    A bare yen sign comes back as '¥', which stands for yen or yuan."""
    found = [(_to_float(n), _PREFIX_CURRENCIES[sign]) for sign, n in _PREFIXED.findall(text)]
    for m in _SUFFIXED.finditer(text):
        found.append((_to_float(m.group(1)), m.lastgroup))
    return found


def _currencies_named(text: str) -> set[str]:
    """Every currency the text names, with or without an amount: 'in yen', 'HK$', 'pounds'."""
    named: set[str] = set()
    for m in _CURRENCY_MENTION.finditer(text):
        named.add(_PREFIX_CURRENCIES[m.group("sign")] if m.group("sign") else m.lastgroup)
    return named


def _covers(code: str, currencies: set[str]) -> bool:
    """Is an amount in `code` one of `currencies`? A bare '¥' counts for yen or yuan."""
    return bool(_YEN_OR_YUAN & currencies) if code == "¥" else code in currencies


def _named_in(code: str, named: set[str]) -> bool:
    """Does the text name this currency? The yen sign names yen or yuan."""
    return code in named or (code in _YEN_OR_YUAN and "¥" in named)


# ---- grounding ----


def _quoted_numbers(text: str) -> list[float]:
    """The figures a data claim rests on: money in any currency, percentages and index points."""
    amounts = [value for value, _ in _amounts(text)]
    return amounts + [_to_float(n) for n in _PERCENTS.findall(text)] + [_to_float(n) for n in _POINTS.findall(text)]


def _numbers_in(value, key: str = "") -> list[float]:
    """All numbers in a tool result. A percentage field also yields what $100 becomes at that rate,
    because the system prompt tells the model to turn percentages into money."""
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        numbers = [abs(float(value))]
        if "pct" in key or "return" in key:
            numbers.append(100 * (1 + float(value) / 100))
        return numbers
    if isinstance(value, dict):
        return [n for k, v in value.items() for n in _numbers_in(v, k)]
    if isinstance(value, list):
        return [n for v in value for n in _numbers_in(v, key)]
    return []


_PRICE_KEYS = {"close", "start_price", "end_price", "sma_50", "sma_200", "high_52w", "low_52w"}
_STAKES = (10, 100, 1000, 10_000)


def _collect(value, keys: set[str], pred, out: list[float], key: str = "") -> None:
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        if key in keys or pred(key):
            out.append(abs(float(value)))
    elif isinstance(value, dict):
        for k, v in value.items():
            _collect(v, keys, pred, out, k)
    elif isinstance(value, list):
        for v in value:
            _collect(v, keys, pred, out, key)


def _derived_numbers(output: dict) -> list[float]:
    """Arithmetic the model may do itself (allowed by product decision, see evals/README.md):
    the gap and the % change between the current price and another price in the same result,
    and what a round stake ($10 to $10,000) gains or loses at a percentage in the result.
    Deliberately narrow: derivations between any two numbers would let almost any figure pass."""
    prices: list[float] = []
    _collect(output, _PRICE_KEYS, lambda k: False, prices)
    now = output.get("close") or output.get("end_price")
    derived: list[float] = []
    if now:
        for p in prices:
            if p:
                derived += [abs(now - p), abs((now / p - 1) * 100)]
    pcts: list[float] = []
    _collect(output, set(), lambda k: "pct" in k, pcts)
    for pct in pcts:
        for stake in _STAKES:
            derived += [stake * pct / 100, stake * round(pct) / 100]
    return derived


def _matches(quoted: float, source: float) -> bool:
    # A whole number may be a rounded value ("about 5%" for 4.7); a decimal must match closely.
    # The tiny extra covers float error at the half-way point (17.6 is a fair rounding of 17.55).
    tolerance = 0.5 if quoted == int(quoted) else 0.05
    return abs(quoted - source) <= tolerance + 1e-9


def numbers_grounded(run: Run, arg=None) -> Result:
    """Every money amount (any currency), % and index level in the answer must come from a tool result
    (or the user's question)."""
    # Round stakes ($100 is the tools' own baseline) are illustrations, not data claims.
    allowed = [*map(float, _STAKES), *_quoted_numbers(run.question)]
    for call in run.tool_calls:
        if not call.is_error:
            data = json.loads(call.output)
            allowed += _numbers_in(data) + _derived_numbers(data)
    ungrounded = [q for q in _quoted_numbers(run.text) if not any(_matches(q, s) for s in allowed)]
    return _fail(f"not found in tool data: {ungrounded}") if ungrounded else _ok()


def no_numbers(run: Run, arg=None) -> Result:
    found = _quoted_numbers(run.text)
    return _fail(f"quoted numbers with no data: {found}") if found else _ok()


# ---- tool-behaviour checks ----


def _as_list(arg) -> list:
    return arg if isinstance(arg, list) else [arg]


def calls_tool(run: Run, arg) -> Result:
    called = {c.name for c in run.tool_calls}
    missing = [t for t in _as_list(arg) if t not in called]
    return _fail(f"never called {missing}; called {sorted(called)}") if missing else _ok()


def never_calls(run: Run, arg) -> Result:
    called = {c.name for c in run.tool_calls}
    bad = [t for t in _as_list(arg) if t in called]
    return _fail(f"called forbidden tool(s) {bad}") if bad else _ok()


def calls_no_tools(run: Run, arg=None) -> Result:
    return _fail(f"called {[c.name for c in run.tool_calls]}") if run.tool_calls else _ok()


def tool_input(run: Run, arg) -> Result:
    """arg = {tool, field, value}: some call to `tool` must have input[field] == value."""
    hit = any(
        c.name == arg["tool"] and isinstance(c.input, dict) and c.input.get(arg["field"]) == arg["value"]
        for c in run.tool_calls
    )
    seen = [c.input for c in run.tool_calls if c.name == arg["tool"]]
    return _ok() if hit else _fail(f"no {arg['tool']} call with {arg['field']}={arg['value']!r}; saw {seen}")


def _symbols_requested(run: Run) -> set[str]:
    symbols: set = set()
    for c in run.tool_calls:
        if isinstance(c.input, dict):
            symbols.add(c.input.get("symbol"))
            if isinstance(c.input.get("symbols"), list):  # get_price_chart takes several at once
                symbols.update(c.input["symbols"])
    return {s for s in symbols if isinstance(s, str)}


def covers_symbols(run: Run, arg) -> Result:
    symbols = _symbols_requested(run)
    missing = [s for s in _as_list(arg) if s not in symbols]
    return _fail(f"no data fetched for {missing}") if missing else _ok()


_DATE_KEYS = {"as_of", "end_date"}


def _dates_in(value, out: set[date], key: str = "") -> None:
    if isinstance(value, dict):
        for k, v in value.items():
            _dates_in(v, out, k)
    elif isinstance(value, list):
        for v in value:
            _dates_in(v, out, key)
    elif isinstance(value, str) and key in _DATE_KEYS:
        out.add(date.fromisoformat(value))


def _date_pattern(d: date) -> re.Pattern:
    """Matches 'Nov 10', 'November 10th', 'Nov. 10', '10 Nov', '10th of November' or '2026-11-10'."""
    mon = d.strftime("%B")
    month = rf"(?:{mon}|{mon[:3]}\.?)"
    day = rf"0?{d.day}(?:st|nd|rd|th)?"
    return re.compile(rf"\b{month}\s+{day}\b|\b{day}\s+(?:of\s+)?{month}|{d.isoformat()}", re.IGNORECASE)


def names_date(run: Run, arg=None) -> Result:
    """The answer must say which day the price data is from, matching a date in the tool results."""
    dates: set[date] = set()
    for call in run.tool_calls:
        if not call.is_error:
            _dates_in(json.loads(call.output), dates)
    if not dates:
        return _fail("no tool result carried a date to compare against")
    if any(_date_pattern(d).search(run.text) for d in dates):
        return _ok()
    return _fail(f"no mention of the data date; expected one of {sorted(d.isoformat() for d in dates)}")


# ---- other markets: currency, index points, which listing, which dates ----

# Numeric fields in a tool result that are prices in the result's currency.
_PRICE_FIELDS = {
    "close", "start_price", "end_price", "sma_50", "sma_200", "high_52w", "low_52w", "high_price", "low_price",
    "atr_14",
}  # fmt: skip
_UNSET = object()


def _priced_figures(run: Run) -> dict[str | None, set[float]]:
    """currency -> the price figures the tool results gave in it. The key None holds index levels,
    which are points and not money. Results with no currency field (an older shape) are skipped."""
    out: dict[str | None, set[float]] = {}

    def walk(value, currency) -> None:
        if isinstance(value, dict):
            if "currency" in value:
                currency = value["currency"]
            for key, item in value.items():
                if isinstance(item, bool):
                    continue
                if isinstance(item, (int, float)):
                    if key in _PRICE_FIELDS and currency is not _UNSET:
                        out.setdefault(currency, set()).add(abs(float(item)))
                else:
                    walk(item, currency)
        elif isinstance(value, list):
            for item in value:
                walk(item, currency)

    for call in run.tool_calls:
        if not call.is_error:
            walk(json.loads(call.output), _UNSET)
    return out


_BARE_NUMBER = re.compile(rf"(?<![\d.,A-Za-z])({_NUM})(?!\d)(?!\s?%)")


def _quotes_figure(text: str, figures: set[float]) -> bool:
    """Does the text quote one of these figures, with or without a currency sign? A decimal must match
    closely. A whole number may be a rounded or cut-off price, but only for figures of 100 or more
    ('1,368' for 1368.7), so a small count such as '50 days' is not mistaken for a price."""
    for m in _BARE_NUMBER.finditer(text):
        token = m.group(1).rstrip(",")
        value = _to_float(token)
        for figure in figures:
            if "." in token and abs(value - figure) <= 0.0051:
                return True
            if "." not in token and figure >= 100 and abs(value - figure) < 1:
                return True
    return False


_DOLLAR_FAMILY = {"HKD", "SGD", "AUD", "CAD"}
_EXPLICIT_US = re.compile(r"US\$|U\.S\.? dollars?|\bUS dollars?|\bUSD\b")


def _foreign_amounts(run: Run) -> list[str]:
    """Amounts in a currency none of the tool results used. Amounts the user wrote are their own."""
    currencies = {c for c in _priced_figures(run) if c}
    asked = set(_amounts(run.question))
    if not currencies:
        # No tool data to hold the answer to (the user supplied the price): an example amount in the
        # currency the user themself wrote, such as "100 yen" after "3,414 yen", is not a conversion.
        currencies = {c for _, c in asked}
    # A bare "dollars" or "$" after the answer has already named, say, Singapore dollars is not US dollars.
    dollar_family = _DOLLAR_FAMILY & currencies & _currencies_named(run.text)
    if dollar_family and not _EXPLICIT_US.search(run.text):
        currencies = currencies | {"USD"}
    return sorted({f"{v:g} {c}" for v, c in _amounts(run.text) if (v, c) not in asked and not _covers(c, currencies)})


def currency_named(run: Run, arg=None) -> Result:
    """Prices are in the stock's own currency: no dollars for a yen stock, and when the answer quotes a
    price it says what currency that price is in."""
    foreign = _foreign_amounts(run)
    if foreign:
        used = sorted(c for c in _priced_figures(run) if c) or "none"
        return _fail(f"amount in a currency the data does not use (data is in {used}): {foreign}")
    priced, named = _priced_figures(run), _currencies_named(run.text)
    unnamed = [
        c for c in sorted(k for k in priced if k) if _quotes_figure(run.text, priced[c]) and not _named_in(c, named)
    ]
    return _fail(f"quotes a {unnamed} price without naming the currency") if unnamed else _ok()


_CONVERSIONS = [
    r"\b(?:exchange|conversion|currency) rate (?:of|is|was|at)\b[^.]{0,20}\d",
    r"\bconverts? (?:to|into) (?:about |roughly |around |approximately )?\W?\d",
    r"\bconverted (?:to|into)\b[^.]{0,30}\d",
    r"\bequivalent to (?:about |roughly |around |approximately )?\W?\d",
    r"\b(?:is|are|=|equals?)\s+(?:about |roughly |around |approximately )?\d[\d,.]*\s*(?:US |U\.S\. )?dollars\b",
]


def no_currency_conversion(run: Run, arg=None) -> Result:
    """The tools hold no exchange rates, so the answer must not turn one currency into another."""
    foreign = _foreign_amounts(run)
    if foreign:
        return _fail(f"amount in a currency no tool returned: {foreign}")
    for pattern in _CONVERSIONS:
        m = re.search(pattern, run.text, re.IGNORECASE)
        if m:
            return _fail(f"conversion-like phrase: '{m.group(0)}'")
    return _ok()


def index_in_points(run: Run, arg=None) -> Result:
    """An index level is points, not money: never '$51,608', and a quoted level says 'points'."""
    levels = _priced_figures(run).get(None, set())
    if not levels:
        return _ok()
    as_money = [(v, c) for v, c in _amounts(run.text) if any(_matches(v, level) for level in levels)]
    if as_money:
        return _fail(f"index level written as money: {as_money}")
    if _quotes_figure(run.text, levels) and not re.search(r"\bpoints?\b", run.text, re.IGNORECASE):
        return _fail("quotes an index level without saying 'points'")
    return _ok()


# What an answer may call each market when it says which listing it used.
_MARKET_ALIASES = {
    "US": ["US", "U.S.", "United States", "American", "NYSE", "Nasdaq", "New York", "ADR"],
    "Tokyo": ["Tokyo", "Japan", "Japanese", "TSE"],
    "Hong Kong": ["Hong Kong", "HK"],
    "Shanghai": ["Shanghai", "China", "Chinese"],
    "Shenzhen": ["Shenzhen", "China", "Chinese"],
    "India (NSE)": ["NSE", "India", "Indian"],
    "India (BSE)": ["BSE", "Bombay", "India", "Indian"],
    "Australia (ASX)": ["ASX", "Australia", "Australian"],
    "London": ["London", "UK", "U.K.", "Britain", "British", "LSE"],
    "Germany (Xetra)": ["Germany", "German", "Xetra", "Frankfurt"],
    "Paris": ["Paris", "France", "French"],
    "Milan": ["Milan", "Italy", "Italian"],
    "Singapore": ["Singapore"],
    "Thailand": ["Thailand", "Thai", "Bangkok"],
    "Indonesia": ["Indonesia", "Jakarta"],
    "Malaysia": ["Malaysia", "Kuala Lumpur"],
}  # fmt: skip


def _mentions(text: str, names: list[str]) -> bool:
    """Short all-caps names (US, UK, HK) must match their case, or 'us' would match every 'tell us'."""
    for name in names:
        flags = 0 if (len(name) <= 3 and name.isupper()) else re.IGNORECASE
        if re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text, flags):
            return True
    return False


def names_listing(run: Run, arg) -> Result:
    """arg = tickers that are one company listed in different places, like [TM, 7203.T]. If the answer
    uses one, it says which listing. If it fetched none, it must say there is more than one."""

    def says(symbol: str) -> bool:
        market = markets.lookup(symbol).name
        return _mentions(run.text, _MARKET_ALIASES.get(market, [market]))

    group = _as_list(arg)
    used = [s for s in group if s in _symbols_requested(run)]
    if used:
        unsaid = [s for s in used if not says(s)]
        return _fail(f"used {unsaid} without saying which listing it is") if unsaid else _ok()
    if sum(says(s) for s in group) >= 2:
        return _ok()
    return _fail(f"fetched none of {group} and does not say there is more than one listing")


def never_fetches(run: Run, arg) -> Result:
    """No data may be requested for these tickers (a market the app does not cover)."""
    bad = sorted(_symbols_requested(run) & set(_as_list(arg)))
    return _fail(f"fetched data for {bad}") if bad else _ok()


_NOT_COVERED = [
    r"\b(?:don't|do not|can't|cannot|couldn't|won't|unable to|not able to|isn't|aren't|not)\b[^.]{0,50}"
    r"\b(?:cover|covered|support|supported|look up|pull up|have (?:data|prices)|get (?:data|prices))\b",
    r"\bonly (?:cover|support|handle|look up)\b",
    r"\bnot (?:one of )?the (?:markets|exchanges)\b",
]


def declines_market(run: Run, arg) -> Result:
    """arg = {place, offers: [...]}: the answer names the place, says plainly it is not covered, and
    offers something that is covered (one of the `offers` words, like a US ticker or exchange)."""
    if not re.search(re.escape(arg["place"]), run.text, re.IGNORECASE):
        return _fail(f"never mentions {arg['place']}")
    if not any(re.search(p, run.text, re.IGNORECASE) for p in _NOT_COVERED):
        return _fail(f"does not say {arg['place']} is not covered")
    if not _mentions(run.text, arg["offers"]):
        return _fail(f"offers no covered alternative (looked for {arg['offers']})")
    return _ok()


_DATES_DIFFER = [
    r"\bdifferent (?:days|dates|times)\b",
    r"\bdates? (?:may |can |might |could )?(?:differ|vary)\b",
    r"\bnot the same (?:day|date)\b",
    r"\bclose[sd]? at different\b",
    r"\b(?:one|a) day (?:apart|ahead|earlier|later|behind)\b",
    r"\bdiffer(?:s|ent)? by (?:a|one) day\b",
]


def names_all_dates(run: Run, arg=None) -> Result:
    """When results from different markets carry different dates, the answer names every date or says
    plainly that the dates differ. (One date needs no such care; names_date covers it.)"""
    dates: set[date] = set()
    for call in run.tool_calls:
        if not call.is_error:
            _dates_in(json.loads(call.output), dates)
    if len(dates) < 2:
        return _ok()
    if all(_date_pattern(d).search(run.text) for d in dates):
        return _ok()
    if any(re.search(p, run.text, re.IGNORECASE) for p in _DATES_DIFFER):
        return _ok()
    found = sorted(d.isoformat() for d in dates)
    return _fail(f"results are dated {found}, but the answer names neither all of them nor that they differ")


# ---- favourites ----

# "Want me to add Nvidia to your favourites?", "Shall I save it as a favourite?", "...heart it for you?"
_FAVORITE_OFFER = re.compile(r"\b(?:favou?rite|heart|watch ?list)[^.!?\n]*\?", re.IGNORECASE)


def offers_favorite(run: Run, arg=None) -> Result:
    """The answer asks whether to add the stock to the favourites (a question, not a statement)."""
    return _ok() if _FAVORITE_OFFER.search(run.text) else _fail("never offered to add it to the favourites")


def no_favorite_offer(run: Run, arg=None) -> Result:
    """No offer to favourite: used when the stock already is one, or the list is full."""
    m = _FAVORITE_OFFER.search(run.text)
    return _fail(f"offered to favourite: '{m.group(0).strip()}'") if m else _ok()


CHECKS: dict[str, Callable[..., Result]] = {
    f.__name__: f
    for f in (
        answered, no_banned_words, no_advice, plain_text, numbers_grounded, no_numbers,
        calls_tool, never_calls, calls_no_tools, tool_input, covers_symbols, names_date,
        currency_named, no_currency_conversion, index_in_points, names_listing, never_fetches,
        declines_market, names_all_dates, offers_favorite, no_favorite_offer,
    )
}  # fmt: skip

# Applied to every case in addition to the ones the case lists.
DEFAULT_CHECKS = ["answered", "no_banned_words", "no_advice", "plain_text", "numbers_grounded"]
