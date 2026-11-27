"""Favourite stocks in the sidebar: added by Claude (when asked, or after its offer), refused with a
visible error for a ticker that doesn't exist, capped at 10, removable, and priced from /api/quotes.
Both /api/chat and /api/quotes are stubbed (see conftest.py)."""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest
from playwright.sync_api import Page, expect

from .conftest import answer, sse

KEY = "stock-guide-favorites-v1"
ROWS = ".fav-row"
TEN = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "NFLX", "BHP.AX", "^DJI"]


@pytest.fixture
def quotes(chat, page: Page):
    """Stub /api/quotes. Every symbol is priced, except NOPE. Records the symbols asked for."""
    asked: list[list[str]] = []

    def handle(route):
        symbols = parse_qs(urlparse(route.request.url).query)["symbols"][0].split(",")
        asked.append(symbols)
        out = []
        for i, s in enumerate(symbols):
            if s == "NOPE":
                out.append({"symbol": s, "error": "No price data found"})
            elif s.startswith("^"):
                out.append({"symbol": s, "close": 44000.5, "as_of": "2026-11-24", "change_pct": -0.4,
                            "currency": None, "kind": "index", "market": "US"})  # fmt: skip
            else:
                out.append({"symbol": s, "close": 100 + i, "as_of": "2026-11-24", "change_pct": 1.25,
                            "currency": "USD", "market": "US"})  # fmt: skip
        route.fulfill(status=200, content_type="application/json", body=json.dumps({"quotes": out}))

    page.route("**/api/quotes?*", handle)
    page.reload()
    return asked


def ask(page: Page, text: str):
    page.locator("#input").fill(text)
    page.locator("#send").click()


def stored(page: Page) -> dict:
    return json.loads(page.evaluate(f"localStorage.getItem('{KEY}') || '{{}}'"))


def seed(page: Page, symbols: list[str]):
    data = {"list": [{"symbol": s, "added": 1} for s in symbols]}
    page.evaluate("([k, v]) => localStorage.setItem(k, JSON.stringify(v))", [KEY, data])
    page.reload()


def snapshot_answer(symbol: str, text: str = "It's up a little.") -> str:
    return answer(text, tools=(("get_stock_snapshot", {"symbol": symbol}, True),))


def failed_answer(symbol: str, text: str) -> str:
    """add_favorite was refused: the server sends favorite_failed instead of favorite."""
    message = f"Failed to add {symbol} to favourites: no stock or index with that ticker was found."
    return sse(
        {"type": "tool_call", "name": "add_favorite", "input": {"symbol": symbol}},
        {"type": "tool_result", "name": "add_favorite", "ok": False},
        {"type": "favorite_failed", "symbol": symbol, "reason": "not_found", "message": message},
        {"type": "text", "text": text},
        {"type": "done", "messages": [{"role": "assistant", "content": [{"type": "text", "text": text}]}]},
    )


def favorite_answer(symbol: str, text: str) -> str:
    """Claude calls add_favorite: tool_call, tool_result, then the favorite event the server sends."""
    return sse(
        {"type": "tool_call", "name": "add_favorite", "input": {"symbol": symbol}},
        {"type": "tool_result", "name": "add_favorite", "ok": True},
        {"type": "favorite", "action": "add", "symbol": symbol, "favorites": [symbol]},
        {"type": "text", "text": text},
        {"type": "done", "messages": [{"role": "assistant", "content": [{"type": "text", "text": text}]}]},
    )


def test_empty_sidebar_explains_how_to_add_favourites(quotes, page):
    expect(page.locator(ROWS)).to_have_count(0)
    expect(page.locator("#no-favs")).to_be_visible()
    assert quotes == []  # nothing to price, so no request


def test_asking_claude_to_heart_a_stock_adds_it_with_its_latest_price(quotes, chat, page):
    chat.reply(favorite_answer("NVDA", "Done, Nvidia is in your favourites."))
    ask(page, "Heart Nvidia")
    row = page.locator(ROWS)
    expect(row).to_have_count(1)
    expect(row.locator("strong")).to_have_text("NVDA")
    expect(row.locator(".fav-change")).to_have_text("+1.3%")
    expect(row.locator(".fav-bottom")).to_contain_text("$100.00")
    expect(row.locator(".fav-bottom")).to_contain_text("Nov 24")
    expect(page.locator("#fav-count")).to_have_text("1/10")
    expect(page.locator(".steps li").first).to_have_text("Adding NVDA to your favourites")
    assert [f["symbol"] for f in stored(page)["list"]] == ["NVDA"]
    assert ["NVDA"] in quotes


def test_every_chat_turn_tells_the_server_the_current_favourites(quotes, chat, page):
    seed(page, ["AAPL", "NVDA"])
    chat.reply(snapshot_answer("TSLA"))
    ask(page, "How's Tesla?")
    expect(page.locator(".msg.assistant .body")).to_have_text("It's up a little.")
    assert chat.requests[-1]["favorites"] == ["AAPL", "NVDA"]


def test_a_ticker_that_does_not_exist_shows_an_error_and_is_not_added(quotes, chat, page):
    seed(page, ["AAPL"])
    chat.reply(failed_answer("ZZZZ", "I couldn't find a stock called ZZZZ, so I couldn't add it."))
    ask(page, "Heart ZZZZ")
    error = page.locator(".msg.assistant .fav-error")
    expect(error).to_have_text("Failed to add ZZZZ to favourites: no stock or index with that ticker was found.")
    expect(error).to_have_attribute("role", "alert")
    expect(page.locator(".steps li.fail")).to_have_text("Adding ZZZZ to your favourites")
    expect(page.locator(f"{ROWS} strong")).to_have_text(["AAPL"])
    assert [f["symbol"] for f in stored(page)["list"]] == ["AAPL"]
    assert all("ZZZZ" not in asked for asked in quotes)  # never priced either


def test_asking_about_a_stock_never_favourites_it_by_itself(quotes, chat, page):
    chat.reply(snapshot_answer("TSLA"))
    for n in range(1, 5):
        ask(page, f"How's Tesla? ({n})")
        expect(page.locator(".msg.assistant .body")).to_have_count(n)
        expect(page.locator(".msg.assistant .body").nth(n - 1)).to_have_text("It's up a little.")
    expect(page.locator(ROWS)).to_have_count(0)
    assert stored(page) == {}


def test_the_list_stops_at_ten(quotes, chat, page):
    seed(page, TEN)
    expect(page.locator(ROWS)).to_have_count(10)
    expect(page.locator("#fav-count")).to_have_text("10/10")
    # Even if the page somehow got an add for an 11th, it keeps 10.
    chat.reply(favorite_answer("IBM", "Added."))
    ask(page, "Heart IBM")
    expect(page.locator(".msg.assistant .body")).to_have_text("Added.")
    expect(page.locator(ROWS)).to_have_count(10)
    assert "IBM" not in [f["symbol"] for f in stored(page)["list"]]


def test_the_heart_button_removes_a_favourite(quotes, page):
    seed(page, ["AAPL", "NVDA"])
    page.get_by_role("button", name="Remove AAPL from favourites").click()
    expect(page.locator(f"{ROWS} strong")).to_have_text(["NVDA"])
    assert [f["symbol"] for f in stored(page)["list"]] == ["NVDA"]


def test_tapping_a_favourite_asks_about_it(quotes, chat, page):
    seed(page, ["NVDA"])
    chat.reply(snapshot_answer("NVDA"))
    page.get_by_role("button", name="Ask how NVDA is doing").click()
    expect(page.locator(".msg.user")).to_have_text("How's NVDA doing?")


def test_indexes_show_points_and_missing_prices_say_so(quotes, page):
    seed(page, ["^DJI", "NOPE"])
    rows = page.locator(ROWS)
    expect(rows.nth(0).locator(".fav-bottom")).to_contain_text("44,000.50 pts")
    expect(rows.nth(0).locator(".fav-change")).to_have_text("-0.4%")
    expect(rows.nth(1).locator(".fav-bottom")).to_have_text("No price right now")


def test_a_broken_store_means_no_favourites_not_a_broken_page(quotes, chat, page):
    page.evaluate(f"localStorage.setItem('{KEY}', '{{not json')")
    page.reload()
    expect(page.locator("#no-favs")).to_be_visible()
    chat.reply(snapshot_answer("NVDA"))
    ask(page, "How's Nvidia?")
    expect(page.locator(".msg.assistant .body")).to_have_text("It's up a little.")
