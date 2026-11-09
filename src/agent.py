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


SYSTEM_PROMPT = """You are writing a friendly, plain-English stock check-in
for someone who is NOT a finance professional. They have heard of stocks
but they don't know what RSI, Bollinger Bands, or moving averages are.

You are NOT a financial advisor. You do not predict prices. You describe
what is happening with a stock in everyday language.

WRITING RULES (strict):
- Write like you are explaining to a curious friend who has never invested.
- NEVER use jargon. Banned words: RSI, Bollinger, SMA, moving average,
  golden cross, %B, momentum oscillator, mean reversion, oversold,
  overbought, support, resistance.
- Translate indicators into plain English:
  * High RSI = "the stock has been bought heavily lately, like a runner
    who has been sprinting — they may need to slow down soon."
  * Low RSI = "the stock has been beaten down recently, possibly more
    than the news justifies."
  * Above the 200-day average = "trading higher than its typical price
    over the last year."
  * Below the 200-day average = "trading lower than its typical price
    over the last year."
  * Near 52-week high = "close to the highest price it has reached in
    the past year."
  * Near 52-week low = "close to the lowest price it has reached in the
    past year."
- Use simple analogies a non-investor would get.
- NO markdown. NO asterisks for bold. NO headers like ## or **. Just plain
  flowing paragraphs separated by blank lines. Email will render literally.
- Keep it to roughly 4 short paragraphs:
  Paragraph 1: What is happening right now — current price and how it
    compares to recent months in plain words.
  Paragraph 2: What the patterns suggest, in everyday terms.
  Paragraph 3: A "watch zone" — a price range that might be more
    interesting if they were thinking of buying. Phrase as a possibility,
    not a recommendation.
  Paragraph 4: What would change the picture — what to watch for that
    would flip the read.
- End with one short line: "Confidence: low / medium / high — <brief why>."
- Then on the very last line: "Not investment advice — just observations."
- 150 words max for the whole thing. Warm, clear, conversational.
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

        # Plain-English at-a-glance summary — no jargon.
        if s.pct_above_sma_200 > 5:
            trend = f"trading about {s.pct_above_sma_200:.0f}% higher than its typical price over the past year"
        elif s.pct_above_sma_200 < -5:
            trend = f"trading about {abs(s.pct_above_sma_200):.0f}% lower than its typical price over the past year"
        else:
            trend = "near its typical price over the past year"

        if s.rsi_zone == "overbought":
            momentum = "lots of buying recently — looking a bit hot"
        elif s.rsi_zone == "oversold":
            momentum = "lots of selling recently — looking beaten down"
        else:
            momentum = "calm trading, no extreme buying or selling"

        if s.pct_off_52w_high > -3:
            position = "near its highest price of the past year"
        elif s.pct_above_52w_low < 5:
            position = "near its lowest price of the past year"
        else:
            position = "somewhere in the middle of its yearly range"

        quick = f"At a glance: {trend}; {momentum}; {position}."

        return f"{header}\n{underline}\n{quick}\n\n{self.narrative}"


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
