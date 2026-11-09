"""Deploy contract: one service serves both the API and the web UI, with no secrets baked in."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from src.api import app as app_module

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def client():
    return TestClient(app_module.app)


def test_root_serves_the_chat_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "Stock Guide" in r.text


def test_static_assets_are_served(client):
    assert client.get("/app.js").status_code == 200
    assert client.get("/style.css").status_code == 200


def test_api_routes_are_not_shadowed_by_static_files(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_unknown_api_path_is_a_404_not_the_html_page(client):
    r = client.get("/api/does-not-exist")
    assert r.status_code == 404
    assert "Stock Guide" not in r.text


def test_page_calls_the_api_on_the_same_origin():
    # A hard-coded localhost URL would break every deployed copy of the page.
    html = (ROOT / "web" / "index.html").read_text()
    match = re.search(r'<meta name="api-base" content="([^"]*)"', html)
    assert match is not None
    assert match.group(1) == ""


def test_dockerignore_keeps_secrets_out_of_the_image():
    lines = {line.strip() for line in (ROOT / ".dockerignore").read_text().splitlines()}
    assert ".env" in lines
    assert ".env.*" in lines


def test_dockerfile_never_copies_env_files():
    dockerfile = (ROOT / "Dockerfile").read_text()
    copies = [line for line in dockerfile.splitlines() if line.strip().startswith(("COPY", "ADD"))]
    assert copies, "expected COPY instructions"
    assert not any(".env" in line for line in copies)
