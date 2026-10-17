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

# The metadata title exactly as BookOrbit/Hardcover send it: bracketed
# annotation, subtitle and volume marker, none of which the file names repeat.
SEVENTH_TIME_LOOP_TITLE = (
    "7th Time Loop: The Villainess Enjoys a Carefree Life "
    "Married to Her Worst Enemy! (Light Novel), Vol. 1"
)
# A series whose folder matches the metadata's short form but whose file
# names never carry the subtitle, plus an omnibus that spans volumes.
SEVENTH_TIME_LOOP_INDEX = {
    "name": ROOT_NAME,
    "path": ".",
    "folders": [
        {
            "name": "7th Time Loop",
            "path": "7th Time Loop",
            "folders": [],
            "files": [
                {
                    "name": "7th Time Loop - Volume 01 [Seven Seas][Kobo].epub",
                    "path": "7th Time Loop/7th Time Loop - Volume 01 [Seven Seas][Kobo].epub",
                    "size": 1024,
                },
                {
                    "name": "7th Time Loop - Volume 02 [Seven Seas][Kobo].epub",
                    "path": "7th Time Loop/7th Time Loop - Volume 02 [Seven Seas][Kobo].epub",
                    "size": 2048,
                },
                {
                    "name": "7th Time Loop - Volume 10 [Seven Seas][Kobo].epub",
                    "path": "7th Time Loop/7th Time Loop - Volume 10 [Seven Seas][Kobo].epub",
                    "size": 4096,
                },
                {
                    "name": "7th Time Loop - Complete [enjugroup][Kobo].epub",
                    "path": "7th Time Loop/7th Time Loop - Complete [enjugroup][Kobo].epub",
                    "size": 8192,
                },
            ],
        },
        {
            "name": "Omnibus Collection",
            "path": "Omnibus Collection",
            "folders": [],
            "files": [
                {
                    "name": "Omnibus Collection - Volume 01-17.epub",
                    "path": "Omnibus Collection/Omnibus Collection - Volume 01-17.epub",
                    "size": 16384,
                },
            ],
        },
        # Two series sharing the word "Magic": a subtitle-bearing query for a
        # third, unknown one must not fall back to the single word "Magic".
        {
            "name": "Magic Academy",
            "path": "Magic Academy",
            "folders": [],
            "files": [
                {
                    "name": "Magic Academy - Vol 1.epub",
                    "path": "Magic Academy/Magic Academy - Vol 1.epub",
                    "size": 512,
                },
            ],
        },
        {
            "name": "Magic Knight",
            "path": "Magic Knight",
            "folders": [],
            "files": [
                {
                    "name": "Magic Knight - Vol 2.epub",
                    "path": "Magic Knight/Magic Knight - Vol 2.epub",
                    "size": 1024,
                },
            ],
        },
        # A series with both numbered volumes and an omnibus spanning them, for
        # range requests.
        {
            "name": "Range Series",
            "path": "Range Series",
            "folders": [],
            "files": [
                {
                    "name": "Range Series - Volume 01.epub",
                    "path": "Range Series/Range Series - Volume 01.epub",
                    "size": 2048,
                },
                {
                    "name": "Range Series - Volume 02.epub",
                    "path": "Range Series/Range Series - Volume 02.epub",
                    "size": 2048,
                },
                {
                    "name": "Range Series - Volume 03.epub",
                    "path": "Range Series/Range Series - Volume 03.epub",
                    "size": 2048,
                },
                {
                    "name": "Range Series - Volume 10.epub",
                    "path": "Range Series/Range Series - Volume 10.epub",
                    "size": 2048,
                },
                {
                    "name": "Range Series - Volume 01-17.epub",
                    "path": "Range Series/Range Series - Volume 01-17.epub",
                    "size": 4096,
                },
            ],
        },
    ],
    "files": [],
}


# A series whose numbering stops at Volume 02: a series position past the
# last file must fall back to these hits instead of hiding them.
TWO_VOLUME_INDEX = {
    "name": ROOT_NAME,
    "path": ".",
    "folders": [
        {
            "name": "Trilogia",
            "path": "Trilogia",
            "folders": [],
            "files": [
                {
                    "name": "Trilogia - Volume 01.epub",
                    "path": "Trilogia/Trilogia - Volume 01.epub",
                    "size": 1024,
                },
                {
                    "name": "Trilogia - Volume 02.epub",
                    "path": "Trilogia/Trilogia - Volume 02.epub",
                    "size": 2048,
                },
            ],
        },
    ],
    "files": [],
}


# A series whose second file carries an absurd digit run where its volume
# number would be: float() answers inf, which must count as no volume.
ABSURD_VOLUME_NAME = "Weird Series - Volume " + "9" * 309 + ".epub"
ABSURD_VOLUME_PATH = "Weird Series/" + ABSURD_VOLUME_NAME
ABSURD_VOLUME_INDEX = {
    "name": ROOT_NAME,
    "path": ".",
    "folders": [
        {
            "name": "Weird Series",
            "path": "Weird Series",
            "folders": [],
            "files": [
                {
                    "name": "Weird Series - Volume 01.epub",
                    "path": "Weird Series/Weird Series - Volume 01.epub",
                    "size": 1024,
                },
                {"name": ABSURD_VOLUME_NAME, "path": ABSURD_VOLUME_PATH, "size": 2048},
            ],
        },
    ],
    "files": [],
}


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


def seventh_time_loop_entries(max_bytes: int = MAX_BYTES) -> dict:
    """Flattened entries of SEVENTH_TIME_LOOP_INDEX, keyed by path."""
    return index.flatten_index(SEVENTH_TIME_LOOP_INDEX, max_bytes=max_bytes).entries


def two_volume_entries(max_bytes: int = MAX_BYTES) -> dict:
    """Flattened entries of TWO_VOLUME_INDEX, keyed by path."""
    return index.flatten_index(TWO_VOLUME_INDEX, max_bytes=max_bytes).entries


def absurd_volume_entries(max_bytes: int = MAX_BYTES) -> dict:
    """Flattened entries of ABSURD_VOLUME_INDEX, keyed by path."""
    return index.flatten_index(ABSURD_VOLUME_INDEX, max_bytes=max_bytes).entries


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
