# Stock Predictor Agent

A small, honest monthly stock-analysis agent. It pulls American equity
prices, computes technical indicators, asks Claude to narrate the context,
and emails you the digest via Gmail.

> **This is not a prediction engine and not investment advice.** It surfaces
> where each stock sits relative to its own history. You make the calls.

## What it does

Once a month (or on demand):

1. Fetches ~2 years of OHLCV data for every ticker in `config/watchlist.yaml` (default: AAPL, MSFT, GOOGL, AMZN, NVDA, META, TSLA).
2. Computes RSI(14), 50/200-day SMAs, Bollinger Bands, ATR, and 52-week high/low — deterministically, in Python.
3. Sends the indicator snapshot to Claude, which writes a short narrative per ticker (current context, signals, watch zone, what would invalidate the read).
4. Saves the digest as Markdown in `reports/` and emails it to you via Gmail SMTP.
5. Optionally runs a transparent backtester so you can see how naive rules have actually performed on your tickers.

## Architecture

```
config/watchlist.yaml
        │
        ▼
data_fetcher.py ──► indicators.py ──► agent.py (Claude) ──► notifier.py (Gmail SMTP)
                                            │
                                            └──► reports/digest-YYYY-MM-DD.md
```

## Quick start

```bash
git clone <YOUR-REPO-URL> stock-predictor
cd stock-predictor
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your API key + Gmail creds. Keep DRY_RUN=true for the first run.

python -m src.main --dry --backtest
```

## Email setup (Gmail)

1. Turn on **2-Step Verification** on your Google account: https://myaccount.google.com/security
2. Create an **App Password**: https://myaccount.google.com/apppasswords
   - Pick "Mail" and "Other (Custom name)" → name it `stock-agent`
   - Google shows you a 16-character password ONE TIME. Copy it now.
3. Paste it into `.env` as `GMAIL_APP_PASSWORD`. Spaces are stripped automatically.
4. Set `GMAIL_USER` and `EMAIL_TO` to your Gmail address (they can be the same — emailing yourself is the common case).
5. Keep `DRY_RUN=true` until your first run prints the message you want.

> App passwords work only on accounts with 2-Step Verification enabled. If the
> "App passwords" page is missing for you, your account is either a Workspace
> account that disables them, or 2-Step Verification isn't fully on yet.

## Anthropic API setup

1. Create a key at https://console.anthropic.com/
2. **Set a spending cap** on the same dashboard (Plans & Billing → Usage Limits). Recommended: $5/month. The default watchlist costs ~$0.10/month on Sonnet.
3. Paste the key into `.env` as `ANTHROPIC_API_KEY`.

## Scheduling

Local cron — simplest:

```cron
0 9 1 * * /path/to/stock-predictor/scripts/run_monthly.sh
```

Runs 9am on the 1st of each month, but only when your machine is awake.
For more reliable scheduling, run it as a GitHub Actions cron — ask Claude
for the workflow file when you're ready.

## Web chat (local)

A conversational version: ask "How's Nvidia?" and Claude decides which tools
to call (company lookup, price snapshot, price trend, strategy test).

```bash
# Terminal 1 — API on :8000 (needs ANTHROPIC_API_KEY in .env)
uvicorn src.api.app:app --port 8000

# Terminal 2 — static frontend on :3000
python -m http.server 3000 -d web
```

Open http://localhost:3000. The frontend is plain HTML/JS in `web/` with no
build step; the backend URL is set in the `api-base` meta tag in
`web/index.html`.

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

## License

MIT — see [LICENSE](LICENSE). In short: do whatever you want with it, but no warranty, and don't blame me if your trades go sideways.

## Disclaimers

- Past indicator behavior does not predict future returns.
- Indicator values from yfinance occasionally lag or differ from your broker.
- The agent is conservative by design: it describes context, it doesn't recommend trades.
- The author is not your financial advisor.
