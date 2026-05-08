"""Claude agent that turns indicator snapshots into a per-ticker analysis.

Design choice: indicators are computed in Python (deterministic, testable).
The LLM only INTERPRETS them and writes the narrative. This keeps math
out of the model and makes the system auditable.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass

from anthropic import Anthropic

from .indicators import IndicatorSnapshot

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are a disciplined market-analysis assistant.

You are NOT a financial advisor. You do not predict prices. You describe
where a stock sits relative to its own history using technical indicators,
and you flag scenarios that have historically been associated with mean
reversion, breakouts, or trend continuation. You never tell the user to buy
or sell.

For each ticker you receive:
1. Summarize current valuation context vs. its own history (1-2 sentences).
2. Flag notable signals (RSI extremes, distance from moving averages,
   position within Bollinger bands, distance from 52-week high/low).
3. Give a "watch zone" — a price range where, IF the user is already
   considering accumulating this name, they may want to pay closer attention.
4. State explicitly what would change your read (the disconfirming evidence).
5. Output a single confidence label: "low", "medium", or "high" — about how
   well-defined the signals are, NOT about price direction.

Be concise. No bullet lists longer than 4 items. Plain prose preferred.
Always end with the disclaimer: "Not investment advice. Signals only."
"""


@dataclass
class TickerAnalysis:
    symbol: str
    snapshot: IndicatorSnapshot
    narrative: str

    def to_text_chunk(self) -> str:
        s = self.snapshot
        header = f"{s.symbol} — ${s.close} (as of {s.as_of})"
        underline = "=" * len(header)
        stats = (
            f"RSI(14): {s.rsi_14} ({s.rsi_zone}) | "
            f"vs 50d: {s.pct_above_sma_50:+.1f}% | "
            f"vs 200d: {s.pct_above_sma_200:+.1f}% | "
            f"off 52w high: {s.pct_off_52w_high:+.1f}%"
        )
        return f"{header}\n{underline}\n{stats}\n\n{self.narrative}"


def analyze_ticker(
    snapshot: IndicatorSnapshot,
    *,
    client: Anthropic | None = None,
    model: str | None = None,
) -> TickerAnalysis:
    """Send one indicator snapshot to Claude and get back a narrative."""
    client = client or Anthropic()  # picks up ANTHROPIC_API_KEY from env
    model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    user_msg = (
        f"Indicator snapshot for {snapshot.symbol}:\n"
        f"```json\n{json.dumps(snapshot.to_dict(), indent=2)}\n```\n\n"
        "Write the analysis per the system prompt. Keep it under 180 words."
    )

    response = client.messages.create(
        model=model,
        max_tokens=600,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )

    narrative_parts = [b.text for b in response.content if b.type == "text"]
    narrative = "\n".join(narrative_parts).strip()
    return TickerAnalysis(symbol=snapshot.symbol, snapshot=snapshot, narrative=narrative)


def analyze_many(
    snapshots: list[IndicatorSnapshot],
    *,
    client: Anthropic | None = None,
    model: str | None = None,
) -> list[TickerAnalysis]:
    out = []
    for snap in snapshots:
        try:
            out.append(analyze_ticker(snap, client=client, model=model))
        except Exception as e:  # noqa: BLE001
            logger.warning("Analysis failed for %s: %s", snap.symbol, e)
    return out
