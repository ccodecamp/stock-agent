# Contributing

Thanks for helping. The workflow is simple and CI enforces most of it.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-e2e.txt
playwright install chromium
pre-commit install        # runs gitleaks, ruff and key detection before each commit
```

You only need an `ANTHROPIC_API_KEY` in `.env` to run the app or the evals. Unit and browser tests need none.

## Workflow

1. Fork, then branch off `main`: `feat/...`, `fix/...`, `test/...`, `docs/...`.
2. Make one change per pull request. Please don't bundle unrelated improvements.
3. Open a PR against `main`. `main` is protected: a PR and a green `test` check are required.
4. Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) (`fix(data): ...`).

## Before you push

```bash
ruff check .
pytest
pytest tests_e2e -o addopts=""     # if you touched web/
```

Style: line length 120, rules `E F W I B UP S`. Use `datetime.now(UTC)`, not `utcnow()`.
Dependencies use compatible ranges (`>=X,<Y`), not exact pins.

## Design rules (please keep these)

- **Python computes the numbers, Claude only explains them.** Don't ask the model to do math you can do in code.
- **Answers must be plain English** for a non-technical reader. The banned-jargon list lives in the system
  prompt in `src/api/chat.py`, and `evals/graders.py` must stay in sync (a test enforces it).
- **No advice, no predictions.** The agent describes what has happened.
- **Tool inputs are validated** with pydantic before anything runs.
- Never commit secrets. Only `.env.example` is tracked.

## Adding an eval case (the best first contribution)

An eval is a test for a non-deterministic system: it checks properties of an answer, not exact text.

1. Find a question where the agent gives a bad answer (jargon, advice, a number that isn't in the data, the wrong tool).
2. Add it to `evals/cases.yaml` with a `category` (`normal`, `edge` or `adversarial`) and the checks it should pass.
   Every case already gets the default checks; see `DEFAULT_CHECKS` in `evals/graders.py`.
3. If the data it needs isn't in `evals/fixtures.py`, add invented frozen data there.
4. Run it: `python -m evals.run_evals --case your_case_id --runs 3` (uses API credits).
5. Decide what a failure is: an agent bug (fix the prompt or tools), a grader bug (fix the grader and add a unit
   test in `tests/test_evals_graders.py`), or a bad case.

A new grader needs a known-good and a known-bad example in `tests/test_evals_graders.py`. A grader that can't fail is worse than none.

## Reporting bugs

Open an issue with the question you asked, what the agent said, and what you expected. Screenshots help.
