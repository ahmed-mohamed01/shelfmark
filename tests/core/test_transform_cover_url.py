"""transform_cover_url leaves inline and local images alone."""

from shelfmark.core.utils import transform_cover_url


def test_data_url_is_not_proxied(monkeypatch):
    # A data: URL is the image itself; wrapping it in the cover proxy would make the
    # proxy try to download it, fail, and show no cover at all.
    monkeypatch.setattr("shelfmark.config.env.is_covers_cache_enabled", lambda: True)
    data_url = "data:image/svg+xml;utf8,%3Csvg%3E%3C%2Fsvg%3E"
    assert transform_cover_url(data_url, "any") == data_url


def test_local_url_is_not_proxied(monkeypatch):
    monkeypatch.setattr("shelfmark.config.env.is_covers_cache_enabled", lambda: True)
    assert transform_cover_url("/api/covers/x?url=abc", "any") == "/api/covers/x?url=abc"


def test_external_url_is_proxied_when_caching(monkeypatch):
    monkeypatch.setattr("shelfmark.config.env.is_covers_cache_enabled", lambda: True)
    assert transform_cover_url("https://covers.example/a.jpg", "book1").startswith(
        "/api/covers/book1?url="
    )
