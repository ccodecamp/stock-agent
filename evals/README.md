# Evals

Tests for the chat agent's *behaviour*. The real Claude runs against frozen fake tool data
(`fixtures.py`), so we know exactly which numbers a correct answer may quote.

```bash
python -m evals.run_evals                          # all cases, 3 runs each (costs API money)
python -m evals.run_evals --case off_topic --runs 1
pytest tests/test_evals_graders.py                 # tests of the graders themselves (free)
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

Known limits: graders are heuristics. `numbers_grounded` only sees `$` amounts and `%`, and matches
whole numbers within ±0.5, so a fabricated figure can occasionally pass by luck. Allowing model
arithmetic widens that gap. A judge model that re-checks the arithmetic is the proper fix.
