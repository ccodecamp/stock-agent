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


def _matches(quoted: float, source: float) -> bool:
    # A whole number may be a rounded value ("about 5%" for 4.7); a decimal must match closely.
    tolerance = 0.5 if quoted == int(quoted) else 0.05
    return abs(quoted - source) <= tolerance


def numbers_grounded(run: Run, arg=None) -> Result:
    """Every $ amount and % in the answer must come from a tool result (or the user's question)."""
    # 100 is the baseline of the tools' "value of $100" field, so answers may restate it.
    allowed = [100.0, *_quoted_numbers(run.question)]
    for call in run.tool_calls:
        if not call.is_error:
            allowed += _numbers_in(json.loads(call.output))
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


CHECKS: dict[str, Callable[..., Result]] = {
    f.__name__: f
    for f in (
        answered, no_banned_words, no_advice, plain_text, numbers_grounded, no_numbers,
        calls_tool, never_calls, calls_no_tools, tool_input, covers_symbols,
    )
}  # fmt: skip

# Applied to every case in addition to the ones the case lists.
DEFAULT_CHECKS = ["answered", "no_banned_words", "no_advice", "plain_text", "numbers_grounded"]
