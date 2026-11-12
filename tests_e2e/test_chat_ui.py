"""Browser tests for the chat page, against a stubbed /api/chat (see conftest.py)."""

from __future__ import annotations

import pytest
from playwright.sync_api import Page, expect

from .conftest import answer, sse

CHIPS = [
    "How's Nvidia doing?",
    "If I'd put $100 into Apple a year ago, what would I have now?",
    "Does buying Tesla after a big drop usually work?",
    "How has Nike done over the last 6 months?",
]


def ask(page: Page, text: str):
    page.locator("#input").fill(text)
    page.locator("#send").click()


def test_empty_state_shows_four_chips_and_no_new_chat_button(chat, page):
    expect(page.locator(".chip")).to_have_text(CHIPS)
    expect(page.locator("#new-chat")).to_be_hidden()
    expect(page.locator("#empty")).to_be_visible()


def test_clicking_a_chip_sends_its_exact_text(chat, page):
    chat.reply(answer("Fine."))
    page.locator(".chip").first.click()
    expect(page.locator(".msg.user")).to_have_text(CHIPS[0])
    expect(page.locator("#empty")).to_be_hidden()
    assert chat.requests[0]["messages"][-1] == {"role": "user", "content": CHIPS[0]}


def test_enter_sends_and_shift_enter_adds_a_newline(chat, page):
    chat.reply(answer("Okay."))
    box = page.locator("#input")
    box.fill("line one")
    box.press("Shift+Enter")
    box.type("line two")
    assert len(chat.requests) == 0
    box.press("Enter")
    expect(page.locator(".msg.user")).to_have_text("line one\nline two")
    assert len(chat.requests) == 1


def test_empty_message_is_not_sent(chat, page):
    page.locator("#input").fill("   ")
    page.locator("#send").click()
    page.locator("#input").press("Enter")
    assert chat.requests == []


def test_streamed_text_replaces_thinking_indicator(chat, page):
    chat.reply(answer("Nvidia is up a bit this year."))
    ask(page, "hi")
    expect(page.locator(".msg.assistant .body")).to_have_text("Nvidia is up a bit this year.")
    expect(page.locator(".typing")).to_have_count(0)


def test_tool_steps_show_friendly_text_and_ok_or_fail_state(chat, page):
    chat.reply(
        answer(
            "Done.",
            tools=(
                ("find_company", {"query": "Nvidia"}, True),
                ("get_stock_snapshot", {"symbol": "NVDA"}, False),
            ),
        )
    )
    ask(page, "How's Nvidia?")
    steps = page.locator(".msg.assistant .steps li")
    expect(steps).to_have_text(["Looking up “Nvidia”", "Checking NVDA's latest numbers"])
    expect(steps.nth(0)).to_have_class("ok")
    expect(steps.nth(1)).to_have_class("fail")


def test_history_from_done_event_is_sent_back_next_turn(chat, page):
    chat.reply(answer("First answer."), answer("Second answer."))
    ask(page, "one")
    expect(page.locator(".msg.assistant .body").first).to_have_text("First answer.")
    ask(page, "two")
    expect(page.locator(".msg.assistant .body").nth(1)).to_have_text("Second answer.")
    roles = [m["role"] for m in chat.requests[1]["messages"]]
    assert roles == ["user", "assistant", "user"]
    assert chat.requests[1]["messages"][0]["content"] == "one"


def test_send_button_is_disabled_while_a_reply_is_pending(chat, page):
    release = []

    def slow(route):
        release.append(route)

    page.unroute("**/api/chat")
    page.route("**/api/chat", slow)
    ask(page, "hi")
    expect(page.locator("#send")).to_be_disabled()
    expect(page.locator(".typing")).to_be_visible()
    release[0].fulfill(status=200, content_type="text/event-stream", body=answer("Back."))
    expect(page.locator("#send")).to_be_enabled()


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        ((429, "{}"), "You're sending questions quickly. Wait a minute and try again."),
        ((422, '{"detail":[{"msg":"List should have at most 40 items: too long"}]}'), "This chat has got long."),
        ((422, '{"detail":"nope"}'), "That message couldn't be sent. Try rephrasing it."),
        ((500, "boom"), "The server had a problem (500). Try again in a moment."),
        (
            sse({"type": "error", "message": "The AI service returned an error. Please try again."}),
            "The AI service returned an error. Please try again.",
        ),
        (sse({"type": "text", "text": "partial"}), "The connection dropped before the answer finished."),
    ],
    ids=["429", "422-too-long", "422-other", "500", "sse-error-event", "dropped-stream"],
)
def test_errors_are_shown_in_plain_words(chat, page, reply, message):
    chat.reply(reply)
    ask(page, "hi")
    expect(page.locator(".msg.assistant .error")).to_contain_text(message)
    expect(page.locator("#send")).to_be_enabled()


def test_failed_turn_does_not_poison_the_history(chat, page):
    chat.reply((500, "boom"), answer("Recovered."))
    ask(page, "first")
    expect(page.locator(".error")).to_be_visible()
    ask(page, "second")
    expect(page.locator(".msg.assistant .body").nth(1)).to_have_text("Recovered.")
    assert [m["content"] for m in chat.requests[1]["messages"]] == ["second"]


def test_new_chat_clears_the_log_and_the_history(chat, page):
    chat.reply(answer("Hello."))
    ask(page, "one")
    expect(page.locator(".msg.assistant .body")).to_have_text("Hello.")
    page.locator("#new-chat").click()
    expect(page.locator(".msg")).to_have_count(0)
    expect(page.locator("#empty")).to_be_visible()
    expect(page.locator("#new-chat")).to_be_hidden()
    ask(page, "fresh")
    assert [m["content"] for m in chat.requests[1]["messages"]] == ["fresh"]


def test_model_output_cannot_inject_html(chat, page):
    chat.reply(answer("<img src=x onerror=window.pwned=1> and <b>bold</b>"))
    ask(page, "hi")
    expect(page.locator(".msg.assistant .body")).to_contain_text("<b>bold</b>")
    assert page.evaluate("window.pwned") is None


def test_mobile_layout_has_no_horizontal_scroll_and_composer_visible(chat, page):
    chat.reply(answer("word " * 200))
    page.set_viewport_size({"width": 375, "height": 812})
    ask(page, "A fairly long question " * 5)
    expect(page.locator(".msg.assistant .body")).to_contain_text("word")
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    expect(page.locator("#composer")).to_be_in_viewport()


# ---- charts ----


def _series(symbol: str, start: float, step: float, days: int = 60) -> dict:
    from datetime import date, timedelta

    first = date(2026, 8, 17)
    return {
        "symbol": symbol,
        "dates": [(first + timedelta(days=i)).isoformat() for i in range(days)],
        "closes": [round(start + step * i + (i % 7) * 0.6, 2) for i in range(days)],
    }


def chart_answer(series: list[dict], text: str = "Here is the chart.") -> str:
    return sse(
        {"type": "tool_call", "name": "get_price_chart", "input": {"symbols": [s["symbol"] for s in series]}},
        {"type": "tool_result", "name": "get_price_chart", "ok": True},
        {"type": "chart", "period": "3mo", "series": series},
        {"type": "text", "text": text},
        {"type": "done", "messages": [{"role": "assistant", "content": [{"type": "text", "text": text}]}]},
    )


def test_single_stock_chart_is_drawn_above_the_answer(chat, page):
    chat.reply(chart_answer([_series("NVDA", 170, 0.5)], "NVDA rose over the period."))
    ask(page, "Show me a graph of Nvidia")
    expect(page.locator(".chart svg polyline.line")).to_have_count(1)
    expect(page.locator(".chart .legend li")).to_have_count(1)
    expect(page.locator(".chart .legend")).to_contain_text("NVDA")
    expect(page.locator(".chart .legend")).to_contain_text("+")
    expect(page.locator(".steps li")).to_have_text(["Drawing a chart of NVDA"])
    # The chart sits between the status lines and the text answer.
    order = page.evaluate("[...document.querySelector('.msg.assistant').children].map(c => c.className.split(' ')[0])")
    assert order == ["steps", "chart", "body"]
    expect(page.locator(".msg.assistant .body")).to_have_text("NVDA rose over the period.")


def test_comparison_chart_rebases_every_line_to_100(chat, page):
    chat.reply(chart_answer([_series("AAPL", 200, 0.4), _series("MSFT", 450, -0.3), _series("TSLA", 300, 1.0)]))
    ask(page, "Compare Apple, Microsoft and Tesla")
    expect(page.locator(".chart svg polyline.line")).to_have_count(3)
    expect(page.locator(".chart .legend li")).to_have_count(3)
    expect(page.locator(".chart figcaption")).to_contain_text("starts at 100")
    # Rebased axis: plain numbers around 100, not dollar prices.
    ticks = page.locator(".chart .tick").all_text_contents()
    assert not any(t.startswith("$") for t in ticks)
    expect(page.locator(".chart .legend li").nth(1)).to_contain_text("-")  # MSFT fell


def test_chart_with_no_usable_data_is_skipped_and_the_answer_still_shows(chat, page):
    chat.reply(chart_answer([{"symbol": "NVDA", "dates": [], "closes": []}], "No chart, but here is text."))
    ask(page, "graph it")
    expect(page.locator(".msg.assistant .body")).to_have_text("No chart, but here is text.")
    expect(page.locator(".chart")).to_have_count(0)


def test_chart_fits_a_phone_screen(chat, page):
    chat.reply(chart_answer([_series("AAPL", 200, 0.4), _series("MSFT", 450, -0.3)]))
    page.set_viewport_size({"width": 375, "height": 812})
    ask(page, "Compare Apple and Microsoft")
    expect(page.locator(".chart svg")).to_be_visible()
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    box = page.locator(".chart svg").bounding_box()
    assert box["width"] > 250


def test_hostile_symbol_text_is_not_rendered_as_html(chat, page):
    chat.reply(chart_answer([_series("<img src=x onerror=window.pwned=1>", 100, 0.2)]))
    ask(page, "graph")
    expect(page.locator(".chart .legend")).to_contain_text("<img")
    assert page.evaluate("window.pwned") is None
