# Production image for the chat API + web UI (deployed on Railway).
# Python 3.11 matches CI and the ruff target.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Dependencies first so this layer is cached until requirements.txt changes.
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src ./src
COPY config ./config
COPY web ./web

# yfinance writes a small cache under $HOME, so give the user a home directory.
RUN useradd --create-home --uid 10001 app
USER app

# The host injects $PORT (falls back to 8000 locally). Shell form of CMD via
# sh -c so the variable expands; exec so uvicorn receives SIGTERM directly.
CMD ["sh", "-c", "exec uvicorn src.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
