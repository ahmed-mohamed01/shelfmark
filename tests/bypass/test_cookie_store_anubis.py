"""Anubis clearance survives the cookie filter, and its interstitial reads as a challenge.

Anubis-protected hosts (server.elscione.com sits behind Cloudflare *and* Anubis) need
both passes on every plain HTTP request. The filter used to keep only the Cloudflare
half, so the next request was challenged by Anubis again.
"""

import pytest

import shelfmark.bypass.cookie_store as cs
import shelfmark.bypass.internal_bypasser as ib


@pytest.fixture(autouse=True)
def _clean_cookie_store(monkeypatch):
    monkeypatch.setattr(cs, "_cf_cookies", {})
    monkeypatch.setattr(cs, "_cf_user_agents", {})
    monkeypatch.setattr(cs, "_get_full_cookie_domains", set)


class _Cookie:
    def __init__(self, name, value="v", domain="server.elscione.com"):
        self.name = name
        self.value = value
        self.expires = None
        self.domain = domain
        self.path = "/"
        self.secure = True


@pytest.mark.parametrize(
    "name",
    [
        "techaro.lol-anubis-auth",
        "techaro.lol-anubis-cookie-verification",
        "within.website-x-cmd-anubis-auth",
    ],
)
def test_anubis_cookies_are_kept(name):
    assert cs._should_extract_cookie(name, extract_all=False)


def test_unrelated_cookies_are_still_dropped():
    assert not cs._should_extract_cookie("session_id", extract_all=False)
    assert not cs._should_extract_cookie("anubis", extract_all=False)


def test_cloudflare_and_anubis_clearance_are_stored_together():
    cs.store_extracted_cookies(
        url="https://server.elscione.com/",
        cookies=[
            _Cookie("cf_clearance", "cf"),
            _Cookie("techaro.lol-anubis-auth", "auth"),
            _Cookie("techaro.lol-anubis-cookie-verification", "verify"),
            _Cookie("_ga", "tracking"),
        ],
        user_agent="UA/1.0",
    )

    assert cs.get_cf_cookies_for_domain("server.elscione.com") == {
        "cf_clearance": "cf",
        "techaro.lol-anubis-auth": "auth",
        "techaro.lol-anubis-cookie-verification": "verify",
    }
    assert cs.get_cf_user_agent_for_domain("server.elscione.com") == "UA/1.0"


@pytest.mark.parametrize(
    "title",
    ["Making sure you're not a bot!", "Making sure you’re not a bot!"],
)
def test_anubis_interstitial_is_not_bypassed(title):
    body = (
        f"{title} Loading... Please wait a moment while we ensure the security of your "
        "connection. Protected by Anubis From Techaro."
    )
    assert not ib._is_bypassed_content(title, body, "https://server.elscione.com/")


def test_a_real_listing_page_is_bypassed():
    body = "Index of / " + " ".join(f"Folder {i}" for i in range(20))
    assert ib._is_bypassed_content("Elscione", body, "https://server.elscione.com/")
