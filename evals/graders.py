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


# ---- grounding ----

_DOLLARS = re.compile(r"\$\s?(\d[\d,]*(?:\.\d+)?)")
_PERCENTS = re.compile(r"(\d+(?:\.\d+)?)\s?%")


def _quoted_numbers(text: str) -> list[float]:
    found = _DOLLARS.findall(text) + _PERCENTS.findall(text)
    return [float(s.replace(",", "")) for s in found]


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
    tolerance = 0.5 if quoted == int(quoted) else 0.05
    return abs(quoted - source) <= tolerance


def numbers_grounded(run: Run, arg=None) -> Result:
    """Every $ amount and % in the answer must come from a tool result (or the user's question)."""
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


def covers_symbols(run: Run, arg) -> Result:
    symbols = {c.input.get("symbol") for c in run.tool_calls if isinstance(c.input, dict)}
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


CHECKS: dict[str, Callable[..., Result]] = {
    f.__name__: f
    for f in (
        answered, no_banned_words, no_advice, plain_text, numbers_grounded, no_numbers,
        calls_tool, never_calls, calls_no_tools, tool_input, covers_symbols, names_date,
    )
}  # fmt: skip

# Applied to every case in addition to the ones the case lists.
DEFAULT_CHECKS = ["answered", "no_banned_words", "no_advice", "plain_text", "numbers_grounded"]
