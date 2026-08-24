"""Browser-session reuse cache (branch-only ``monitored_bypass_session``).

Reuse is default-off (a pure create/close passthrough = upstream behaviour). When on, a
solved driver is kept and handed back for the next same-domain bypass, but a driver that
produced no page (challenge still up), a cancelled bypass, a domain change, or a used-up
driver is never reused — the safety rules an adversarial review said were load-bearing.
"""

from __future__ import annotations

import asyncio

import pytest

from shelfmark.bypass import monitored_bypass_session as session


class FakeDriver:
    def __init__(self, tag: int) -> None:
        self.tag = tag


@pytest.fixture
def harness(monkeypatch):
    created: list[FakeDriver] = []
    closed: list[int] = []

    async def create(_url: str) -> FakeDriver:
        d = FakeDriver(len(created))
        created.append(d)
        return d

    async def close(d: FakeDriver) -> None:
        closed.append(d.tag)

    session._reset_state()
    monkeypatch.setattr(session, "_MAX_USES", 25)
    monkeypatch.setattr(session, "_MAX_AGE_SECONDS", 600.0)
    yield created, closed, create, close
    session._reset_state()


def _on(monkeypatch, enabled: bool) -> None:
    monkeypatch.setattr(session, "reuse_enabled", lambda: enabled)


def _run(coro):
    return asyncio.run(coro)


URL_PK = "https://annas-archive.pk/search?q=a"
URL_PK2 = "https://annas-archive.pk/search?q=b"
URL_GL = "https://annas-archive.gl/search?q=a"


class TestReuseOff:
    def test_acquire_creates_and_release_closes(self, harness, monkeypatch):
        created, closed, create, close = harness
        _on(monkeypatch, False)
        d = _run(session.acquire(URL_PK, create, close))
        _run(session.release(d, keep=True, close_fn=close))  # keep ignored when off
        assert len(created) == 1
        assert closed == [0]  # closed every time, like upstream
        assert session._driver is None


class TestReuseOn:
    def test_second_same_domain_reuses(self, harness, monkeypatch):
        created, closed, create, close = harness
        _on(monkeypatch, True)
        d1 = _run(session.acquire(URL_PK, create, close))
        _run(session.release(d1, keep=True, close_fn=close))
        d2 = _run(session.acquire(URL_PK2, create, close))
        assert d1 is d2
        assert len(created) == 1
        assert closed == []  # kept alive, not closed

    def test_empty_result_discards_the_driver(self, harness, monkeypatch):
        # The poison guard: a driver that produced "" must never be reused.
        _created, closed, create, close = harness
        _on(monkeypatch, True)
        d = _run(session.acquire(URL_PK, create, close))
        _run(session.release(d, keep=False, close_fn=close))
        assert closed == [0]
        assert session._driver is None

    def test_domain_change_closes_old_and_creates_new(self, harness, monkeypatch):
        created, closed, create, close = harness
        _on(monkeypatch, True)
        a = _run(session.acquire(URL_PK, create, close))
        _run(session.release(a, keep=True, close_fn=close))
        b = _run(session.acquire(URL_GL, create, close))
        assert b is not a
        assert closed == [0]  # old .pk driver closed before .gl created
        assert len(created) == 2

    def test_recycles_after_use_cap(self, harness, monkeypatch):
        created, _closed, create, close = harness
        _on(monkeypatch, True)
        monkeypatch.setattr(session, "_MAX_USES", 3)
        for _ in range(5):
            d = _run(session.acquire(URL_PK, create, close))
            _run(session.release(d, keep=True, close_fn=close))
        assert len(created) == 2  # one recycle when the cap is hit

    def test_recycles_after_max_age(self, harness, monkeypatch):
        _created, closed, create, close = harness
        _on(monkeypatch, True)
        monkeypatch.setattr(session, "_MAX_AGE_SECONDS", -1.0)  # always "too old"
        a = _run(session.acquire(URL_PK, create, close))
        _run(session.release(a, keep=True, close_fn=close))
        b = _run(session.acquire(URL_PK, create, close))
        assert b is not a
        assert closed == [0]

    def test_discard_closes_and_clears(self, harness, monkeypatch):
        _created, closed, create, close = harness
        _on(monkeypatch, True)
        d = _run(session.acquire(URL_PK, create, close))
        _run(session.discard(d, close))
        assert closed == [0]
        assert session._driver is None

    def test_shutdown_closes_cached(self, harness, monkeypatch):
        _created, closed, create, close = harness
        _on(monkeypatch, True)
        d = _run(session.acquire(URL_PK, create, close))
        _run(session.release(d, keep=True, close_fn=close))  # cached
        _run(session.shutdown(close))
        assert closed == [0]
        assert session._driver is None

    def test_create_failure_leaves_no_cached_driver(self, harness, monkeypatch):
        _created, _closed, _create, close = harness
        _on(monkeypatch, True)

        async def boom(_url):
            raise RuntimeError("browser start failed")

        with pytest.raises(RuntimeError):
            _run(session.acquire(URL_PK, boom, close))
        assert session._driver is None  # never cache a half-created driver

    def test_reuse_off_drops_a_stale_cache(self, harness, monkeypatch):
        # A driver cached during a reuse-ON window is dropped when reuse is toggled off,
        # so it can't leak for the process lifetime (the live-toggle-off strand).
        _created, closed, create, close = harness
        _on(monkeypatch, True)
        d = _run(session.acquire(URL_PK, create, close))
        _run(session.release(d, keep=True, close_fn=close))  # cached
        _on(monkeypatch, False)
        fresh = _run(session.acquire(URL_PK, create, close))
        assert closed == [0]  # the stale cached driver was closed
        assert fresh is not d
        assert session._driver is None  # off path never caches

    def test_release_of_non_cached_driver_only_closes_it(self, harness, monkeypatch):
        # The timeout-backstop race can leave a request holding a driver that is no longer
        # the cached one; releasing it must just close it and leave the cache intact.
        _created, closed, create, close = harness
        _on(monkeypatch, True)
        cached = _run(session.acquire(URL_PK, create, close))
        _run(session.release(cached, keep=True, close_fn=close))  # cached stays
        detached = FakeDriver(99)
        _run(session.release(detached, keep=True, close_fn=close))
        assert closed == [99]  # only the detached one closed
        assert session._driver is cached  # cache untouched

    def test_egress_key_change_recreates(self, harness, monkeypatch):
        # A DNS/proxy change (different routing key) must not reuse a browser whose baked-in
        # host-resolver-rules are now stale.
        _created, closed, create, close = harness
        _on(monkeypatch, True)
        keys = iter([("pk", "dns-A"), ("pk", "dns-A"), ("pk", "dns-B")])
        monkeypatch.setattr(session, "_session_key", lambda _url: next(keys))
        a = _run(session.acquire(URL_PK, create, close))  # key dns-A -> create
        _run(session.release(a, keep=True, close_fn=close))
        b = _run(session.acquire(URL_PK, create, close))  # key dns-A -> reuse
        assert b is a
        c = _run(session.acquire(URL_PK, create, close))  # key dns-B -> recreate
        assert c is not a
        assert closed == [0]  # old driver closed before the new one
