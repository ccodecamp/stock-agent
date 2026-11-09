"""FastAPI app for the conversational stock guide.

Run locally:
    uvicorn src.api.app:app --reload

Behind a proxy (Fly.io, Railway) start uvicorn with --proxy-headers so rate
limiting sees the real client IP instead of the proxy's.

Environment:
    ANTHROPIC_API_KEY   required
    CHAT_MODEL          default claude-sonnet-5-5
    CHAT_EFFORT         default medium
    ALLOWED_ORIGINS     comma-separated CORS origins, default http://localhost:3000
    CHAT_RATE_LIMIT     per-IP limit, default 10/minute
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .chat import stream_chat

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


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)

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
def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    messages = [m.model_dump() for m in body.messages]
    return StreamingResponse(
        _sse(stream_chat(messages)),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
