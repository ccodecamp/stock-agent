"""Tests for the global-markets evals: the currency-aware graders, the non-US fixtures, and a contract
test that the fixtures have the same shape as the real tools. No network, no API key.

Each grader gets a known-good and a known-bad answer, because a grader that cannot fail is worse than none.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from evals import fixtures, graders
from evals.fixtures import FixtureTools
from evals.graders import Run
from evals.run_evals import load_cases, parse_check
from src import markets
from src.api import tools
from src.api.tools import SYMBOL_PATTERN
from src.data_fetcher import PriceHistory
from tests.test_indicators import _synthetic_df

# ---- helpers ----


def _calls(*calls: tuple[str, dict]):
    """Run the fixture tools for these (tool, input) pairs and return the recorded calls."""
    fixture = FixtureTools()
    for name, inp in calls:
        fixture(name, inp)
    return fixture.log


def _run(text, *calls, question="q"):
    return Run(question=question, text=text, tool_calls=_calls(*calls))


def _snap(symbol):
    return ("get_stock_snapshot", {"symbol": symbol})


# ---- reading amounts: which number, in which currency ----


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("It closed at $178.67.", [(178.67, "USD")]),
        ("Tencent is at HK$379.20.", [(379.2, "HKD")]),  # not also a bare $
        ("BHP is A$54.38 and CBA is AU$150.", [(54.38, "AUD"), (150.0, "AUD")]),
        ("DBS is S$39.70 or SG$40.", [(39.7, "SGD"), (40.0, "SGD")]),
        ("Toyota is ¥3,414.", [(3414.0, "¥")]),  # a bare yen sign could also be yuan
        ("Shell is £36.85.", [(36.85, "GBP")]),
        ("SAP is €209.65.", [(209.65, "EUR")]),
        ("Reliance is ₹1,368.70.", [(1368.7, "INR")]),
        ("PTT is ฿38.70.", [(38.7, "THB")]),
        ("BBCA is Rp 5,625.", [(5625.0, "IDR")]),
        ("Maybank is RM 9.98.", [(9.98, "MYR")]),
        ("Toyota closed at 3,414 yen.", [(3414.0, "JPY")]),
        ("About 100 pounds became 125 pounds.", [(100.0, "GBP"), (125.0, "GBP")]),
        ("Tencent is 376 Hong Kong dollars.", [(376.0, "HKD")]),  # not 376 plain dollars
        ("It is 100 dollars.", [(100.0, "USD")]),
        ("It is 52 US dollars.", [(52.0, "USD")]),
        ("DBS is 39.7 Singapore dollars.", [(39.7, "SGD")]),
        ("BHP is 54.38 Australian dollars.", [(54.38, "AUD")]),
        ("Enel is 7.42 euros.", [(7.42, "EUR")]),
        ("Reliance is 1,368 rupees.", [(1368.0, "INR")]),
        ("PTT is 38.7 baht.", [(38.7, "THB")]),
        ("It is 12 yuan.", [(12.0, "CNY")]),
        ("It rose 12% in 50 days, as of Nov 10.", []),  # a percentage and a count are not money
    ],
)
def test_amounts_are_read_with_their_currency(text, expected):
    assert graders._amounts(text) == expected or sorted(graders._amounts(text)) == sorted(expected)


def test_currency_names_are_found_with_or_without_an_amount():
    assert graders._currencies_named("Prices are in yen.") == {"JPY"}
    assert graders._currencies_named("It's in Hong Kong dollars.") == {"HKD"}
    assert graders._currencies_named("HK$376 and $178.67") == {"HKD", "USD"}
    assert graders._currencies_named("Tell us what you think.") == set()  # 'us' is not US dollars
    assert graders._currencies_named("rp 5 apples") == set()  # signs keep their case


def test_a_bare_yen_sign_counts_for_yen_or_yuan_but_not_for_anything_else():
    assert graders._covers("¥", {"JPY"}) and graders._covers("¥", {"CNY"})
    assert not graders._covers("¥", {"USD"})
    assert graders._named_in("JPY", {"¥"}) and graders._named_in("CNY", {"¥"})
    assert not graders._named_in("USD", {"¥"})


# ---- grounding with other currencies and index points ----


def test_grounding_accepts_yen_pounds_and_index_points_from_the_data():
    yen = _run("Toyota closed at ¥3,414, which is 3.4% above its typical price.", _snap("7203.T"))
    assert graders.numbers_grounded(yen).passed
    pounds = _run("100 pounds would be about 125 pounds now.", ("get_price_trend", {"symbol": "SHEL.L"}))
    assert graders.numbers_grounded(pounds).passed
    dow = _run("The Dow closed at 51,608 points.", _snap("^DJI"))
    assert graders.numbers_grounded(dow).passed


@pytest.mark.parametrize(
    ("text", "symbol", "bad"),
    [
        ("Toyota closed at ¥3,500.", "7203.T", 3500.0),
        ("Toyota closed at 3,500 yen.", "7203.T", 3500.0),
        ("Tencent is at HK$333.", "0700.HK", 333.0),
        ("It is at 40 euros.", "SAP.DE", 40.0),
        ("The Dow closed at 52,900 points.", "^DJI", 52900.0),
        ("Shell is at 50 pounds.", "SHEL.L", 50.0),
    ],
)
def test_grounding_catches_an_invented_amount_in_any_currency(text, symbol, bad):
    result = graders.numbers_grounded(_run(text, _snap(symbol)))
    assert not result.passed
    assert f"{bad:g}" in result.detail or str(bad) in result.detail


def test_grounding_accepts_a_fair_one_decimal_rounding_at_the_halfway_point():
    # The tool says 17.55; "17.6%" is a fair rounding. Float error used to reject it.
    run = _run("The Dow is about 17.6% above its 52-week low.", _snap("^DJI"))
    assert graders.numbers_grounded(run).passed
    assert not graders.numbers_grounded(_run("The Dow is about 17.7% above its low.", _snap("^DJI"))).passed


def test_grounding_still_ignores_counts_and_dates_that_are_not_money():
    run = _run("Over the last 50 trading days, as of Nov 11, it held steady.", _snap("7203.T"))
    assert graders.numbers_grounded(run).passed


def test_no_numbers_now_sees_other_currencies_and_points():
    assert graders.no_numbers(_run("I couldn't find that company.")).passed
    assert not graders.no_numbers(_run("It is 3,414 yen.")).passed
    assert not graders.no_numbers(_run("The Dow is at 51,608 points.")).passed
    assert not graders.no_numbers(_run("It is 12 euros.")).passed


# ---- currency_named ----


@pytest.mark.parametrize(
    "text",
    [
        "Toyota closed at 3,414 yen on Nov 11.",
        "Toyota closed at ¥3,414 on Nov 11.",
        "Toyota closed at 3,414 Japanese yen on Nov 11.",
        "Prices are in yen. Toyota closed at 3,414 on Nov 11.",
        "Toyota is about 3.4% above its typical price.",  # no price quoted, so nothing to label
    ],
)
def test_currency_named_accepts_the_stocks_own_currency(text):
    assert graders.currency_named(_run(text, _snap("7203.T"))).passed


@pytest.mark.parametrize(
    "text",
    [
        "Toyota closed at $3,414 on Nov 11.",
        "Toyota closed at 3,414 dollars on Nov 11.",
        "Toyota closed at 3,414 on Nov 11.",  # a price with no currency at all
        "Toyota closed at 3,414 euros on Nov 11.",
    ],
)
def test_currency_named_rejects_wrong_or_missing_currency(text):
    assert not graders.currency_named(_run(text, _snap("7203.T"))).passed


def test_currency_named_with_two_markets_needs_each_price_labelled():
    chart = ("get_price_chart", {"symbols": ["0700.HK", "NVDA"], "period": "6mo"})
    good = "Tencent went from HK$492.5 to HK$376. Nvidia went from $212.8 to $178.67."
    assert graders.currency_named(_run(good, chart)).passed
    # Dollars for the Hong Kong stock: the US dollar is in the data, so only the missing HK label gives it away.
    bad = "Tencent went from $492.5 to $376. Nvidia went from $212.8 to $178.67."
    result = graders.currency_named(_run(bad, chart))
    assert not result.passed and "HKD" in result.detail


def test_currency_named_does_not_blame_the_model_for_an_amount_the_user_wrote():
    run = _run(
        "Your 100 pounds would be about 125 pounds now.",
        ("get_price_trend", {"symbol": "SHEL.L"}),
        question="If I'd put 100 pounds into Shell a year ago, what would I have now?",
    )
    assert graders.currency_named(run).passed
    in_dollars = _run("Your 100 dollars would be 125 dollars.", ("get_price_trend", {"symbol": "SHEL.L"}))
    assert not graders.currency_named(in_dollars).passed


def test_bare_dollars_after_naming_singapore_dollars_is_not_us_dollars():
    said_once = "DBS closed at 39.70 Singapore dollars. Its 52-week high was 48.10 dollars."
    assert graders.currency_named(_run(said_once, _snap("D05.SI"))).passed
    # ...but never when the answer says US dollars, and never for a stock whose currency is not a dollar.
    us = "DBS closed at 39.70 Singapore dollars, or about 29 US dollars."
    assert not graders.currency_named(_run(us, _snap("D05.SI"))).passed
    assert not graders.currency_named(_run("Toyota closed at 3,414 yen, high 4,000 dollars.", _snap("7203.T"))).passed
    assert not graders.currency_named(_run("DBS closed at 39.70 dollars.", _snap("D05.SI"))).passed  # no Singapore


def test_an_example_in_the_currency_the_user_wrote_is_not_a_conversion_when_no_data_was_fetched():
    ask = "Toyota trades in Tokyo at 3,414 yen. What is that in US dollars?"
    refusal = "I can't convert that. I can show how 100 yen invested a year ago would have grown."
    assert graders.no_currency_conversion(Run(ask, refusal)).passed
    assert not graders.no_currency_conversion(Run(ask, "That comes to $22.76.")).passed  # dollars were not an amount
    assert not graders.no_currency_conversion(Run(ask, "It is about 22 euros.")).passed


def test_currency_named_fails_on_dollars_when_the_data_is_an_index():
    run = _run("The Dow closed at $51,608.", _snap("^DJI"))
    assert not graders.currency_named(run).passed


# ---- no_currency_conversion ----


@pytest.mark.parametrize(
    "text",
    [
        "I don't have an exchange rate, so I can't turn yen into dollars. Toyota's price is 3,414 yen.",
        "The exchange rate is not something I can look up.",
        "Toyota trades at 3,414 yen in Tokyo.",
    ],
)
def test_no_conversion_accepts_a_plain_refusal_or_the_stocks_own_currency(text):
    assert graders.no_currency_conversion(_run(text, _snap("7203.T"))).passed


@pytest.mark.parametrize(
    "text",
    [
        "That is about $22.76 at today's rate.",
        "At an exchange rate of 150 yen per dollar, that comes to 22 dollars.",
        "3,414 yen is equivalent to roughly 22 dollars.",
        "3,414 yen is 22.76 dollars.",
    ],
)
def test_no_conversion_catches_an_invented_exchange_rate(text):
    assert not graders.no_currency_conversion(_run(text, _snap("7203.T"))).passed


def test_a_real_us_listing_price_is_not_a_conversion():
    run = _run("The US shares (TM) closed at $223.45 on Nov 10.", _snap("TM"))
    assert graders.no_currency_conversion(run).passed


def test_an_amount_the_user_wrote_is_not_a_conversion():
    run = Run(question="Toyota is 3,414 yen. What is that in dollars?", text="3,414 yen is the price in Tokyo.")
    assert graders.no_currency_conversion(run).passed


# ---- index_in_points ----


@pytest.mark.parametrize(
    "text",
    [
        "The Dow closed at 51,608 points on Nov 10, about 5.7% below its high of 54,744 points.",
        "The Dow is down about 5.7% from its high.",  # no level quoted, so no unit needed
    ],
)
def test_index_in_points_accepts_points_or_no_level(text):
    assert graders.index_in_points(_run(text, _snap("^DJI"))).passed


@pytest.mark.parametrize(
    "text",
    [
        "The Dow closed at $51,608.",
        "The Dow closed at 51,608 dollars.",
        "The Dow closed at 51,608 on Nov 10.",  # a level with no unit
    ],
)
def test_index_in_points_rejects_money_and_a_missing_unit(text):
    assert not graders.index_in_points(_run(text, _snap("^DJI"))).passed


def test_index_in_points_ignores_runs_with_no_index_data():
    assert graders.index_in_points(_run("Toyota closed at 3,414 yen.", _snap("7203.T"))).passed


def test_index_in_points_covers_an_index_chart_summary():
    chart = ("get_price_chart", {"symbols": ["^DJI", "^N225"], "period": "6mo"})
    good = "The Dow ended near 51,608 points and the Nikkei near 58,956.67 points."
    assert graders.index_in_points(_run(good, chart)).passed
    assert not graders.index_in_points(_run("The Dow ended near $51,608.", chart)).passed


# ---- names_listing ----

TOYOTA = ["TM", "7203.T"]


@pytest.mark.parametrize(
    ("text", "symbol"),
    [
        ("Toyota's Tokyo listing closed at 3,414 yen.", "7203.T"),
        ("Toyota's Japanese shares closed at 3,414 yen.", "7203.T"),
        ("Toyota's US-listed shares closed at $223.45.", "TM"),
        ("Toyota's shares on the NYSE closed at $223.45.", "TM"),
    ],
)
def test_names_listing_accepts_saying_which_listing_was_used(text, symbol):
    assert graders.names_listing(_run(text, _snap(symbol)), TOYOTA).passed


@pytest.mark.parametrize(
    ("text", "symbol"),
    [
        ("Toyota closed at $223.45.", "TM"),
        ("Let us know. Toyota closed at $223.45.", "TM"),  # 'us' is not the US
        ("Toyota closed at 3,414 yen.", "7203.T"),
    ],
)
def test_names_listing_rejects_silently_picking_one(text, symbol):
    result = graders.names_listing(_run(text, _snap(symbol)), TOYOTA)
    assert not result.passed and symbol in result.detail


def test_names_listing_accepts_asking_which_one_when_nothing_was_fetched():
    ask = "Toyota trades in Tokyo and also in the US. Which one do you mean?"
    assert graders.names_listing(Run("q", ask), TOYOTA).passed
    assert not graders.names_listing(Run("q", "Toyota is a big carmaker in Tokyo."), TOYOTA).passed


# ---- never_fetches and declines_market ----


def test_never_fetches_catches_single_and_multi_symbol_calls():
    assert graders.never_fetches(_run("x", _snap("SHOP")), ["SHOP.TO"]).passed
    assert not graders.never_fetches(_run("x", _snap("SHOP.TO")), ["SHOP.TO"]).passed
    chart = ("get_price_chart", {"symbols": ["SHOP", "SHOP.TO"], "period": "6mo"})
    assert not graders.never_fetches(_run("x", chart), ["SHOP.TO"]).passed


TORONTO = {"place": "Toronto", "offers": ["US", "U.S.", "Nasdaq", "SHOP", "American"]}


@pytest.mark.parametrize(
    "text",
    [
        "I don't cover the Toronto listing of Shopify yet. I can tell you about the US version, SHOP.",
        "Toronto isn't one of the markets I cover. Shopify's Nasdaq shares are an option.",
        "I can't look up Toronto listings. I can check Shopify on the US market instead.",
    ],
)
def test_declines_market_accepts_a_plain_decline_with_an_alternative(text):
    assert graders.declines_market(Run("q", text), TORONTO).passed


@pytest.mark.parametrize(
    "text",
    [
        "Shopify in Toronto closed at 206.93 on Nov 10.",  # answers anyway
        "I don't cover that market. Try the US listing.",  # never says which place
        "I don't cover the Toronto market.",  # declines but offers nothing
        "Toronto is a lovely city. Tell us more.",  # no decline
    ],
)
def test_declines_market_rejects_answering_anyway_or_a_dead_end(text):
    assert not graders.declines_market(Run("q", text), TORONTO).passed


# ---- names_all_dates ----

MIXED = (("get_price_chart", {"symbols": ["0700.HK", "NVDA"], "period": "6mo"}),)


@pytest.mark.parametrize(
    "text",
    [
        "Tencent's data runs to Nov 11 and Nvidia's to Nov 10.",
        "Tencent is as of 2026-11-11, Nvidia as of 10 Nov.",
        "Hong Kong closed a day ahead of New York, so the dates differ.",
        "The two markets close at different times, so these are different days.",
    ],
)
def test_names_all_dates_accepts_naming_both_or_saying_they_differ(text):
    assert graders.names_all_dates(_run(text, *MIXED)).passed


def test_names_all_dates_rejects_one_date_for_two_markets():
    result = graders.names_all_dates(_run("Both are as of Nov 10.", *MIXED))
    assert not result.passed and "2026-11-11" in result.detail


def test_names_all_dates_needs_nothing_when_every_result_has_one_date():
    run = _run("Nothing about dates here.", ("get_price_chart", {"symbols": ["NVDA", "AAPL"], "period": "6mo"}))
    assert graders.names_all_dates(run).passed


# ---- the fixtures ----

NON_US = ["7203.T", "TM", "0700.HK", "SHEL.L", "RELIANCE.NS", "BHP.AX", "SAP.DE", "ENEL.MI", "D05.SI", "PTT.BK",
          "SHOP", "SHOP.TO", "^DJI", "^N225"]  # fmt: skip


def _out(name, inp):
    out, err = FixtureTools()(name, inp)
    return json.loads(out), err


@pytest.mark.parametrize("symbol", list(fixtures._STOCKS))
def test_every_fixture_symbol_is_one_the_real_tools_would_accept(symbol):
    import re

    assert re.fullmatch(SYMBOL_PATTERN, symbol)


@pytest.mark.parametrize("symbol", list(fixtures._STOCKS))
def test_fixture_numbers_are_self_consistent(symbol):
    _, closes, sma50, sma200, rsi, high, low, atr = fixtures._STOCKS[symbol]
    assert len(closes) == 12
    assert high >= max(closes) and low <= min(closes), "the 52-week range must contain every monthly close"
    assert 0 < rsi < 100 and atr > 0 and sma50 > 0 and sma200 > 0
    snap, _ = _out("get_stock_snapshot", {"symbol": symbol})
    assert snap["close"] == closes[-1]
    assert snap["pct_off_52w_high"] <= 0 <= snap["pct_above_52w_low"]
    trend, _ = _out("get_price_trend", {"symbol": symbol, "period": "1y"})
    assert trend["value_of_100"] == round(100 * trend["end_price"] / trend["start_price"], 2)
    assert "value_of_100_dollars" not in trend  # the old key would invite "dollars" for a yen stock


@pytest.mark.parametrize("symbol", list(fixtures._STOCKS))
def test_every_fixture_symbol_reports_the_market_and_currency_the_market_table_gives(symbol):
    snap, _ = _out("get_stock_snapshot", {"symbol": symbol})
    market = markets.lookup(symbol)
    assert snap["market"] == market.name
    if market.is_index:
        assert snap["currency"] is None and snap["kind"] == "index"
        assert snap["index_name"] and "points" in snap["note"]
    else:
        assert snap["currency"] == fixtures._CURRENCY_OVERRIDES.get(symbol, market.currency)
        assert "kind" not in snap


def test_the_currencies_are_the_ones_the_cases_expect():
    expected = {
        "7203.T": "JPY", "TM": "USD", "0700.HK": "HKD", "SHEL.L": "GBP", "RELIANCE.NS": "INR", "BHP.AX": "AUD",
        "SAP.DE": "EUR", "ENEL.MI": "EUR", "D05.SI": "SGD", "PTT.BK": "THB", "SHOP": "USD", "SHOP.TO": "CAD",
    }  # fmt: skip
    for symbol, currency in expected.items():
        assert _out("get_stock_snapshot", {"symbol": symbol})[0]["currency"] == currency, symbol


@pytest.mark.parametrize("symbol", list(fixtures._STOCKS))
def test_every_date_in_a_fixture_agrees_and_nothing_is_dated_after_the_data_ends(symbol):
    end = fixtures.ASIA_END_DATE if symbol in fixtures._END_DATES else fixtures.END_DATE
    assert _out("get_stock_snapshot", {"symbol": symbol})[0]["as_of"] == end
    assert _out("get_price_trend", {"symbol": symbol})[0]["end_date"] == end
    for period in ("3mo", "6mo", "1y"):
        (stock,) = _out("get_price_chart", {"symbols": [symbol], "period": period})[0]["stocks"]
        assert stock["end_date"] == end
        assert stock["high_date"] <= end and stock["low_date"] <= end, (symbol, period)
        assert stock["market"] and "currency" in stock


def test_asia_is_a_day_ahead_of_europe_and_the_us_in_the_data():
    assert fixtures.ASIA_END_DATE > fixtures.END_DATE
    assert set(fixtures._END_DATES) == {"7203.T", "0700.HK", "RELIANCE.NS", "BHP.AX", "D05.SI", "PTT.BK", "^N225"}


def test_find_company_shows_both_toyota_listings_and_the_one_we_do_not_cover():
    toyota, err = _out("find_company", {"query": "Toyota"})
    assert not err
    assert [(m["symbol"], m["exchange"], m["market"]) for m in toyota["matches"]] == [
        ("TM", "NYSE", "US"),
        ("7203.T", "Tokyo", "Tokyo"),
    ]
    shopify, _ = _out("find_company", {"query": "Shopify"})
    assert [(m["symbol"], m["market"]) for m in shopify["matches"]] == [("SHOP", "US"), ("SHOP.TO", "unsupported")]


def test_find_company_finds_indexes_and_exact_tickers_and_still_misses_unknown_names():
    dow, _ = _out("find_company", {"query": "Dow Jones"})
    assert [(m["symbol"], m["type"]) for m in dow["matches"]] == [("^DJI", "INDEX")]
    assert _out("find_company", {"query": "7203.T"})[0]["matches"][0]["symbol"] == "7203.T"
    assert _out("find_company", {"query": "Blorptech"})[0]["matches"] == []
    nikkei, _ = _out("find_company", {"query": "nikkei"})
    assert nikkei["matches"][0]["symbol"] == "^N225"  # and not Nike: 'nike' is not inside 'nikkei'


def test_the_unsupported_toronto_listing_still_returns_data_so_a_wrong_fetch_is_possible():
    snap, err = _out("get_stock_snapshot", {"symbol": "SHOP.TO"})
    assert not err and (snap["market"], snap["currency"]) == ("unsupported", "CAD")


def test_the_chart_fixture_handles_a_mix_of_stocks_and_indexes():
    out, err = _out("get_price_chart", {"symbols": ["^DJI", "^N225", "7203.T"], "period": "6mo"})
    assert not err
    assert [s["currency"] for s in out["stocks"]] == [None, None, "JPY"]
    assert [s["end_date"] for s in out["stocks"]] == ["2026-11-10", "2026-11-11", "2026-11-11"]


# ---- the fixtures have the same shape as the real tools ----


@pytest.fixture
def real_tools(monkeypatch):
    def fake_fetch(symbol, lookback_years=2):
        return PriceHistory(symbol=symbol, df=_synthetic_df(n=520), fetched_at=datetime.now(UTC))

    tools.clear_cache()
    monkeypatch.setattr(tools, "fetch_history", fake_fetch)
    yield
    tools.clear_cache()


def _real(name, inp):
    content, is_error = tools.execute_tool(name, inp)
    assert not is_error, content
    return json.loads(content)


@pytest.mark.parametrize("symbol", ["NVDA", "7203.T", "SHEL.L", "^DJI", "SHOP.TO"])
def test_fixture_results_have_exactly_the_fields_the_real_tools_return(real_tools, symbol):
    for name, extra in (
        ("get_stock_snapshot", {}),
        ("get_price_trend", {"period": "1y"}),
        ("run_backtest", {"strategy": "dip_buying"}),
    ):
        if name == "run_backtest" and symbol not in fixtures._BACKTESTS:
            continue
        real = _real(name, {"symbol": symbol, **extra})
        fake, err = _out(name, {"symbol": symbol, **extra})
        assert not err
        assert set(fake) == set(real), (name, symbol, set(fake) ^ set(real))


@pytest.mark.parametrize("symbols", [["NVDA"], ["7203.T", "NVDA"], ["^DJI", "^N225"]])
def test_fixture_chart_matches_the_real_chart_summary(real_tools, symbols):
    real = _real("get_price_chart", {"symbols": symbols, "period": "6mo"})
    fake, err = _out("get_price_chart", {"symbols": symbols, "period": "6mo"})
    assert not err
    assert set(fake) == set(real) - {"chart"}  # the page-only series never reaches the model
    assert set(fake["stocks"][0]) == set(real["stocks"][0])


def test_fixture_find_company_matches_the_real_search_shape(monkeypatch):
    class FakeSearch:
        def __init__(self, query, max_results):
            self.quotes = [{"symbol": "7203.T", "longname": "Toyota", "exchDisp": "Tokyo", "quoteType": "EQUITY"}]

    monkeypatch.setattr(tools.yf, "Search", FakeSearch)
    real = json.loads(tools.execute_tool("find_company", {"query": "Toyota"})[0])
    fake, _ = _out("find_company", {"query": "Toyota"})
    assert set(fake) == set(real)
    assert set(fake["matches"][0]) == set(real["matches"][0])


# ---- the case file ----


def _case_symbols(case):
    for spec in case.get("checks", []):
        name, arg = parse_check(spec)
        if name == "covers_symbols":
            yield from arg if isinstance(arg, list) else [arg]
        elif name == "tool_input" and arg["field"] == "symbol":
            yield arg["value"]
        elif name == "names_listing":
            yield from arg


def test_every_symbol_a_case_expects_has_fixture_data():
    # The old non_us_listing case expected a Tokyo answer, but the fixtures had no Tokyo data, so it
    # passed by "not finding" the stock. A case that expects a symbol must be able to get it.
    for case in load_cases():
        for symbol in _case_symbols(case):
            assert symbol in fixtures._STOCKS, f"{case['id']}: no fixture for {symbol}"


def test_the_weak_non_us_case_is_gone_and_the_new_cases_cover_every_market_we_added():
    cases = {c["id"]: c for c in load_cases()}
    assert "non_us_listing" not in cases
    covered = {s for c in cases.values() for s in _case_symbols(c)}
    for symbol in ("7203.T", "0700.HK", "SHEL.L", "RELIANCE.NS", "SAP.DE", "D05.SI", "PTT.BK", "^DJI", "^N225"):
        assert symbol in covered, symbol


def test_cases_that_are_about_an_unsupported_market_or_a_conversion_use_the_matching_checks():
    cases = {c["id"]: [parse_check(s)[0] for s in c["checks"]] for c in load_cases() if "checks" in c}
    assert {"never_fetches", "declines_market"} <= set(cases["shopify_in_toronto"])
    assert "no_currency_conversion" in cases["yen_to_dollars"]
    assert "index_in_points" in cases["dow_in_points"]
    assert "names_listing" in cases["toyota_which_listing"]
    assert "names_all_dates" in cases["tencent_vs_nvidia"]


def test_every_new_grader_is_registered():
    for name in (
        "currency_named", "no_currency_conversion", "index_in_points", "names_listing", "never_fetches",
        "declines_market", "names_all_dates",
    ):  # fmt: skip
        assert name in graders.CHECKS
