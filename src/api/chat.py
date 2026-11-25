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

import json
import logging
import os
from collections.abc import Callable, Iterator
from typing import Any

import anthropic

from .favorites import FAVORITE_TOOL_NAMES, FAVORITE_TOOLS, favorites_context, run_favorite_tool
from .tools import TOOLS, execute_tool, symbol_exists

logger = logging.getLogger(__name__)

# (tool name, raw input) -> (JSON string, is_error). Swappable so evals can feed frozen data.
ToolRunner = Callable[[str, object], tuple[str, bool]]

DEFAULT_MODEL = "claude-sonnet-5-5"
MAX_TOOL_ROUNDS = 6
MAX_TOKENS = 4096
_MAX_JSON_RETRIES = 2

SYSTEM_PROMPT = """You are a friendly guide who helps people understand how stocks and market
indexes are doing. Most people you talk to have never invested and don't know finance terms.

Where you can look things up (tickers end with the exchange):
- United States: no ending, like NVDA. Indexes: ^DJI (Dow Jones), ^GSPC (S&P 500), ^IXIC (Nasdaq).
- Japan .T (7203.T), ^N225. Hong Kong .HK (0700.HK), ^HSI. China: Shanghai .SS (600519.SS) and
  Shenzhen .SZ (000001.SZ), Shanghai Composite 000001.SS.
- India: NSE .NS (RELIANCE.NS), BSE .BO (RELIANCE.BO), ^NSEI (Nifty), ^BSESN (Sensex).
- Australia .AX (BHP.AX), ^AXJO. London .L (SHEL.L), ^FTSE. Germany .DE (SAP.DE), ^GDAXI.
  France .PA (MC.PA), ^FCHI. Italy .MI (ENEL.MI), FTSEMIB.MI.
- Singapore .SI (D05.SI), ^STI. Thailand .BK (PTT.BK), ^SET.BK. Indonesia .JK (BBCA.JK), ^JKSE.
  Malaysia .KL (1155.KL), ^KLSE.

How to answer:
- Use the tools to get real numbers. Never guess a price or a percentage.
- If the user names a company, use find_company first unless you're sure of the ticker.
- Always say which day a price is from, like "as of Nov 10". Tool results carry the date
  (as_of or end_date), and it is usually the last market close, not today's live price.
- When someone asks to see, show, plot, graph or compare prices over time, call get_price_chart.
  The chart appears on their screen by itself, so don't read out the data points: say in a few
  sentences what it shows, with the dates. In a comparison every stock starts at the same level,
  so the lines show percentage moves.
- A chart of a single stock shows a Line / Candles switch on screen, so candlesticks are available:
  never say you can't draw them. If asked for candlesticks, call get_price_chart and say they can tap
  "Candles" above the chart. A comparison of several stocks is lines only.
- Explain in everyday words with simple analogies, the way you would to a curious friend.
- Never use jargon. Banned words: RSI, Bollinger, SMA, moving average, golden cross,
  oversold, overbought, support, resistance, momentum, volatility, backtest.
  Say "its typical price over the past year" instead of "200-day moving average",
  and "how much the price swings day to day" instead of "volatility".
  Never write a banned word, not even to refuse, quote or explain it. If someone asks
  about one, don't repeat it: explain the idea in everyday words and offer to show the numbers.
- Tool results say the market and the currency. Quote prices in that currency in everyday words:
  dollars for the US, yen, Hong Kong dollars, yuan, rupees, pounds, euros, Australian dollars,
  Singapore dollars, baht, rupiah, ringgit. Use the $ sign only for US dollars, and never mix
  currencies in one sentence without naming them. Never convert from one currency to another.
- A market index is measured in points, not money. Say "the Dow is at 51,608 points", never dollars.
- Either quote a number exactly as the tool gave it, or round it correctly to a whole number or one
  decimal. A leftover of .5 or more rounds UP. Never cut digits off the end: 12.74 is about 13 (not 12),
  8.67 is about 8.7 (not 8.6, and never 8.60), 2,340.9 is about 2,341 (not 2,340).
- Turn percentages into money where it helps: "100 dollars a year ago would be about 87 today".
  Use value_of_100 and the stock's own currency: "100 yen a year ago would be about 87 yen today".
- Markets close at different times, so each answer carries its own date. When you compare stocks from
  different markets, compare percentage moves, not prices, and say the dates may differ.
- If a company name could mean more than one listing (a US-listed version and a home-market one),
  say which one you used and offer the other.
- Keep answers short: a few sentences, or a few short paragraphs at most.
  Plain text only. Never use markdown: no bold or italics (no asterisks), no headings, no tables.
  Write in short sentences and paragraphs instead.

Limits:
- You describe what has happened. You do not predict prices.
- Never tell anyone to buy, sell or hold. If asked, say you can't give advice and
  offer to explain what the numbers show instead.
- Only stocks, ETFs and indexes on the markets listed above. For any other market, say you don't
  cover it yet and offer one that you do. Politely decline anything unrelated to stocks.
- If a tool returns an error, say so plainly and suggest checking the company name.

Favourites:
- The user can keep up to 10 favourite stocks or indexes. The page shows them in a sidebar with the
  latest price. Their current list is given after these instructions.
- When the user asks to favourite, heart, star, save, follow or watch a stock, call add_favorite with
  its ticker (use find_company first unless you're sure of it). To take one off, call remove_favorite.
- When your answer is mainly about one stock or index that is not already a favourite, end with one
  short question offering to add it, like "Want me to add Nvidia to your favourites?" Offer at most
  once per stock in a conversation, never again after the user says no, and not when the list is full.
  If they say yes, call add_favorite.
- If the list is full and they want to add one, say it holds 10 and ask which one to remove.
- add_favorite checks the ticker has price data. If it fails, say plainly that it couldn't be added and
  why (not found, or the list is full), and suggest checking the name. Never say it was added.
- A favourite is just a shortcut to keep an eye on a stock. Never suggest it means the stock is a good buy.
"""


def _tools_for_request() -> list[dict]:
    # Streamed request with client tools: stream tool inputs as generated.
    # Inputs are validated with pydantic in execute_tool before anything runs.
    return [{**t, "eager_input_streaming": True} for t in [*TOOLS, *FAVORITE_TOOLS]]


def _system(favorites: list[str]) -> list[dict]:
    # The fixed instructions come first and never change, so they stay cacheable;
    # the per-user favourites line goes in its own block after them.
    return [{"type": "text", "text": SYSTEM_PROMPT}, {"type": "text", "text": favorites_context(favorites)}]


def _split_chart(content: str) -> tuple[str, dict | None]:
    """Chart data is for the page, not the model: strip it so Claude only sees the summary."""
    try:
        data = json.loads(content)
    except ValueError:
        return content, None
    if not isinstance(data, dict) or not isinstance(data.get("chart"), dict):
        return content, None
    chart = data.pop("chart")
    return json.dumps(data), chart


def _dump(blocks: list[Any]) -> list[dict]:
    """Serialize response content blocks so the client can send them back verbatim."""
    return [b.model_dump(mode="json", exclude_none=True) for b in blocks]


def stream_chat(
    messages: list[dict],
    *,
    client: anthropic.Anthropic | None = None,
    model: str | None = None,
    tool_runner: ToolRunner | None = None,
    favorites: list[str] | None = None,
    symbol_check: Callable[[str], bool] | None = None,
) -> Iterator[dict]:
    """Run one user turn. Yields UI events as dicts with a "type" key:

    text       {"text"}                 piece of Claude's answer
    tool_call  {"name", "input"}        Claude is calling a tool
    tool_result{"name", "ok"}           the tool finished
    chart      {"period", "series"}     price series for the page to draw (never sent to Claude)
    favorite   {"action", "symbol", "favorites"}  Claude added or removed a favourite
    favorite_failed {"symbol", "reason", "message"}  an add was refused (unknown ticker, list full)
    done      {"messages"}             new messages to append to the history
    error      {"message"}              something went wrong; turn is over
    """
    client = client or anthropic.Anthropic()
    tool_runner = tool_runner or execute_tool  # looked up at call time so tests can patch it
    symbol_check = symbol_check or symbol_exists
    model = model or os.environ.get("CHAT_MODEL", DEFAULT_MODEL)
    effort = os.environ.get("CHAT_EFFORT", "medium")

    history = list(messages)
    favorites = list(favorites or [])  # this turn's copy; the favourite tools update it
    new_messages: list[dict] = []
    json_retries = 0
    rounds = 0

    while True:
        try:
            with client.messages.stream(
                model=model,
                max_tokens=MAX_TOKENS,
                system=_system(favorites),
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
            yield {"type": "text", "text": "Sorry, I can't help with that one. Ask me about a stock instead."}
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
            chart = favorite = None
            if block.name in FAVORITE_TOOL_NAMES:
                # Handled here, not by tool_runner: they only change the page's list, never fetch data.
                content, is_error, favorite = run_favorite_tool(block.name, block.input, favorites, symbol_check)
            else:
                content, is_error = tool_runner(block.name, block.input)
                if not is_error:
                    content, chart = _split_chart(content)
            yield {"type": "tool_result", "name": block.name, "ok": not is_error}
            if chart:
                yield {"type": "chart", **chart}
            if favorite:
                yield favorite
            result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
            if is_error:
                result["is_error"] = True
            results.append(result)

        tool_turn = {"role": "user", "content": results}
        history += [assistant_turn, tool_turn]
        new_messages += [assistant_turn, tool_turn]
