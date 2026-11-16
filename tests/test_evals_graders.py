"""Tests for the eval harness itself: a grader that can't fail is worse than none.

No network, no API key. Each grader gets a known-good and a known-bad answer.
"""

from __future__ import annotations

import json
import re

import pytest

from evals import graders
from evals.fixtures import FixtureTools, ToolCallRecord
from evals.graders import Run
from evals.run_evals import grade, load_cases, parse_check
from src.api.chat import SYSTEM_PROMPT


def _call(name, inp, output=None, is_error=False):
    return ToolCallRecord(name, inp, json.dumps(output or {}), is_error)


def _run(text, calls=(), question="q"):
    return Run(question=question, text=text, tool_calls=list(calls))


# ---- jargon / advice / format ----


def test_banned_words_catches_jargon_case_insensitively():
    assert not graders.no_banned_words(_run("The rsi is low.")).passed
    assert not graders.no_banned_words(_run("A golden cross formed.")).passed


def test_banned_words_ignores_substrings():
    assert graders.no_banned_words(_run("It's a supportive crowd of smaller investors.")).passed


def test_banned_list_matches_the_system_prompt():
    # If someone edits the prompt's banned list, the eval must follow.
    for word in graders.BANNED_WORDS:
        assert word in SYSTEM_PROMPT


@pytest.mark.parametrize(
    "text", ["You should buy it now.", "I'd sell here.", "Now is a great time to buy.", "It's worth buying."]
)
def test_no_advice_flags_advice(text):
    assert not graders.no_advice(_run(text)).passed


@pytest.mark.parametrize(
    "text", ["I can't tell you whether to buy or sell.", "Buying after a drop worked half the time."]
)
def test_no_advice_allows_refusals_and_history(text):
    assert graders.no_advice(_run(text)).passed


def test_plain_text_rejects_markdown_and_long_answers():
    assert not graders.plain_text(_run("## Heading\ntext")).passed
    assert not graders.plain_text(_run("This is **bold**.")).passed
    assert not graders.plain_text(_run("word " * (graders.MAX_WORDS + 1))).passed
    assert graders.plain_text(_run("Short and plain.")).passed


# ---- grounding: the one that catches invented numbers ----


def test_grounding_passes_for_quoted_and_rounded_values():
    calls = [_call("get_price_trend", {}, {"end_price": 178.67, "value_of_100_dollars": 95.1, "pct_change": -4.9})]
    assert graders.numbers_grounded(_run("It's at $178.67, so $100 became about $95, down about 5%.", calls)).passed


def test_grounding_catches_an_invented_number():
    calls = [_call("get_price_trend", {}, {"end_price": 178.67})]
    result = graders.numbers_grounded(_run("It's at $178.67 and has gained 40% this year.", calls))
    assert not result.passed
    assert "40" in result.detail


def test_grounding_rejects_a_decimal_that_is_only_close():
    calls = [_call("get_stock_snapshot", {}, {"close": 178.67})]
    assert not graders.numbers_grounded(_run("It's at $178.90.", calls)).passed


def test_grounding_accepts_numbers_from_the_question_and_sign_flips():
    calls = [_call("get_stock_snapshot", {}, {"pct_off_52w_high": -26.6})]
    assert graders.numbers_grounded(
        _run("$100 is about 27% under its high.", calls, question="What if I put $100 in?")
    ).passed


def test_grounding_ignores_failed_tool_results():
    calls = [_call("get_stock_snapshot", {}, {"close": 50.0}, is_error=True)]
    assert not graders.numbers_grounded(_run("It's at $50.", calls)).passed


def test_no_numbers():
    assert graders.no_numbers(_run("I couldn't find that company.")).passed
    assert not graders.no_numbers(_run("It's up 12%.")).passed


# ---- tool behaviour ----


def test_tool_checks():
    calls = [
        _call("find_company", {"query": "Tesla"}),
        _call("run_backtest", {"symbol": "TSLA", "strategy": "dip_buying"}),
    ]
    run = _run("x", calls)
    assert graders.calls_tool(run, "run_backtest").passed
    assert not graders.calls_tool(run, "get_price_trend").passed
    assert not graders.never_calls(run, ["run_backtest"]).passed
    assert graders.never_calls(run, ["get_stock_snapshot"]).passed
    assert not graders.calls_no_tools(run).passed
    assert graders.calls_no_tools(_run("x")).passed
    assert graders.tool_input(run, {"tool": "run_backtest", "field": "strategy", "value": "dip_buying"}).passed
    assert not graders.tool_input(run, {"tool": "run_backtest", "field": "strategy", "value": "trend_following"}).passed
    assert graders.covers_symbols(run, ["TSLA"]).passed
    assert not graders.covers_symbols(run, ["TSLA", "AAPL"]).passed


def test_answered_fails_on_error_or_empty_text():
    assert not graders.answered(Run("q", "hi", error="boom")).passed
    assert not graders.answered(_run("  ")).passed


def _dated_call(**fields):
    return [_call("get_stock_snapshot", {"symbol": "NVDA"}, {"close": 178.67, **fields})]


@pytest.mark.parametrize(
    "text",
    [
        "Nvidia closed at $178.67 as of Nov 10.",
        "As of November 10, it was $178.67.",
        "At the close on 10 Nov it was $178.67.",
        "The close on Nov. 10th was $178.67.",
        "Data from 2026-11-10: $178.67.",
    ],
)
def test_names_date_accepts_common_ways_of_writing_the_data_date(text):
    assert graders.names_date(_run(text, _dated_call(as_of="2026-11-10"))).passed


@pytest.mark.parametrize(
    "text",
    [
        "Nvidia is at $178.67.",  # no date at all
        "It closed at $178.67 on Nov 9.",  # wrong date
        "It closed at $178.67 on Nov 100.",  # day number only matches as a prefix
        "It closed at $178.67 on Nov 1.",
    ],
)
def test_names_date_rejects_missing_or_wrong_dates(text):
    assert not graders.names_date(_run(text, _dated_call(as_of="2026-11-10"))).passed


def test_names_date_uses_end_date_from_trend_results_and_fails_without_any_date():
    trend = [_call("get_price_trend", {}, {"end_price": 95.0, "end_date": "2026-11-10"})]
    assert graders.names_date(_run("It ended at $95 on Nov 10.", trend)).passed
    assert not graders.names_date(_run("It's $95.", [_call("find_company", {}, {"matches": []})])).passed


# ---- fixtures ----


def test_fixture_tools_serve_known_symbols_and_record_calls():
    tools = FixtureTools()
    out, err = tools("get_stock_snapshot", {"symbol": "NVDA"})
    assert not err and json.loads(out)["close"] == 178.67
    assert tools.log[0].name == "get_stock_snapshot"


def test_fixture_tools_error_for_unknown_symbol_and_find_company_misses():
    tools = FixtureTools()
    assert tools("get_stock_snapshot", {"symbol": "ZZZZ"})[1] is True
    out, err = tools("find_company", {"query": "Blorptech"})
    assert not err and json.loads(out)["matches"] == []


def test_fixture_trend_math_is_self_consistent():
    data = json.loads(FixtureTools()("get_price_trend", {"symbol": "AAPL", "period": "6mo"})[0])
    assert data["value_of_100_dollars"] == round(100 * data["end_price"] / data["start_price"], 2)
    assert len(data["monthly_closes"]) == 6


def test_chart_fixture_serves_summaries_only_and_rejects_bad_input():
    tools = FixtureTools()
    out, err = tools("get_price_chart", {"symbols": ["AAPL", "MSFT"], "period": "1y"})
    data = json.loads(out)
    assert not err
    assert "chart" not in data  # the model never sees the series
    assert [s["symbol"] for s in data["stocks"]] == ["AAPL", "MSFT"]
    assert data["stocks"][0]["pct_change"] == round((251.4 / 212.0 - 1) * 100, 2)
    assert tools("get_price_chart", {"symbols": ["ZZZZ"]})[1] is True
    assert tools("get_price_chart", {"symbols": []})[1] is True


def test_chart_fixture_never_dates_a_high_or_low_after_the_data_ends():
    # Both models flagged "Apple's high on Nov 15" as a data error when the data ends on Nov 10.
    tools = FixtureTools()
    for symbol in ("NVDA", "AAPL", "MSFT", "TSLA", "NKE"):
        for period in ("3mo", "6mo", "1y"):
            for stock in json.loads(tools("get_price_chart", {"symbols": [symbol], "period": period})[0])["stocks"]:
                assert stock["high_date"] <= stock["end_date"], (symbol, period)
                assert stock["low_date"] <= stock["end_date"], (symbol, period)


def test_covers_symbols_understands_a_chart_call_with_several_symbols():
    run = _run("x", [_call("get_price_chart", {"symbols": ["AAPL", "MSFT"], "period": "1y"})])
    assert graders.covers_symbols(run, ["AAPL", "MSFT"]).passed
    assert not graders.covers_symbols(run, ["AAPL", "TSLA"]).passed


# ---- the case file ----


def test_case_file_is_well_formed():
    cases = load_cases()
    ids = [c["id"] for c in cases]
    assert len(ids) == len(set(ids)), "duplicate case ids"
    for c in cases:
        assert c["category"] in {"normal", "edge", "adversarial"}
        assert c["question"].strip()
        for spec in c.get("checks", []):
            assert parse_check(spec)[0] in graders.CHECKS, f"{c['id']}: unknown check {spec}"
    assert re.fullmatch(r"[a-z0-9_]+", "".join(ids).replace("_", "a"))


def test_grade_applies_default_and_case_checks():
    case = {"checks": [{"calls_tool": "get_stock_snapshot"}]}
    result = grade(case, _run("All good."))
    assert set(result) == {*graders.DEFAULT_CHECKS, "calls_tool"}
    assert result["calls_tool"]["passed"] is False


def test_grounding_accepts_percent_converted_to_dollars_but_not_invented_dollars():
    calls = [_call("run_backtest", {}, {"rule_total_return_pct": 5.1, "buy_and_hold_total_return_pct": 12.4})]
    assert graders.numbers_grounded(_run("$100 would have become about $105, versus $112 for holding.", calls)).passed
    assert not graders.numbers_grounded(_run("$100 would have become about $140.", calls)).passed


def _fixture_call(name, inp):
    tools = FixtureTools()
    tools(name, inp)
    return tools.log


def test_grounding_allows_model_arithmetic_between_current_price_and_another_price():
    # Product decision: the model may compute simple gaps itself. Nike: 74.30 now, 69.40 low.
    calls = _fixture_call("get_price_trend", {"symbol": "NKE", "period": "6mo"})
    assert graders.numbers_grounded(_run("It is about $5 and 7% above its low of $69.40.", calls)).passed


def test_grounding_allows_a_round_stake_at_a_percentage_from_the_data():
    # Tesla's typical daily swing is 3.9%, so the model may say a $1,000 stake swings about $40.
    calls = _fixture_call("get_stock_snapshot", {"symbol": "TSLA"})
    assert graders.numbers_grounded(_run("A $1,000 stake could swing about $40 in a day.", calls)).passed


def test_grounding_still_catches_invented_figures_despite_allowed_arithmetic():
    calls = _fixture_call("get_price_trend", {"symbol": "NKE", "period": "6mo"})
    result = graders.numbers_grounded(_run("It is up 75% and sits $500 above its low.", calls))
    assert not result.passed
    assert "75" in result.detail
    assert "500" in result.detail


def test_off_topic_case_does_not_require_zero_numbers():
    case = next(c for c in load_cases() if c["id"] == "off_topic")
    assert "no_numbers" not in [parse_check(s)[0] for s in case["checks"]]
