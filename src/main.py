"""Entry point — runs the monthly pipeline end-to-end.

Usage:
    python -m src.main           # full run, sends WhatsApp (or dry-run)
    python -m src.main --dry     # force dry run regardless of env
    python -m src.main --backtest  # also print backtest results
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler

from .agent import TickerAnalysis, analyze_many
from .backtest import backtest_golden_cross, backtest_rsi_oversold
from .data_fetcher import fetch_many
from .indicators import compute_indicators
from .notifier import send_whatsapp


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(rich_tracebacks=True, show_path=False)],
    )


def load_watchlist(path: str) -> tuple[list[str], dict]:
    cfg = yaml.safe_load(Path(path).read_text())
    symbols = [t["symbol"] for t in cfg["tickers"]]
    return symbols, cfg.get("thresholds", {})


def build_digest(analyses: list[TickerAnalysis]) -> str:
    today = datetime.utcnow().strftime("%Y-%m-%d")
    header = f"📊 Monthly Stock Digest — {today}\n"
    body = "\n\n— — —\n\n".join(a.to_whatsapp_chunk() for a in analyses)
    footer = "\n\nNot investment advice. Signals only."
    return header + body + footer


def write_report(content: str, reports_dir: str) -> Path:
    Path(reports_dir).mkdir(parents=True, exist_ok=True)
    fname = f"digest-{datetime.utcnow().strftime('%Y-%m-%d')}.md"
    path = Path(reports_dir) / fname
    path.write_text(content)
    return path


def main() -> int:
    load_dotenv()
    _setup_logging()

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry", action="store_true", help="Force dry-run (no WhatsApp send)")
    parser.add_argument("--backtest", action="store_true", help="Also run backtests")
    args = parser.parse_args()

    console = Console()
    watchlist_path = os.environ.get("WATCHLIST_PATH", "config/watchlist.yaml")
    reports_dir = os.environ.get("REPORTS_DIR", "reports")
    lookback = int(os.environ.get("LOOKBACK_YEARS", "2"))

    symbols, thresholds = load_watchlist(watchlist_path)
    console.rule(f"Fetching {len(symbols)} tickers")
    histories = fetch_many(symbols, lookback_years=lookback)

    snapshots = []
    for sym, hist in histories.items():
        try:
            snap = compute_indicators(
                sym,
                hist.df,
                rsi_oversold=thresholds.get("rsi_oversold", 30),
                rsi_overbought=thresholds.get("rsi_overbought", 70),
                bb_window=thresholds.get("bollinger_window", 20),
                bb_std=thresholds.get("bollinger_std", 2),
                sma_short=thresholds.get("sma_short", 50),
                sma_long=thresholds.get("sma_long", 200),
            )
            snapshots.append(snap)
        except Exception as e:  # noqa: BLE001
            console.print(f"[yellow]Indicator failure {sym}: {e}[/yellow]")

    console.rule("Calling Claude for narratives")
    analyses = analyze_many(snapshots)

    digest = build_digest(analyses)
    report_path = write_report(digest, reports_dir)
    console.print(f"[green]Wrote {report_path}[/green]")

    if args.backtest:
        console.rule("Backtests (RSI<30 + Golden Cross, hold 30d)")
        for sym, hist in histories.items():
            for fn in (backtest_rsi_oversold, backtest_golden_cross):
                r = fn(hist.df, sym)
                console.print(
                    f"{r.symbol}: {r.rule} → "
                    f"n={r.n_signals}, avg={r.avg_30d_forward_return_pct}%, "
                    f"win={r.win_rate_30d_pct}% (B&H={r.buy_and_hold_total_return_pct}%)"
                )

    console.rule("Sending digest")
    send_whatsapp(digest, dry_run=True if args.dry else None)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
