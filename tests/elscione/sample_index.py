"""Shared fixtures for the Elscione tests: a sample index tree and state resets.

The tree covers every entry kind the flattener must handle (spec section 8):
epub, pdf, zip, bat, dll, size 0, ``..``, backslash, empty segment, control
character, duplicate, non-int size, bool size, root-level file, nested folders
and the list-of-roots form.
"""

import contextlib
import logging

from shelfmark.release_sources.elscione import index

ROOT_NAME = "Officially Translated Light Novels"
AMAZING_GRACE_PATH = "!Yaoi/Amazing Grace/Amazing Grace - Complete [enjugroup][Kindle].epub"
# The two default mirrors, in configured order.
MIRROR_1 = index.DEFAULT_MIRRORS[0]
MIRROR_2 = index.DEFAULT_MIRRORS[1]
# The live URL for the file above, captured from the working prototype; the
# quote(safe="") encoder must reproduce it byte for byte.
EXPECTED_AMAZING_GRACE_URL = (
    "https://server.elscione.com/Officially%20Translated%20Light%20Novels/"
    "%21Yaoi/Amazing%20Grace/Amazing%20Grace%20-%20Complete%20"
    "%5Benjugroup%5D%5BKindle%5D.epub"
)
# The same file on the second mirror: same path, different host.
MIRROR_2_DOWNLOAD_URL = EXPECTED_AMAZING_GRACE_URL.replace(
    "server.elscione.com", "server.elsci.one"
)

# Skip counts the sample tree produces with a 1 MiB size cap (see test_index.py).
MAX_BYTES = 1024 * 1024
EXPECTED_SKIPPED = {
    "bad_node": 2,  # "folders" not a list, and a bare string node
    "bad_entry": 4,  # str size, bool size, non-str name, negative size
    "other_format": 3,  # zip, bat, dll
    "empty": 1,
    "too_large": 1,
    "bad_path": 4,  # "..", backslash, empty segment, control character
    "duplicate": 1,
}
EXPECTED_ENTRIES = 7

SAMPLE_INDEX = {
    "name": ROOT_NAME,
    "path": ".",
    "folders": [
        {
            "name": "!Yaoi",
            "path": "!Yaoi",
            "folders": [
                {
                    "name": "Amazing Grace",
                    "path": "!Yaoi/Amazing Grace",
                    "folders": [
                        {
                            "name": "Deep",
                            "path": "!Yaoi/Amazing Grace/Deep",
                            "folders": [],
                            "files": [
                                {
                                    "name": "Deep Book.pdf",
                                    "path": "!Yaoi/Amazing Grace/Deep/Deep Book.pdf",
                                    "size": 4096,
                                },
                            ],
                        },
                    ],
                    "files": [
                        {
                            "name": "Amazing Grace - Complete [enjugroup][Kindle].epub",
                            "path": AMAZING_GRACE_PATH,
                            "size": 156484,
                        },
                        {
                            "name": "Amazing Grace - Vol 2.epub",
                            "path": "!Yaoi/Amazing Grace/Amazing Grace - Vol 2.epub",
                            "size": 2048,
                        },
                        {
                            "name": "Amazing Grace - Vol 10.epub",
                            "path": "!Yaoi/Amazing Grace/Amazing Grace - Vol 10.epub",
                            "size": 3072,
                        },
                        {
                            "name": "Amazing Grace - Vol 1.pdf",
                            "path": "!Yaoi/Amazing Grace/Amazing Grace - Vol 1.pdf",
                            "size": 1024,
                        },
                        {"name": "cover.zip", "path": "!Yaoi/Amazing Grace/cover.zip", "size": 64},
                        {"name": "setup.bat", "path": "!Yaoi/Amazing Grace/setup.bat", "size": 64},
                        {
                            "name": "library.dll",
                            "path": "!Yaoi/Amazing Grace/library.dll",
                            "size": 64,
                        },
                        {"name": "empty.pdf", "path": "!Yaoi/Amazing Grace/empty.pdf", "size": 0},
                        {
                            # Over every cap the settings allow (max 512 MiB), so it
                            # is too_large under both the test cap and the configured
                            # one, and refresh-path counts match the flatten tests.
                            "name": "huge.epub",
                            "path": "!Yaoi/Amazing Grace/huge.epub",
                            "size": 600 * MAX_BYTES,
                        },
                        {
                            "name": "updir.epub",
                            "path": "!Yaoi/Amazing Grace/../updir.epub",
                            "size": 64,
                        },
                        {
                            "name": "backslash.epub",
                            "path": "!Yaoi/Amazing Grace\\backslash.epub",
                            "size": 64,
                        },
                        {
                            "name": "blank.epub",
                            "path": "!Yaoi/Amazing Grace//blank.epub",
                            "size": 64,
                        },
                        {
                            "name": "ctrl.epub",
                            "path": "!Yaoi/Amazing Grace/ctrl\x07char.epub",
                            "size": 64,
                        },
                        {"name": "dup.epub", "path": "!Yaoi/Amazing Grace/dup.epub", "size": 64},
                        {"name": "dup.epub", "path": "!Yaoi/Amazing Grace/dup.epub", "size": 65},
                        {
                            "name": "badsize.epub",
                            "path": "!Yaoi/Amazing Grace/badsize.epub",
                            "size": "12",
                        },
                        {
                            "name": "boolsize.epub",
                            "path": "!Yaoi/Amazing Grace/boolsize.epub",
                            "size": True,
                        },
                        {"name": 123, "path": "!Yaoi/Amazing Grace/noname.epub", "size": 64},
                        {
                            "name": "negative.epub",
                            "path": "!Yaoi/Amazing Grace/negative.epub",
                            "size": -1,
                        },
                    ],
                },
            ],
            "files": [],
        },
        # "folders" is not a list: the node and its files are lost, counted bad_node.
        {"name": "NotANode", "path": "NotANode", "folders": "oops", "files": []},
        # A folder entry that is not a dict at all.
        "just a string",
    ],
    # A file sitting directly in the root: folder falls back to its own stem.
    "files": [{"name": "Root Book.epub", "path": "Root Book.epub", "size": 1000}],
}

# The index is also published as a top-level list of roots; both forms parse.
LIST_OF_ROOTS_INDEX = [
    SAMPLE_INDEX,
    {
        "name": "Another Root",
        "path": ".",
        "folders": [],
        "files": [{"name": "Solo Book.pdf", "path": "Solo Book.pdf", "size": 100}],
    },
]


def deep_tree(levels: int) -> dict:
    """A single chain of ``levels`` folders with one epub at the bottom."""
    path = "/".join(f"d{i}" for i in range(levels))
    node: dict = {
        "name": "Bottom",
        "path": path,
        "folders": [],
        "files": [{"name": "deep.epub", "path": f"{path}/deep.epub", "size": 10}],
    }
    for i in reversed(range(levels)):
        node = {"name": f"L{i}", "path": path[: i + 1], "folders": [node], "files": []}
    return node


def reset_index_state() -> None:
    """Reset the index module's process-global caches between tests."""
    with index._STATE_LOCK:
        index._entries = {}
        index._meta = {}
        index._loaded = False
        index._refreshing = False
        index._failure_count = 0
        index._backoff_until = 0.0
        index._stale_warned_at = 0.0
        index._preferred_mirror = None


def pin_mirrors(monkeypatch, bases) -> None:
    """Pin the configured mirror list (and so the download order) for one test.

    Tests that assert per-mirror call counts must not depend on whatever
    ELSCIONE_MIRRORS the real config object would answer with.
    """
    monkeypatch.setattr(index, "mirrors", lambda: list(bases))


def install_entries(entries: dict) -> None:
    """Serve ``entries`` from the index module as if they were loaded from disk."""
    reset_index_state()
    with index._STATE_LOCK:
        index._entries = dict(entries)
        index._meta = {"fetched_at": index._now_iso(), "entries": len(entries)}
        index._loaded = True


def sample_entries(max_bytes: int = MAX_BYTES) -> dict:
    """Flattened entries of SAMPLE_INDEX, keyed by path."""
    return index.flatten_index(SAMPLE_INDEX, max_bytes=max_bytes).entries


@contextlib.contextmanager
def captured_logs(logger, level=logging.WARNING):
    """Capture records from a ``setup_logger``-built logger.

    Those loggers are constructed outside the logging manager's hierarchy, so
    their records never reach the root handler caplog installs; tests attach
    a handler directly to the logger instead.
    """
    messages: list[str] = []

    class _Capture(logging.Handler):
        def emit(self, record):
            messages.append(record.getMessage())

    capture = _Capture()
    logger.addHandler(capture)
    previous_level = logger.level
    logger.setLevel(level)
    logger._cache.clear()  # the level answers are cached through the manager we bypassed
    try:
        yield messages
    finally:
        logger.removeHandler(capture)
        logger.setLevel(previous_level)
        logger._cache.clear()
