# Evals

Tests for the chat agent's *behaviour*. The real Claude runs against frozen fake tool data
(`fixtures.py`), so we know exactly which numbers a correct answer may quote.

```bash
python -m evals.run_evals                          # all cases, 3 runs each (costs API money)
python -m evals.run_evals --case off_topic --runs 1
pytest tests/test_evals_graders.py tests/test_evals_global_markets.py   # tests of the graders and fixtures (free)
```

| File | Role |
|---|---|
| `cases.yaml` | Questions plus the checks each answer must pass |
| `fixtures.py` | Frozen tool data (invented, not market data) |
| `graders.py` | Deterministic checks: jargon, advice, markdown, invented numbers, tool choice |
| `run_evals.py` | Runs cases, prints pass rates and cost, writes `results/latest.json` (git-ignored) |
| `usage.py` | Counts tokens and dollars per run (wraps the API client; app code is untouched) |

Every case gets the default checks (`answered`, `no_banned_words`, `no_advice`, `plain_text`,
`numbers_grounded`) plus any it lists.

**Cost:** every run prints tokens and dollars (per case, and in total). `--model claude-haiku-5-5` compares models
on the same cases. The price table in `usage.py` is typed in by hand and dated, so check it against Anthropic's
pricing page. A model with no entry shows tokens only.

**Reading a failure:** open `results/latest.json`, then decide which it is: an agent bug
(fix the prompt or tools), a grader bug (fix the grader and add a unit test), or a bad case.

Product decisions encoded here:
- The model **may** do simple arithmetic itself (the gap or % change between today's price and another
  price in the same result, or a round stake like $1,000 at a % from the data). `numbers_grounded` accepts those.
- The model **may not** say banned jargon words, even to refuse them (`no_banned_words` stays strict).
- A refusal may offer example questions with numbers, so `off_topic` does not check `no_numbers`.

## Other markets

The fixtures cover Tokyo, Hong Kong, London, India (NSE), Australia, Germany, Italy, Singapore, Thailand and
two indexes (Dow, Nikkei), with the same `market` / `currency` / `index_name` / `value_of_100` fields the real
tools return. Market and currency come from `src/markets.py`, and `tests/test_evals_global_markets.py` checks that
the fixture fields match the real tools' fields, so they cannot drift apart. `SHOP.TO` (Toronto) is served on
purpose: the real data source would answer it, so `never_fetches` can catch the agent breaking the
"unsupported market" rule. Asia-Pacific data ends a day after US and Europe data (Nov 11 vs Nov 10).

| Grader | Checks |
|---|---|
| `numbers_grounded` | Now reads yen, pounds, euros, rupees, baht, HK$, A$, S$ and index points, not only `$` |
| `currency_named` | Every price carries the stock's own currency (yen for Toyota, never `$`); indexes never get money |
| `no_currency_conversion` | No invented exchange rate or "that is about $X" |
| `index_in_points` | An index level is in points, never dollars |
| `names_listing: [TM, 7203.T]` | When a company has two listings, the answer says which one it used |
| `never_fetches: [SHOP.TO]` | An unsupported market's symbol is never requested |
| `declines_market` | Says plainly the place is not covered and offers a covered alternative |
| `names_all_dates` | When results end on different dates, the answer names each or says they differ |

Known limits: graders are heuristics. `numbers_grounded` matches decimals within ±0.005 and whole numbers
(only for figures of 100 or more) within ±1, so a fabricated figure can occasionally pass by luck. Allowing model
arithmetic widens that gap. It rejects correct-but-rounded figures ("about 54,700 points"). `currency_named`
cannot tell `$` used for a Hong Kong stock when a US stock is also in the answer (the missing HK$ label is what
catches it). `names_listing`, `declines_market` and `names_all_dates` match phrases. A judge model that re-checks
the arithmetic and wording is the proper fix.
