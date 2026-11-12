# Monthly email digest

The project started as a monthly job that emails a short plain-English note per stock. It still
works and shares code with the chat app. Not investment advice.

Pipeline: `data_fetcher.py` → `indicators.py` → `agent.py` (Claude) → `notifier.py` (Gmail SMTP).
Tickers come from `config/watchlist.yaml`; each digest is also saved to `reports/`.

```bash
cp .env.example .env     # add ANTHROPIC_API_KEY and your Gmail details; keep DRY_RUN=true
python -m src.main --dry --backtest      # prints the digest, sends nothing
DRY_RUN=false python -m src.main         # really sends
```

**Gmail:** turn on 2-Step Verification, create an [App Password](https://myaccount.google.com/apppasswords),
and put it in `.env` as `GMAIL_APP_PASSWORD` (spaces are stripped). Set `GMAIL_USER` and `EMAIL_TO`.

**Scheduling:** `0 9 1 * * /path/to/stock-agent/scripts/run_monthly.sh` in cron runs it on the 1st of
each month while your machine is awake.
