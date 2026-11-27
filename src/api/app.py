"""FastAPI app for the conversational stock guide.

Run locally (API and web UI together on http://localhost:8000):
    uvicorn src.api.app:app --reload

The web UI in web/ is served by this app at "/", so a deploy is one service
and the page calls the API on the same origin (no CORS needed).

Rate limiting: behind a reverse proxy every request arrives from the proxy's
address, so the per-IP limit acts as one shared limit for all users. Do not
turn on uvicorn's --forwarded-allow-ips="*" to fix that: it trusts the
client-supplied X-Forwarded-For and lets anyone dodge the limit. Trust the
specific proxy once its header behaviour is verified.

Environment:
    ANTHROPIC_API_KEY   required
    CHAT_MODEL          default claude-sonnet-5-5
    CHAT_EFFORT         default medium
    ALLOWED_ORIGINS     comma-separated CORS origins, default http://localhost:3000
    CHAT_RATE_LIMIT     per-IP limit, default 10/minute
    CHAT_DAILY_LIMIT    chats per UTC day for the whole app, default 300, 0 = no cap
    QUOTES_RATE_LIMIT   per-IP limit on the sidebar's price lookups, default 30/minute
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StringConstraints, ValidationError, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .chat import stream_chat
from .favorites import MAX_FAVORITES, latest_quote
from .limits import configured_limit, daily_limit
from .tools import SYMBOL_PATTERN

load_dotenv()

MAX_MESSAGES = 40
MAX_HISTORY_CHARS = 60_000
MAX_USER_TEXT_CHARS = 1_000
# Only block types this API itself produces; rejects images/documents smuggled into history.
_ALLOWED_BLOCKS = {"text", "thinking", "redacted_thinking", "tool_use", "tool_result"}

limiter = Limiter(key_func=get_remote_address)

app = FastAPI(title="Stock Guide API", version="0.1.0")
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
_origins = os.environ.get("ALLOWED_ORIGINS", "http://localhost:3000")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _origins.split(",") if o.strip()],
    allow_methods=["GET", "POST"],
    allow_headers=["Content-Type"],
)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str | list[dict[str, Any]]


Symbol = Annotated[str, StringConstraints(pattern=SYMBOL_PATTERN)]


def _dedupe(symbols: list[str]) -> list[str]:
    return list(dict.fromkeys(symbols))  # keep order, drop repeats


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)
    # The page's favourites, so Claude knows what's saved (the server stores nothing).
    favorites: list[Symbol] = Field(default_factory=list, max_length=MAX_FAVORITES)

    @field_validator("favorites")
    @classmethod
    def _dedupe_favorites(cls, favorites: list[str]) -> list[str]:
        return _dedupe(favorites)

    @field_validator("messages")
    @classmethod
    def _check_history(cls, messages: list[Message]) -> list[Message]:
        last = messages[-1]
        if last.role != "user" or not isinstance(last.content, str):
            raise ValueError("last message must be a user message with text content")
        if not last.content.strip():
            raise ValueError("message is empty")
        if len(last.content) > MAX_USER_TEXT_CHARS:
            raise ValueError(f"message is longer than {MAX_USER_TEXT_CHARS} characters")
        for m in messages:
            if isinstance(m.content, list) and any(b.get("type") not in _ALLOWED_BLOCKS for b in m.content):
                raise ValueError("unsupported content block in history")
        size = len(json.dumps([m.model_dump() for m in messages]))
        if size > MAX_HISTORY_CHARS:
            raise ValueError("conversation is too long; start a new chat")
        return messages


def _sse(events: Iterator[dict]) -> Iterator[str]:
    for event in events:
        yield f"event: {event['type']}\ndata: {json.dumps(event, default=str)}\n\n"


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/chat")
@limiter.limit(lambda: os.environ.get("CHAT_RATE_LIMIT", "10/minute"))
def chat(request: Request, body: ChatRequest) -> Response:
    # Checked after the per-minute limit and after validation, so only real requests use up the day.
    if not daily_limit.try_take(configured_limit()):
        resets_at = daily_limit.resets_at()
        retry_after = max(int((resets_at - datetime.now(UTC)).total_seconds()), 1)
        return JSONResponse(
            status_code=429,
            headers={"Retry-After": str(retry_after)},
            content={
                "code": "daily_limit",
                "detail": "Today's question limit has been reached.",
                "resets_at": resets_at.isoformat(),
            },
        )
    messages = [m.model_dump() for m in body.messages]
    return StreamingResponse(
        _sse(stream_chat(messages, favorites=body.favorites)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class _QuoteSymbols(BaseModel):
    symbols: list[Symbol] = Field(min_length=1, max_length=MAX_FAVORITES)


@app.get("/api/quotes")
@limiter.limit(lambda: os.environ.get("QUOTES_RATE_LIMIT", "30/minute"))
def quotes(request: Request, symbols: str = Query(max_length=200)) -> dict:
    """Latest close and daily change for the favourites sidebar, e.g. ?symbols=NVDA,BHP.AX.

    No model is involved, so this does not count towards the daily chat limit.
    """
    try:
        wanted = _QuoteSymbols(symbols=_dedupe([s.strip() for s in symbols.split(",") if s.strip()]))
    except ValidationError as e:
        raise HTTPException(status_code=422, detail=e.errors(include_url=False, include_context=False)) from None
    return {"quotes": [latest_quote(s) for s in wanted.symbols]}


# Mounted last so every /api route above wins; anything else is a static file,
# with index.html served at "/".
WEB_DIR = Path(__file__).resolve().parents[2] / "web"
if WEB_DIR.is_dir():
    app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
