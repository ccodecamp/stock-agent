"""Read-only check of the deployed site. Sends no message, so it costs nothing.

E2E_LIVE_URL=https://stock-agent-production-4e16.up.railway.app pytest tests_e2e/test_live_smoke.py
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect

LIVE_URL = os.environ.get("E2E_LIVE_URL")

pytestmark = pytest.mark.skipif(not LIVE_URL, reason="set E2E_LIVE_URL to run against the live site")


def test_live_site_renders_the_chat_page(page: Page):
    page.goto(LIVE_URL)
    expect(page).to_have_title("Stock Guide")
    expect(page.locator(".chip")).to_have_count(4)
    expect(page.locator("#input")).to_be_enabled()


def test_live_health_endpoint_is_ok(page: Page):
    response = page.request.get(f"{LIVE_URL.rstrip('/')}/api/health")
    assert response.ok
