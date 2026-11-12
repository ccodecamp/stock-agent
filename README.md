# Stock Guide

A plain-English chat agent for US stocks. Ask "How's Nvidia doing?" and Claude decides which
tools to call (company lookup, price snapshot, price trend, charts, strategy test), then explains the
numbers the way you would to a friend who has never invested. No jargon, no predictions, no advice.

**Try it:** https://stock-agent-production-4e16.up.railway.app/ (a small demo that may be busy.
If it is, run it locally below.)

> Not a prediction engine and not investment advice. It describes what has happened.

## How it works

```
browser (web/, plain HTML/JS) ──SSE──► FastAPI (src/api/)
                                          │  tool-use loop (chat.py)
                                          ▼
                                   Claude ◄──► tools.py ──► yfinance + indicators (Python)
```

- **Python computes, Claude explains.** Every number comes from deterministic code in `src/`
  (`indicators.py`, `backtest.py`). Claude only interprets tool results, so the math is auditable and testable.
- **Hand-written tool-use loop** in `src/api/chat.py`: streams text, emits an event per tool call,
  caps round trips, and handles refusals and malformed tool input.
- **Stateless server.** The client keeps the conversation and sends it back each turn.
- **Tool inputs are validated** with pydantic before anything runs.
- **Plain English is enforced** by the system prompt, and checked by the evals below.

The repo also contains the original monthly email digest (`src/main.py`). See [docs/email-digest.md](docs/email-digest.md).

## Run it locally

Needs Python 3.10+ and an [Anthropic API key](https://console.anthropic.com/) (set a spending cap there first).

```bash
git clone https://github.com/ccodecamp/stock-agent.git
cd stock-agent
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

echo "ANTHROPIC_API_KEY=sk-ant-..." > .env
uvicorn src.api.app:app --port 8000
```

Open http://localhost:8000. One process serves both the API and the UI; there is no build step.

Optional environment variables: `CHAT_MODEL` (default `claude-sonnet-5-5`), `CHAT_EFFORT`, `CHAT_RATE_LIMIT` (default `10/minute`).

## Tests

Three layers, each catching a different kind of failure:

| Layer | Command | What it catches | Cost |
|---|---|---|---|
| Unit | `pytest` | Logic bugs in tools, indicators, API, and the graders themselves | Free |
| Browser | `pip install -r requirements-e2e.txt && playwright install chromium && pytest tests_e2e -o addopts=""` | UI bugs: streaming, error messages, history, mobile layout (API stubbed) | Free |
| Evals | `python -m evals.run_evals --runs 3` | Bad answers from the real model: jargon, advice, invented numbers, wrong tool choice | Uses API credits |

Evals run the real Claude against frozen fake tool data and grade each answer with code checks.
See [evals/README.md](evals/README.md) for how cases and graders work.

## Contributing

Contributions are welcome, including small ones. Start with [CONTRIBUTING.md](CONTRIBUTING.md),
then look for issues labelled `good first issue`. A great first contribution is a new eval case:
find a question where the agent answers badly and turn it into a test.

## More docs

- [docs/email-digest.md](docs/email-digest.md): the original monthly email digest
- [SECURITY.md](SECURITY.md): threat model and hardening

## License and disclaimers

MIT, see [LICENSE](LICENSE).

- Past behaviour does not predict future returns.
- Price data comes from Yahoo Finance via yfinance, which can lag or differ from your broker.
- The author is not your financial advisor.
