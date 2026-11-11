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
| `run_evals.py` | Runs cases, prints pass rates, writes `results/latest.json` (git-ignored) |

Every case gets the default checks (`answered`, `no_banned_words`, `no_advice`, `plain_text`,
`numbers_grounded`) plus any it lists.

**Reading a failure:** open `results/latest.json`, then decide which it is: an agent bug
(fix the prompt or tools), a grader bug (fix the grader and add a unit test), or a bad case.

Known limits: graders are heuristics. `numbers_grounded` only sees `$` amounts and `%`, matches
whole numbers within ±0.5 (so a fabricated number can occasionally match by luck), and flags
arithmetic the model did itself (e.g. "7% above the low") because no tool returned it.
