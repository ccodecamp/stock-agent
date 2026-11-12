"""The tool-use loop behind POST /api/chat.

A hand-written loop rather than the SDK's tool runner, because the UI needs
a custom event for every tool call and we want a hard cap on round trips.

Flow for one user turn:
  1. Stream Claude's reply, forwarding text as it arrives.
  2. If Claude asked for tools, run them, append the results, go to 1.
  3. Stop when Claude answers without tools (or after MAX_TOOL_ROUNDS).

The server keeps no state. The client stores the `messages` from the `done`
event and sends them back unchanged next turn: current Claude models reject
a history whose assistant turns were edited.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from typing import Any

import anthropic

from .tools import TOOLS, execute_tool

logger = logging.getLogger(__name__)

# (tool name, raw input) -> (JSON string, is_error). Swappable so evals can feed frozen data.
ToolRunner = Callable[[str, object], tuple[str, bool]]

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOOL_ROUNDS = 6
MAX_TOKENS = 4096
_MAX_JSON_RETRIES = 2

SYSTEM_PROMPT = """You are a friendly guide who helps people understand how US-listed stocks
are doing. Most people you talk to have never invested and don't know finance terms.

How to answer:
- Use the tools to get real numbers. Never guess a price or a percentage.
- If the user names a company, use find_company first unless you're sure of the ticker.
- Always say which day a price is from, like "as of Nov 10". Tool results carry the date
  (as_of or end_date), and it is usually the last market close, not today's live price.
- Explain in everyday words with simple analogies, the way you would to a curious friend.
- Never use jargon. Banned words: RSI, Bollinger, SMA, moving average, golden cross,
  oversold, overbought, support, resistance, momentum, volatility, backtest.
  Say "its typical price over the past year" instead of "200-day moving average",
  and "how much the price swings day to day" instead of "volatility".
  Never write a banned word, not even to refuse, quote or explain it. If someone asks
  about one, don't repeat it: explain the idea in everyday words and offer to show the numbers.
- Turn percentages into money where it helps: "$100 a year ago would be about $87 today".
- Keep answers short: a few sentences, or a few short paragraphs at most.
  Plain text only. Never use markdown: no bold or italics (no asterisks), no headings, no tables.
  Write in short sentences and paragraphs instead.

Limits:
- You describe what has happened. You do not predict prices.
- Never tell anyone to buy, sell or hold. If asked, say you can't give advice and
  offer to explain what the numbers show instead.
- Only US stocks and ETFs. Politely decline anything unrelated to stocks.
- If a tool returns an error, say so plainly and suggest checking the company name.
"""


def _tools_for_request() -> list[dict]:
    # Streamed request with client tools: stream tool inputs as generated.
    # Inputs are validated with pydantic in execute_tool before anything runs.
    return [{**t, "eager_input_streaming": True} for t in TOOLS]


def _dump(blocks: list[Any]) -> list[dict]:
    """Serialize response content blocks so the client can send them back verbatim."""
    return [b.model_dump(mode="json", exclude_none=True) for b in blocks]


def stream_chat(
    messages: list[dict],
    *,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
    tool_runner: ToolRunner | None = None,
) -> Iterator[dict]:
    """Run one user turn. Yields UI events as dicts with a "type" key:

    text       {"text"}                 piece of Claude's answer
    tool_call  {"name", "input"}        Claude is calling a tool
    tool_result{"name", "ok"}           the tool finished
    done       {"messages"}             new messages to append to the history
    error      {"message"}              something went wrong; turn is over
    """
    client = client or anthropic.Anthropic()
    tool_runner = tool_runner or execute_tool  # looked up at call time so tests can patch it
    model = model or os.environ.get("CHAT_MODEL", DEFAULT_MODEL)
    effort = os.environ.get("CHAT_EFFORT", "medium")

    history = list(messages)
    new_messages: list[dict] = []
    json_retries = 0
    rounds = 0

    while True:
        try:
            with client.messages.stream(
                model=model,
                max_tokens=MAX_TOKENS,
                system=SYSTEM_PROMPT,
                tools=_tools_for_request(),
                output_config={"effort": effort},
                messages=history,
            ) as stream:
                for event in stream:
                    if event.type == "text":
                        yield {"type": "text", "text": event.text}
                response = stream.get_final_message()
            json_retries = 0
        except ValueError:
            # Tool-input JSON the SDK could not parse at all; there is no
            # tool_use id to answer, so re-issue the turn (bounded).
            json_retries += 1
            if json_retries > _MAX_JSON_RETRIES:
                yield {"type": "error", "message": "Claude sent a malformed tool call. Please try again."}
                return
            continue
        except anthropic.RateLimitError:
            yield {"type": "error", "message": "We're getting a lot of questions right now. Try again in a minute."}
            return
        except anthropic.APIStatusError as e:
            logger.error("Anthropic API error %s: %s", e.status_code, e.message)
            yield {"type": "error", "message": "The AI service returned an error. Please try again."}
            return
        except anthropic.APIConnectionError:
            yield {"type": "error", "message": "Couldn't reach the AI service. Please try again."}
            return

        assistant_turn = {"role": "assistant", "content": _dump(response.content)}
        tool_uses = [b for b in response.content if b.type == "tool_use"]

        if response.stop_reason == "refusal":
            # A refusal can cut a tool call off mid-input: never run that turn's tools,
            # and leave the turn out of the history (a tool_use with no result is a 400).
            yield {"type": "text", "text": "Sorry, I can't help with that one. Ask me about a US stock instead."}
            yield {"type": "done", "messages": new_messages}
            return

        if response.stop_reason == "pause_turn":
            history.append(assistant_turn)
            new_messages.append(assistant_turn)
            continue

        if not tool_uses:
            new_messages.append(assistant_turn)
            yield {"type": "done", "messages": new_messages}
            return

        if response.stop_reason == "max_tokens":
            # A truncated tool input can still parse as a valid partial object.
            yield {"type": "error", "message": "The answer got too long. Try a narrower question."}
            return

        rounds += 1
        if rounds > MAX_TOOL_ROUNDS:
            yield {"type": "error", "message": "That needed too many lookups. Try asking about fewer stocks at once."}
            return

        # All tool results go back in ONE user message, matching the tool_use ids.
        results = []
        for block in tool_uses:
            yield {"type": "tool_call", "name": block.name, "input": block.input}
            content, is_error = tool_runner(block.name, block.input)
            yield {"type": "tool_result", "name": block.name, "ok": not is_error}
            result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
            if is_error:
                result["is_error"] = True
            results.append(result)

        tool_turn = {"role": "user", "content": results}
        history += [assistant_turn, tool_turn]
        new_messages += [assistant_turn, tool_turn]
