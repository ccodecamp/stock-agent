"""Favourite stocks: the add/remove tools, the loop's favorite event, request validation,
and the sidebar's /api/quotes endpoint. Fake Anthropic client and fake prices; no network."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from src.api import app as app_module
from src.api import chat, favorites, tools
from src.api.favorites import MAX_FAVORITES, favorites_context, run_favorite_tool
from src.api.limits import daily_limit
from src.data_fetcher import PriceHistory
from tests.test_api_chat import FakeClient, _text_turn, _tool_turn
from tests.test_indicators import _synthetic_df

TEN = ["AAPL", "MSFT", "NVDA", "TSLA", "AMZN", "META", "GOOGL", "NFLX", "BHP.AX", "^DJI"]


def known(symbol):
    """Stands in for tools.symbol_exists: ZZZZ is not a real ticker."""
    return symbol != "ZZZZ"


def must_not_check(symbol):
    pytest.fail(f"checked {symbol} when it didn't need to")


def source_down(symbol):
    raise ConnectionError("yahoo is down")


# ---- the tools on their own ----


def test_add_appends_and_tells_the_page():
    favs = ["AAPL"]
    content, is_error, event = run_favorite_tool("add_favorite", {"symbol": "NVDA"}, favs, known)
    assert not is_error
    assert favs == ["AAPL", "NVDA"]
    assert event == {"type": "favorite", "action": "add", "symbol": "NVDA", "favorites": ["AAPL", "NVDA"]}
    assert json.loads(content)["count"] == 2


def test_adding_an_existing_favourite_is_a_no_op_without_an_event():
    favs = ["NVDA"]
    content, is_error, event = run_favorite_tool("add_favorite", {"symbol": "NVDA"}, favs, must_not_check)
    assert not is_error and event is None
    assert favs == ["NVDA"]
    assert "already" in json.loads(content)["note"]


def test_add_refuses_past_the_cap_without_looking_the_ticker_up():
    favs = list(TEN)
    content, is_error, event = run_favorite_tool("add_favorite", {"symbol": "IBM"}, favs, must_not_check)
    assert is_error
    assert favs == TEN
    assert json.loads(content)["reason"] == "full"
    assert event["type"] == "favorite_failed" and event["reason"] == "full"
    assert event["message"].startswith("Failed to add IBM to favourites")


def test_add_refuses_a_ticker_that_does_not_exist():
    favs = ["AAPL"]
    content, is_error, event = run_favorite_tool("add_favorite", {"symbol": "ZZZZ"}, favs, known)
    assert is_error
    assert favs == ["AAPL"]  # nothing was added
    assert json.loads(content)["reason"] == "not_found"
    assert event == {
        "type": "favorite_failed",
        "symbol": "ZZZZ",
        "reason": "not_found",
        "message": "Failed to add ZZZZ to favourites: no stock or index with that ticker was found.",
    }


def test_add_fails_safely_when_the_data_source_is_down():
    favs = []
    content, is_error, event = run_favorite_tool("add_favorite", {"symbol": "NVDA"}, favs, source_down)
    assert is_error and favs == []
    assert event["reason"] == "unavailable"
    assert "try again" in json.loads(content)["error"]


def test_remove_drops_it_and_tells_the_page():
    favs = ["AAPL", "NVDA"]
    _, is_error, event = run_favorite_tool("remove_favorite", {"symbol": "AAPL"}, favs, must_not_check)
    assert not is_error
    assert favs == ["NVDA"]
    assert event["action"] == "remove" and event["symbol"] == "AAPL"


def test_removing_a_non_favourite_is_an_error():
    _, is_error, event = run_favorite_tool("remove_favorite", {"symbol": "IBM"}, ["NVDA"], must_not_check)
    assert is_error and event is None


@pytest.mark.parametrize("bad", [{}, {"symbol": "nvda"}, {"symbol": "NVDA", "x": 1}, {"symbol": "<script>"}, "NVDA"])
def test_bad_input_is_rejected(bad):
    favs = []
    _, is_error, event = run_favorite_tool("add_favorite", bad, favs, must_not_check)
    assert is_error and event is None and favs == []


def test_context_line_names_the_list_and_says_when_full():
    assert "no favourites" in favorites_context([])
    assert "AAPL, NVDA" in favorites_context(["AAPL", "NVDA"])
    assert "2 of 10" in favorites_context(["AAPL", "NVDA"])
    assert "full" in favorites_context(TEN)


# ---- inside the tool-use loop ----


def test_loop_emits_a_favorite_event_and_never_calls_the_data_tools():
    runner_calls = []
    client = FakeClient([_tool_turn(name="add_favorite", inp={"symbol": "NVDA"}), _text_turn("Added Nvidia.")])
    events = list(
        chat.stream_chat(
            [{"role": "user", "content": "Heart Nvidia"}],
            client=client,
            model="m",
            tool_runner=lambda n, i: runner_calls.append(n) or ("{}", False),
            favorites=["AAPL"],
            symbol_check=known,
        )
    )
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "favorite", "text", "done"]
    assert events[2]["favorites"] == ["AAPL", "NVDA"]
    assert runner_calls == []


def test_claude_is_told_the_current_favourites_and_sees_changes_within_the_turn():
    client = FakeClient([_tool_turn(name="add_favorite", inp={"symbol": "NVDA"}), _text_turn()])
    list(
        chat.stream_chat(
            [{"role": "user", "content": "Heart Nvidia"}],
            client=client,
            model="m",
            favorites=["AAPL"],
            symbol_check=known,
        )
    )
    first, second = (call["system"] for call in client.calls)
    assert first[0]["text"] == chat.SYSTEM_PROMPT  # the fixed part stays identical, so it stays cacheable
    assert "AAPL" in first[1]["text"] and "NVDA" not in first[1]["text"]
    assert "AAPL, NVDA" in second[1]["text"]


def test_favourite_tools_are_offered_to_claude():
    client = FakeClient([_text_turn()])
    list(chat.stream_chat([{"role": "user", "content": "hi"}], client=client, model="m"))
    names = {t["name"] for t in client.calls[0]["tools"]}
    assert {"add_favorite", "remove_favorite"} <= names


def test_full_list_gives_claude_an_error_and_the_page_a_failure_event():
    client = FakeClient([_tool_turn(name="add_favorite", inp={"symbol": "IBM"}), _text_turn()])
    events = list(
        chat.stream_chat(
            [{"role": "user", "content": "Heart IBM"}], client=client, model="m", favorites=TEN, symbol_check=known
        )
    )
    types = [e["type"] for e in events]
    assert "favorite" not in types
    assert events[1] == {"type": "tool_result", "name": "add_favorite", "ok": False}
    assert events[2]["type"] == "favorite_failed" and events[2]["reason"] == "full"


def test_unknown_ticker_is_never_added_and_claude_and_the_page_are_told():
    client = FakeClient([_tool_turn(name="add_favorite", inp={"symbol": "ZZZZ"}), _text_turn("Couldn't add it.")])
    events = list(
        chat.stream_chat(
            [{"role": "user", "content": "Heart ZZZZ"}],
            client=client,
            model="m",
            favorites=["AAPL"],
            symbol_check=known,
        )
    )
    assert [e["type"] for e in events] == ["tool_call", "tool_result", "favorite_failed", "text", "done"]
    assert events[2]["message"] == "Failed to add ZZZZ to favourites: no stock or index with that ticker was found."
    tool_result = client.calls[1]["messages"][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert json.loads(tool_result["content"])["reason"] == "not_found"
    # Claude's next request still lists only the real favourite.
    assert "AAPL" in client.calls[1]["system"][1]["text"] and "ZZZZ" not in client.calls[1]["system"][1]["text"]


def test_the_real_ticker_check_is_used_by_default(monkeypatch):
    checked = []
    monkeypatch.setattr(chat, "symbol_exists", lambda s: checked.append(s) or True)
    client = FakeClient([_tool_turn(name="add_favorite", inp={"symbol": "NVDA"}), _text_turn()])
    list(chat.stream_chat([{"role": "user", "content": "Heart Nvidia"}], client=client, model="m"))
    assert checked == ["NVDA"]


def test_symbol_exists_asks_the_data_source(monkeypatch):
    def fake_fetch(symbol, lookback_years=2):
        if symbol == "ZZZZ":
            raise ValueError(f"No price data returned for {symbol}")
        if symbol == "DOWN":
            raise ConnectionError("network")
        return PriceHistory(symbol=symbol, df=_synthetic_df(n=30), fetched_at=datetime.now(UTC))

    tools.clear_cache()
    monkeypatch.setattr(tools, "fetch_history", fake_fetch)
    assert tools.symbol_exists("NVDA") is True
    assert tools.symbol_exists("ZZZZ") is False
    with pytest.raises(ConnectionError):  # "can't tell" is not "doesn't exist"
        tools.symbol_exists("DOWN")
    tools.clear_cache()


def test_prompt_explains_both_ways_to_favourite_and_the_failure_case():
    p = chat.SYSTEM_PROMPT
    assert "add_favorite" in p  # asked directly
    assert "Want me to add" in p  # offered after a single-stock answer
    assert "couldn't be added" in p  # unknown ticker or full list
    assert "three times" not in p  # no automatic favouriting


# ---- HTTP ----


@pytest.fixture
def api(monkeypatch):
    seen = {}

    def fake_stream(messages, **kwargs):
        seen.update(kwargs)
        return iter([{"type": "text", "text": "hi"}])

    def fake_fetch(symbol, lookback_years=2):
        if symbol == "NOPE":
            raise ValueError(f"No price data returned for {symbol}")
        return PriceHistory(symbol=symbol, df=_synthetic_df(n=300), fetched_at=datetime.now(UTC))

    monkeypatch.setattr(app_module, "stream_chat", fake_stream)
    monkeypatch.setattr(tools, "fetch_history", fake_fetch)
    tools.clear_cache()
    app_module.limiter.reset()
    daily_limit.reset()
    client = TestClient(app_module.app)
    client.seen = seen
    yield client
    tools.clear_cache()
    daily_limit.reset()


def _chat(api, favs):
    return api.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}], "favorites": favs})


def test_chat_passes_favourites_through_deduped(api):
    assert _chat(api, ["NVDA", "AAPL", "NVDA"]).status_code == 200
    assert api.seen["favorites"] == ["NVDA", "AAPL"]


def test_chat_without_favourites_still_works(api):
    assert api.post("/api/chat", json={"messages": [{"role": "user", "content": "hi"}]}).status_code == 200
    assert api.seen["favorites"] == []


@pytest.mark.parametrize("favs", [[*TEN, "IBM"], ["nvda"], ["NVDA; drop"], "NVDA"])
def test_chat_rejects_bad_favourites(api, favs):
    assert _chat(api, favs).status_code == 422


def test_quotes_returns_latest_close_and_daily_change(api):
    r = api.get("/api/quotes", params={"symbols": "NVDA,^DJI"})
    assert r.status_code == 200
    nvda, dji = r.json()["quotes"]
    df = _synthetic_df(n=300)
    assert nvda["symbol"] == "NVDA"
    assert nvda["close"] == round(float(df["Close"].iloc[-1]), 2)
    assert nvda["change_pct"] == pytest.approx((df["Close"].iloc[-1] / df["Close"].iloc[-2] - 1) * 100, abs=0.01)
    assert nvda["as_of"] == df.index[-1].strftime("%Y-%m-%d")
    assert nvda["currency"]
    assert dji["kind"] == "index" and dji["currency"] is None


def test_one_unknown_symbol_does_not_break_the_others(api):
    nvda, nope = api.get("/api/quotes", params={"symbols": "NVDA,NOPE"}).json()["quotes"]
    assert "close" in nvda
    assert nope == {"symbol": "NOPE", "error": "No price data found"}


@pytest.mark.parametrize("symbols", ["", ",", "nvda", "NVDA,<b>", ",".join([*TEN, "IBM"])])
def test_quotes_rejects_bad_symbol_lists(api, symbols):
    assert api.get("/api/quotes", params={"symbols": symbols}).status_code == 422


def test_quotes_do_not_use_up_the_daily_chat_limit(api, monkeypatch):
    monkeypatch.setenv("CHAT_DAILY_LIMIT", "1")
    for _ in range(3):
        assert api.get("/api/quotes", params={"symbols": "NVDA"}).status_code == 200
    assert _chat(api, []).status_code == 200


def test_quotes_are_rate_limited(api, monkeypatch):
    monkeypatch.setenv("QUOTES_RATE_LIMIT", "2/minute")
    codes = [api.get("/api/quotes", params={"symbols": "NVDA"}).status_code for _ in range(3)]
    assert codes == [200, 200, 429]


def test_max_matches_the_page():
    # web/app.js has its own copy of the cap; keep them in step.
    js = (app_module.WEB_DIR / "app.js").read_text()
    assert f"const MAX_FAVORITES = {MAX_FAVORITES};" in js
    assert favorites.MAX_FAVORITES == 10
