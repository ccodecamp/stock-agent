# Stock Predictor Agent

A small, honest monthly stock-analysis agent. It pulls American equity
prices, computes technical indicators, asks Claude to narrate the context,
and delivers the digest to your WhatsApp via Twilio.

> **This is not a prediction engine and not investment advice.** It surfaces
> where each stock sits relative to its own history. You make the calls.

## What it does

Once a month (or on demand):

1. Fetches ~2 years of OHLCV data for every ticker in `config/watchlist.yaml` (default: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA).
2. Computes RSI(14), 50/200-day SMAs, Bollinger Bands, ATR, and 52-week high/low — deterministically, in Python.
3. Sends the indicator snapshot to Claude, which writes a short narrative per ticker (current context, signals, watch zone, what would invalidate the read).
4. Saves the digest as Markdown in `reports/` and sends it to your WhatsApp.
5. Optionally runs a transparent backtester so you can see how naive rules have actually performed on your tickers.

## Architecture

```
config/watchlist.yaml
        │
        ▼
data_fetcher.py ──► indicators.py ──► agent.py (Claude) ──► notifier.py (Twilio WhatsApp)
                                            │
                                            └──► reports/digest-YYYY-MM-DD.md
```

## Quick start

```bash
git clone <YOUR-REPO-URL> stock-predictor
cd stock-predictor
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your API keys. Keep DRY_RUN=true for the first run.

python -m src.main --dry --backtest
```

## WhatsApp setup (Twilio)

1. Create a free Twilio account at https://www.twilio.com/.
2. Activate the **WhatsApp Sandbox** (Console → Messaging → Try it out → Send a WhatsApp message).
3. From your phone, send the join code shown to the sandbox number.
4. Put your Account SID, Auth Token, the sandbox `whatsapp:+...` number, and your own `whatsapp:+...` number in `.env`.
5. Set `DRY_RUN=false` only after a successful dry run.

## Scheduling

The simplest option — `cron`:

```cron
0 9 1 * * /path/to/stock-predictor/scripts/run_monthly.sh
```

Or a GitHub Actions workflow on a schedule (don't forget to put your secrets in repo settings, never in YAML).

## Security

See [SECURITY.md](SECURITY.md). Short version:
- Secrets live in `.env`, which is git-ignored.
- `gitleaks` runs as a pre-commit hook *and* in CI.
- `pip-audit` flags vulnerable dependencies in CI.
- `DRY_RUN=true` is the safe default.

```bash
pre-commit install   # enable hooks locally
```

## Tests

```bash
pytest
```

## Why Python (not Java)

Finance/ML libraries (`yfinance`, `pandas`, `ta`, `numpy`) and the Anthropic
SDK are far more mature in Python. For a monthly batch job, JVM performance
buys you nothing and Python ships in roughly a third the lines of code.

## Disclaimers

- Past indicator behavior does not predict future returns.
- Indicator values from yfinance occasionally lag or differ from your broker.
- The agent is conservative by design: it describes context, it doesn't recommend trades.
- The author is not your financial advisor.
