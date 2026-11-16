"""Installability and offline behaviour. Real server and real service worker; /api/chat is stubbed."""

from __future__ import annotations

from playwright.sync_api import Page, expect

from .conftest import answer


def wait_for_worker(page: Page) -> None:
    page.evaluate("navigator.serviceWorker.ready.then(() => true)")
    # The worker claims open pages when it activates, so no reload is needed.
    page.wait_for_function("navigator.serviceWorker.controller !== null", timeout=10_000)


def test_page_links_a_valid_manifest_and_icons(chat, page, base_url):
    href = page.locator('link[rel="manifest"]').get_attribute("href")
    manifest = page.request.get(f"{base_url}/{href}").json()
    assert manifest["name"] == "Stock Guide"
    for icon in manifest["icons"]:
        assert page.request.get(f"{base_url}/{icon['src']}").ok, icon["src"]
    assert page.request.get(f"{base_url}/icons/icon-180.png").ok


def test_service_worker_registers_and_takes_control(chat, page):
    wait_for_worker(page)
    assert page.evaluate("navigator.serviceWorker.controller.scriptURL").endswith("/sw.js")


def test_chat_still_works_with_the_service_worker_in_control(chat, page):
    wait_for_worker(page)
    chat.reply(answer("Still fine."))
    page.locator("#input").fill("hi")
    page.locator("#send").click()
    expect(page.locator(".msg.assistant .body")).to_have_text("Still fine.")
    # The request reached the stub every time: the worker never answered it from a cache.
    page.locator("#input").fill("again")
    page.locator("#send").click()
    expect(page.locator(".msg.assistant .body").nth(1)).to_have_text("Still fine.")
    assert len(chat.requests) == 2


def test_api_responses_are_never_stored_by_the_worker(chat, page):
    wait_for_worker(page)
    page.reload()  # the first load predates the worker; this one goes through it
    chat.reply(answer("Live answer."))
    page.locator("#input").fill("hi")
    page.locator("#send").click()
    expect(page.locator(".msg.assistant .body")).to_have_text("Live answer.")
    cached = page.evaluate(
        """async () => {
            const urls = [];
            for (const name of await caches.keys()) {
                for (const req of await (await caches.open(name)).keys()) urls.push(new URL(req.url).pathname);
            }
            return urls;
        }"""
    )
    assert cached, "expected the app files to be cached"
    assert not any(path.startswith("/api/") for path in cached)


def test_offline_visit_to_an_unseen_page_shows_the_friendly_message(chat, page, base_url):
    wait_for_worker(page)
    page.context.set_offline(True)
    page.goto(f"{base_url}/never-visited")
    expect(page.locator("h1")).to_have_text("You're offline")
    page.context.set_offline(False)


def test_app_shell_reopens_offline_from_the_last_good_copy(chat, page, base_url):
    wait_for_worker(page)
    page.reload()  # make sure the shell was fetched while the worker was in control
    page.context.set_offline(True)
    page.goto(base_url)
    expect(page.locator("#input")).to_be_visible()
    page.context.set_offline(False)
