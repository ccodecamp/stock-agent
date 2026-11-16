"""Token and cost tracking for eval runs.

The runner hands `stream_chat` a RecordingClient: a thin wrapper around the real Anthropic client
that reads `usage` from every API response and changes nothing else. The app code is untouched.

Prices are typed in by hand and go stale. Check them against Anthropic's pricing page.
"""

from __future__ import annotations

from dataclasses import dataclass

PRICES_AS_OF = "2026-11-03"

# model id -> (input $ per million tokens, output $ per million tokens).
# Haiku 5.5's price is for prompts up to 100K tokens; chat turns here are far smaller.
PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    # Prompt-cache tokens. The chat app does not use caching and they are not priced here,
    # so a non-zero value means the cost below undercounts.
    cache_tokens: int = 0

    def record(self, usage) -> None:
        """Add one API response's `usage` object."""
        self.calls += 1
        self.input_tokens += usage.input_tokens or 0
        self.output_tokens += usage.output_tokens or 0
        self.cache_tokens += (getattr(usage, "cache_creation_input_tokens", 0) or 0) + (
            getattr(usage, "cache_read_input_tokens", 0) or 0
        )

    def add(self, other: Usage) -> None:
        self.calls += other.calls
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_tokens += other.cache_tokens

    def cost(self, model: str) -> float | None:
        """Dollars, or None for a model that has no entry in PRICES."""
        if model not in PRICES:
            return None
        price_in, price_out = PRICES[model]
        return (self.input_tokens * price_in + self.output_tokens * price_out) / 1_000_000

    def to_dict(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cache_tokens": self.cache_tokens,
        }


def format_cost(usage: Usage, model: str, runs: int) -> str:
    """One line for the summary: total dollars and dollars per run, or 'unknown' without a price."""
    cost = usage.cost(model)
    if cost is None:
        return f"cost unknown: no price for {model!r} (add it to PRICES in evals/usage.py)"
    return f"${cost:.4f} total, ${cost / max(runs, 1):.4f} per run (prices as of {PRICES_AS_OF})"


class _RecordingStream:
    def __init__(self, stream, usage: Usage):
        self._stream = stream
        self._usage = usage

    def __iter__(self):
        return iter(self._stream)

    def get_final_message(self):
        message = self._stream.get_final_message()
        self._usage.record(message.usage)
        return message


class _RecordingManager:
    def __init__(self, manager, usage: Usage):
        self._manager = manager
        self._usage = usage

    def __enter__(self):
        return _RecordingStream(self._manager.__enter__(), self._usage)

    def __exit__(self, *exc):
        return self._manager.__exit__(*exc)


class _RecordingMessages:
    def __init__(self, messages, usage: Usage):
        self._messages = messages
        self._usage = usage

    def stream(self, **kwargs):
        return _RecordingManager(self._messages.stream(**kwargs), self._usage)


class RecordingClient:
    """Drop-in for the Anthropic client as `stream_chat` uses it. `.usage` totals every call made through it."""

    def __init__(self, client):
        self.usage = Usage()
        self.messages = _RecordingMessages(client.messages, self.usage)
