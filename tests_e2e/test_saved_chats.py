"""Saved chats: several conversations kept in the browser, a list to switch between them,
and a drawer version of that list on phones. /api/chat is stubbed (see conftest.py)."""

from __future__ import annotations

import json

from playwright.sync_api import Page, expect

from .conftest import answer, sse

KEY = "stock-guide-chats-v1"
ROWS = ".chat-row .chat-open"


def ask(page: Page, text: str):
    page.locator("#input").fill(text)
    page.locator("#send").click()


def stored(page: Page) -> list[dict]:
    return json.loads(page.evaluate(f"localStorage.getItem('{KEY}') || '[]'"))


def seed(page: Page, chats: list[dict]):
    page.evaluate("([k, v]) => localStorage.setItem(k, JSON.stringify(v))", [KEY, chats])


def fake_chat(n: int, *, updated: int) -> dict:
    history = [
        {"role": "user", "content": f"Question {n}"},
        {"role": "assistant", "content": [{"type": "text", "text": f"Answer {n}"}]},
    ]
    return {"id": f"c{n}", "title": f"Question {n}", "updated": updated, "history": history, "charts": []}


def two_chats(chat, page):
    chat.reply(answer("Nvidia is up."))
    ask(page, "How's Nvidia?")
    expect(page.locator(".msg.assistant .body")).to_have_text("Nvidia is up.")
    page.locator("#new-chat").click()
    chat.reply(answer("Apple is flat."))
    ask(page, "How's Apple?")
    expect(page.locator(".msg.assistant .body")).to_have_text("Apple is flat.")


def test_a_finished_chat_appears_in_the_list_titled_by_its_first_question(chat, page):
    expect(page.locator(ROWS)).to_have_count(0)
    expect(page.locator("#no-chats")).to_be_visible()
    chat.reply(answer("Fine."))
    ask(page, "How's Nvidia doing today?")
    expect(page.locator(ROWS)).to_have_text(["How's Nvidia doing today?"])
    expect(page.locator("#no-chats")).to_be_hidden()
    assert len(stored(page)) == 1


def test_new_chat_keeps_the_old_chat_and_you_can_go_back_to_it(chat, page):
    two_chats(chat, page)
    expect(page.locator(ROWS)).to_have_text(["How's Apple?", "How's Nvidia?"])
    page.locator(ROWS).nth(1).click()
    expect(page.locator(".msg.user")).to_have_text("How's Nvidia?")
    expect(page.locator(".msg.assistant .body")).to_have_text("Nvidia is up.")
    expect(page.locator(".chat-row.current .chat-open")).to_have_text("How's Nvidia?")


def test_continuing_an_old_chat_sends_that_chats_history_not_the_other_ones(chat, page):
    two_chats(chat, page)
    page.locator(ROWS).nth(1).click()
    chat.reply(answer("Still up."))
    ask(page, "And now?")
    expect(page.locator(".msg.assistant .body").nth(1)).to_have_text("Still up.")
    sent = [m.get("content") for m in chat.requests[-1]["messages"]]
    assert sent[0] == "How's Nvidia?"
    assert sent[-1] == "And now?"
    assert not any("Apple" in str(c) for c in sent)
    # The continued chat moves to the top of the list.
    expect(page.locator(ROWS).first).to_have_text("How's Nvidia?")


def test_chats_survive_a_reload_but_the_page_opens_on_a_fresh_chat(chat, page):
    two_chats(chat, page)
    page.reload()
    expect(page.locator(ROWS)).to_have_count(2)
    expect(page.locator(".msg")).to_have_count(0)
    expect(page.locator("#empty")).to_be_visible()


def test_deleting_takes_two_taps_and_removes_only_that_chat(chat, page):
    two_chats(chat, page)
    first = page.locator(".chat-del").first
    first.click()
    expect(first).to_have_text("Delete?")
    expect(page.locator(ROWS)).to_have_count(2)  # one tap is not enough
    first.click()
    expect(page.locator(ROWS)).to_have_text(["How's Nvidia?"])
    assert [c["title"] for c in stored(page)] == ["How's Nvidia?"]
    # That was the open chat, so we are back on an empty one.
    expect(page.locator("#empty")).to_be_visible()


def test_deleting_the_last_chat_empties_the_store(chat, page):
    chat.reply(answer("Fine."))
    ask(page, "Only one")
    expect(page.locator(ROWS)).to_have_count(1)
    page.locator(".chat-del").click()
    page.locator(".chat-del").click()
    expect(page.locator(ROWS)).to_have_count(0)
    assert stored(page) == []


def test_a_failed_turn_is_not_saved(chat, page):
    chat.reply((500, '{"detail":"boom"}'))
    ask(page, "This will fail")
    expect(page.locator(".error")).to_be_visible()
    expect(page.locator(ROWS)).to_have_count(0)
    assert stored(page) == []


def test_a_chart_comes_back_when_a_chat_is_reopened(chat, page):
    series = [{"symbol": "NVDA", "dates": ["2026-01-02", "2026-06-01", "2026-11-10"], "closes": [100.0, 120.0, 150.0]}]
    chat.reply(
        sse(
            {"type": "chart", "series": series},
            {"type": "text", "text": "Up half."},
            {"type": "done", "messages": [{"role": "assistant", "content": [{"type": "text", "text": "Up half."}]}]},
        )
    )
    ask(page, "Chart Nvidia")
    expect(page.locator(".chart")).to_have_count(1)
    page.locator("#new-chat").click()
    expect(page.locator(".chart")).to_have_count(0)
    page.locator(ROWS).click()
    expect(page.locator(".chart")).to_have_count(1)
    expect(page.locator(".msg.assistant .body")).to_have_text("Up half.")


def test_a_tool_using_chat_reopens_as_plain_question_and_answer(chat, page):
    tool_turn = [
        {"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "get_stock_snapshot", "input": {}}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]},
        {"role": "assistant", "content": [{"type": "text", "text": "Here you go."}]},
    ]
    chat.reply(sse({"type": "text", "text": "Here you go."}, {"type": "done", "messages": tool_turn}))
    ask(page, "Snapshot please")
    expect(page.locator(".msg.assistant .body")).to_have_text("Here you go.")
    page.locator("#new-chat").click()
    page.locator(ROWS).click()
    expect(page.locator(".msg.user")).to_have_count(1)  # the tool result is not shown as a person's message
    expect(page.locator(".msg.assistant")).to_have_count(1)
    expect(page.locator(".msg.assistant .body")).to_have_text("Here you go.")


def test_only_the_30_newest_chats_are_kept(chat, page):
    seed(page, [fake_chat(n, updated=1000 + n) for n in range(30)])
    page.reload()
    expect(page.locator(ROWS)).to_have_count(30)
    chat.reply(answer("Fine."))
    ask(page, "Brand new")
    expect(page.locator(ROWS).first).to_have_text("Brand new")
    expect(page.locator(ROWS)).to_have_count(30)
    titles = [c["title"] for c in stored(page)]
    assert "Question 0" not in titles  # the oldest was dropped
    assert "Question 29" in titles


def test_corrupt_saved_data_is_ignored_and_the_app_still_works(chat, page):
    page.evaluate(f"localStorage.setItem('{KEY}', '{{not json')")
    page.reload()
    expect(page.locator(ROWS)).to_have_count(0)
    chat.reply(answer("Works."))
    ask(page, "Still here?")
    expect(page.locator(".msg.assistant .body")).to_have_text("Works.")
    assert len(stored(page)) == 1  # the bad data was replaced


def test_malformed_entries_are_skipped_but_good_ones_load(chat, page):
    seed(page, [fake_chat(1, updated=5), {"id": 7}, "junk", {"id": "x", "title": "t", "history": "nope"}])
    page.reload()
    expect(page.locator(ROWS)).to_have_text(["Question 1"])


def test_when_the_browser_will_not_save_the_chat_still_works_and_says_so(chat, page):
    page.evaluate("(() => { Storage.prototype.setItem = function () { throw new Error('quota'); }; })()")
    chat.reply(answer("Works."))
    ask(page, "Can't save this")
    expect(page.locator(".msg.assistant .body")).to_have_text("Works.")
    expect(page.locator("#save-warning")).to_be_visible()


def test_a_chat_saved_in_another_tab_shows_up_in_this_one(chat, page, base_url):
    other = page.context.new_page()
    other.route(
        "**/api/chat",
        lambda route: route.fulfill(status=200, content_type="text/event-stream", body=answer("Hi.")),
    )
    other.goto(base_url)
    ask(other, "From the other tab")
    expect(other.locator(ROWS)).to_have_count(1)
    expect(page.locator(ROWS)).to_have_text(["From the other tab"])
    other.close()


# ---- phone layout: the list is a drawer ----


def test_on_a_wide_screen_the_list_is_always_showing(chat, page):
    page.set_viewport_size({"width": 1280, "height": 800})
    expect(page.locator("#sidebar")).to_be_visible()
    expect(page.locator("#chats-toggle")).to_be_hidden()


def test_on_a_phone_the_list_is_a_drawer_that_opens_and_closes(chat, page):
    page.set_viewport_size({"width": 375, "height": 812})
    two_chats(chat, page)
    expect(page.locator("#sidebar")).to_be_hidden()
    page.locator("#chats-toggle").click()
    expect(page.locator("#sidebar")).to_be_visible()
    expect(page.locator("#chats-toggle")).to_have_attribute("aria-expanded", "true")
    page.locator(ROWS).nth(1).click()  # choosing a chat closes the drawer
    expect(page.locator("#sidebar")).to_be_hidden()
    expect(page.locator(".msg.user")).to_have_text("How's Nvidia?")

    page.locator("#chats-toggle").click()
    page.keyboard.press("Escape")
    expect(page.locator("#sidebar")).to_be_hidden()

    page.locator("#chats-toggle").click()
    page.locator("#close-sidebar").click()
    expect(page.locator("#sidebar")).to_be_hidden()

    page.locator("#chats-toggle").click()
    page.mouse.click(360, 400)  # outside the drawer, on the dimmed page
    expect(page.locator("#sidebar")).to_be_hidden()


def test_on_a_phone_the_page_never_scrolls_sideways(chat, page):
    page.set_viewport_size({"width": 375, "height": 812})
    two_chats(chat, page)
    page.locator("#chats-toggle").click()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
