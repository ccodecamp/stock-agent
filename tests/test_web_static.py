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


def test_manifest_is_served_and_its_icons_exist(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    manifest = r.json()
    assert manifest["display"] == "standalone"
    assert manifest["start_url"] == "/"
    sizes = {icon["sizes"] for icon in manifest["icons"]}
    assert {"192x192", "512x512"} <= sizes
    for icon in manifest["icons"]:
        served = client.get("/" + icon["src"])
        assert served.status_code == 200, icon["src"]
        assert served.content.startswith(b"\x89PNG"), icon["src"]


def test_iphone_home_screen_pieces_are_in_the_page(client):
    html = client.get("/").text
    assert 'rel="manifest"' in html
    assert 'rel="apple-touch-icon" href="icons/icon-180.png"' in html
    assert client.get("/icons/icon-180.png").status_code == 200
    assert "viewport-fit=cover" in html  # without it the safe-area insets are always 0


def test_browser_tab_favicon_is_linked_and_served(client):
    html = client.get("/").text
    for href in ("icons/icon.svg", "icons/icon-192.png"):
        assert 'rel="icon"' in html and f'href="{href}"' in html
        assert client.get(f"/{href}").status_code == 200


def test_service_worker_is_served_from_the_root_so_it_covers_the_whole_app(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "javascript" in r.headers["content-type"]


def test_service_worker_never_touches_api_calls():
    # Chat answers and prices must always be live, so /api must be skipped before any caching.
    sw = (ROOT / "web" / "sw.js").read_text()
    assert 'startsWith("/api/")' in sw
    assert 'request.method !== "GET"' in sw
