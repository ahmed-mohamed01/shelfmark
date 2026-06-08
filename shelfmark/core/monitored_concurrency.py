"""Small bounded-concurrency helper shared by the monitored sync/scan paths.

The monitored pipeline fans out several independent, I/O-bound batches of work
(per-book Grimmory detail fetches, ABS item-format fetches, cover prefetches,
Hardcover pagination waves, per-source release searches). They were each an
inline ``ThreadPoolExecutor`` with the same shape; this centralizes that so the
worker-count policy and result ordering live in one place.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

# Default ceiling on concurrent in-flight requests per fan-out. Kept modest to
# stay polite to external services (Hardcover / ABS / Grimmory / indexers).
DEFAULT_MAX_WORKERS = 6


def bounded_map[T, R](
    func: Callable[[T], R],
    items: Sequence[T],
    *,
    max_workers: int = DEFAULT_MAX_WORKERS,
) -> list[R]:
    """Apply *func* to each item concurrently, returning results in input order.

    Bounds concurrency to ``min(max_workers, len(items))``. Runs a single item
    inline (no pool overhead) and returns ``[]`` for an empty input. Exceptions
    raised by *func* propagate from the corresponding result position, exactly
    as ``ThreadPoolExecutor.map`` does — callers that want per-item isolation
    should catch inside *func* and return a sentinel.
    """
    items = list(items)
    if not items:
        return []
    if len(items) == 1:
        return [func(items[0])]
    workers = max(1, min(max_workers, len(items)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(func, items))
