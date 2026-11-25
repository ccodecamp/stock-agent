"""Favourite stocks: the agent's add/remove tools and the sidebar's latest prices.

Favourites live in the browser, like saved chats. The server keeps no state:
the page sends its current list with every chat turn, and when Claude adds or
removes one the loop emits a `favorite` event so the page can update its copy.

The cap of MAX_FAVORITES is enforced in three places: request validation (the page
can't send more), the add tool (Claude can't push past it), and the page itself
(auto-favouriting stops at the cap).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .tools import SYMBOL_PATTERN, _get_history, _market_fields

logger = logging.getLogger(__name__)

MAX_FAVORITES = 10

# (ticker) -> does the data source have prices for it. Raises if it can't tell.
SymbolCheck = Callable[[str], bool]

FAVORITE_TOOL_NAMES = frozenset({"add_favorite", "remove_favorite"})

_SYMBOL_PROP = {
    "type": "string",
    "description": "Yahoo Finance ticker in capitals, with the exchange ending outside the US, e.g. NVDA, 7203.T, ^DJI",
}

FAVORITE_TOOLS: list[dict] = [
    {
        "name": "add_favorite",
        "description": (
            f"Add a stock or index to the user's favourites, a list of up to {MAX_FAVORITES} shown in a sidebar "
            "with the latest price. Use when the user asks to favourite, heart, star, save, follow or watch one, "
            "or says yes when you offered. The ticker is checked first, and the add fails if it has no price data: "
            "then tell the user plainly it couldn't be added and suggest checking the name."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": _SYMBOL_PROP},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
    {
        "name": "remove_favorite",
        "description": (
            "Remove a stock or index from the user's favourites. Use when they ask to unfavourite or remove one."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"symbol": _SYMBOL_PROP},
            "required": ["symbol"],
            "additionalProperties": False,
        },
    },
]


class _FavoriteInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    symbol: str = Field(pattern=SYMBOL_PATTERN)


def run_favorite_tool(
    name: str, raw_input: object, favorites: list[str], exists: SymbolCheck
) -> tuple[str, bool, dict | None]:
    """Run add_favorite / remove_favorite against this turn's list, updating it in place.

    Returns (JSON content for Claude, is_error, event for the page or None). A failed add
    also produces an event, `favorite_failed`, so the page can show the error itself.
    """
    try:
        symbol = _FavoriteInput.model_validate(raw_input).symbol
    except ValidationError as e:
        return json.dumps({"error": "Invalid input", "details": e.errors(include_url=False)}, default=str), True, None

    action: Literal["add", "remove"] = "add" if name == "add_favorite" else "remove"
    if action == "add":
        if symbol in favorites:
            return _result(symbol, favorites, note="It was already a favourite."), False, None
        if len(favorites) >= MAX_FAVORITES:
            return _add_failed(
                symbol, "full", f"the list is full ({MAX_FAVORITES} of {MAX_FAVORITES})", favorites,
                hint="Ask the user which one to remove first.",
            )  # fmt: skip
        try:
            found = exists(symbol)
        except Exception:  # noqa: BLE001 — network / yfinance failures must not crash the chat
            logger.exception("Checking %s for a favourite failed", symbol)
            why = "the price data source didn't answer; try again shortly"
            return _add_failed(symbol, "unavailable", why, favorites)
        if not found:
            return _add_failed(
                symbol, "not_found", "no stock or index with that ticker was found", favorites,
                hint="Tell the user it couldn't be added and suggest checking the name or ticker.",
            )  # fmt: skip
        favorites.append(symbol)
    else:
        if symbol not in favorites:
            return json.dumps({"error": f"{symbol} is not in the favourites.", "favorites": favorites}), True, None
        favorites.remove(symbol)

    event = {"type": "favorite", "action": action, "symbol": symbol, "favorites": list(favorites)}
    return _result(symbol, favorites), False, event


def _add_failed(
    symbol: str, reason: str, why: str, favorites: list[str], hint: str | None = None
) -> tuple[str, bool, dict]:
    message = f"Failed to add {symbol} to favourites: {why}."
    content = {"error": message, "reason": reason, "favorites": favorites}
    if hint:
        content["hint"] = hint
    event = {"type": "favorite_failed", "symbol": symbol, "reason": reason, "message": message}
    return json.dumps(content), True, event


def _result(symbol: str, favorites: list[str], note: str | None = None) -> str:
    out = {"symbol": symbol, "favorites": favorites, "count": len(favorites), "max": MAX_FAVORITES}
    if note:
        out["note"] = note
    return json.dumps(out)


def favorites_context(favorites: list[str]) -> str:
    """The line appended to the system prompt each turn, so Claude knows what's already saved."""
    if not favorites:
        return f"The user has no favourites yet (0 of {MAX_FAVORITES})."
    full = " The list is full." if len(favorites) >= MAX_FAVORITES else ""
    return f"The user's favourites ({len(favorites)} of {MAX_FAVORITES}): {', '.join(favorites)}.{full}"


def latest_quote(symbol: str) -> dict:
    """Last close and the change from the close before, for the sidebar. No model involved.

    Uses the same 15-minute price cache as the chat tools, so a chat about a favourite
    and the sidebar share one download.
    """
    try:
        history = _get_history(symbol)
    except ValueError:
        return {"symbol": symbol, "error": "No price data found"}
    except Exception:  # noqa: BLE001 — one failing symbol must not break the whole sidebar
        return {"symbol": symbol, "error": "Data source failed"}
    close = history.df["Close"].dropna()
    if close.empty:
        return {"symbol": symbol, "error": "No price data found"}
    last = float(close.iloc[-1])
    out = {
        "symbol": symbol,
        "close": round(last, 2),
        "as_of": close.index[-1].strftime("%Y-%m-%d"),
        "change_pct": None,
        **_market_fields(history),
    }
    if len(close) > 1 and float(close.iloc[-2]):
        out["change_pct"] = round((last / float(close.iloc[-2]) - 1) * 100, 2)
    return out
