"""Which market a ticker belongs to and what it is measured in. No network."""

from __future__ import annotations

import pytest

from src import markets
from src.api.chat import SYSTEM_PROMPT
from src.markets import INDEXES, SUFFIXES, lookup, to_main_unit


@pytest.mark.parametrize(
    ("symbol", "name", "currency"),
    [
        ("NVDA", "US", "USD"),
        ("BRK-B", "US", "USD"),
        ("BRK.B", "US", "USD"),  # a one-letter ending is a US share class, not a market
        ("7203.T", "Tokyo", "JPY"),
        ("0700.HK", "Hong Kong", "HKD"),
        ("600519.SS", "Shanghai", "CNY"),
        ("000001.SZ", "Shenzhen", "CNY"),
        ("RELIANCE.NS", "India (NSE)", "INR"),
        ("RELIANCE.BO", "India (BSE)", "INR"),
        ("BHP.AX", "Australia (ASX)", "AUD"),
        ("SHEL.L", "London", "GBP"),
        ("SAP.DE", "Germany (Xetra)", "EUR"),
        ("MC.PA", "Paris", "EUR"),
        ("ENEL.MI", "Milan", "EUR"),
        ("D05.SI", "Singapore", "SGD"),
        ("PTT.BK", "Thailand", "THB"),
        ("BBCA.JK", "Indonesia", "IDR"),
        ("1155.KL", "Malaysia", "MYR"),
        ("shel.l", "London", "GBP"),  # case does not matter
    ],
)
def test_stocks_are_placed_by_their_ending(symbol, name, currency):
    market = lookup(symbol)
    assert (market.name, market.currency, market.is_index) == (name, currency, False)


@pytest.mark.parametrize("symbol", sorted(INDEXES))
def test_every_known_index_has_a_name_and_no_currency(symbol):
    market = lookup(symbol)
    assert market.is_index
    assert market.currency is None
    assert market.index_name


def test_an_index_whose_ticker_ends_like_a_stock_is_still_an_index():
    # FTSEMIB.MI ends in .MI (Milan) and 000001.SS ends in .SS (Shanghai); 000001.SZ is a bank.
    assert lookup("FTSEMIB.MI").is_index
    assert lookup("000001.SS").is_index
    assert not lookup("000001.SZ").is_index


def test_the_dow_is_a_us_index_measured_in_points():
    dow = lookup("^DJI")
    assert (dow.name, dow.index_name, dow.currency) == ("US", "Dow Jones Industrial Average", None)


def test_an_unknown_index_is_still_treated_as_points():
    market = lookup("^KS11")
    assert market.is_index and market.currency is None


@pytest.mark.parametrize("symbol", ["SHOP.TO", "005930.KS", "ABC.XX"])
def test_other_markets_are_flagged_as_unsupported_rather_than_guessed_as_us_dollars(symbol):
    market = lookup(symbol)
    assert market.name == "unsupported"
    assert market.currency is None


@pytest.mark.parametrize(("code", "expected"), [
    ("GBp", ("GBP", 100)),
    ("ZAc", ("ZAR", 100)),
    ("ILA", ("ILS", 100)),
    ("GBP", ("GBP", 1)),
    ("JPY", ("JPY", 1)),
    (None, (None, 1)),
])
def test_pence_cents_and_agorot_are_converted_to_the_main_unit(code, expected):
    assert to_main_unit(code) == expected


def test_every_ending_and_index_is_in_the_system_prompt():
    # If a market is added here but not to the prompt, Claude would not know to offer it.
    for ending in SUFFIXES:
        assert ending in SYSTEM_PROMPT, ending
    for symbol in INDEXES:
        assert symbol in SYSTEM_PROMPT, symbol


def test_every_currency_is_a_real_three_letter_code():
    for _, currency in SUFFIXES.values():
        assert len(currency) == 3 and currency.isupper()
    assert markets.MINOR_UNITS["GBp"] == ("GBP", 100)
