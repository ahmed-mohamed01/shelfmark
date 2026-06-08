"""Sized-thumbnail cache for monitored cover/portrait images.

Branch-only companion to the upstream ``image_cache.ImageCacheService``. The
monitored grid renders cover/portrait tiles at ~150px while the source assets
(especially author portraits) can be several hundred KB at 400-1200px wide, so
the browser was downloading and decoding ~10-20x more pixels than it displayed.

This module adds an on-the-fly resize step that reuses the existing disk LRU:
the resized WEBP variant is stored under ``{cache_id}_w{width}`` alongside the
full-size original, so nothing in the upstream cache needs to change. The
full-size ``/api/covers`` path is left untouched for detail views.
"""

from __future__ import annotations

import io

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

# Detect Pillow once at import. If it's missing, the resize feature can't work
# and we degrade to serving the full-size source — but LOUDLY, so a deploy that
# forgot the dependency doesn't silently ship full-resolution images (which
# looks like the optimization is working when it isn't). Previously a broad
# per-call ``except`` swallowed the ImportError identically to a bad-decode.
try:
    from PIL import Image as _PIL_Image

    _PILLOW_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only on deploys without Pillow
    _PIL_Image = None  # type: ignore[assignment]
    _PILLOW_AVAILABLE = False
    logger.warning(
        "Pillow is not installed — monitored thumbnails will serve full-size "
        "images instead of resized variants. Install `pillow` to enable the "
        "thumbnail optimization."
    )

# WEBP is universally supported by modern browsers and ~25-35% smaller than the
# equivalent JPEG at the same perceived quality.
_WEBP_QUALITY = 82

# Clamp requested widths to a small allowlist so the on-disk cache can't be
# inflated with arbitrary sizes, and so the frontend's srcset descriptors stay
# predictable. Covers/portraits are ~2:3, so heights are bounded to 2x width.
ALLOWED_THUMB_WIDTHS: tuple[int, ...] = (150, 300, 450)


def _variant_cache_id(cache_id: str, width: int) -> str:
    return f"{cache_id}_w{width}"


def get_or_create_thumbnail(
    cache_id: str,
    *,
    url: str | None,
    width: int,
) -> tuple[bytes, str] | None:
    """Return ``(bytes, content_type)`` for a width-resized WEBP variant.

    Reuses ``ImageCacheService``: the variant is cached under
    ``{cache_id}_w{width}``; the full-size source is reused from the cache or
    fetched from *url* on miss. Returns ``None`` when the source can't be
    obtained. If Pillow can't decode/encode the source, falls back to serving
    the original bytes so the tile still renders.
    """
    from shelfmark.core.image_cache import get_image_cache

    cache = get_image_cache()

    variant_id = _variant_cache_id(cache_id, width)
    cached_variant = cache.get(variant_id)
    if cached_variant is not None:
        return cached_variant

    # Obtain the full-size source: cache hit, or fetch from origin on miss.
    source = cache.get(cache_id)
    if source is None and url:
        source = cache.fetch_and_cache(cache_id, url)
    if source is None:
        return None

    # Pillow missing → can't resize; serve the source. The one-time warning at
    # import already flagged this, so don't repeat it per tile.
    if not _PILLOW_AVAILABLE:
        return source

    source_bytes, _source_type = source
    try:
        with _PIL_Image.open(io.BytesIO(source_bytes)) as src_img:
            img = src_img if src_img.mode in ("RGB", "RGBA") else src_img.convert("RGB")
            # thumbnail() only ever downscales and preserves aspect ratio; the
            # height bound keeps portrait covers from being clipped.
            img.thumbnail((width, width * 2))
            buf = io.BytesIO()
            img.save(buf, format="WEBP", quality=_WEBP_QUALITY, method=4)
            webp_bytes = buf.getvalue()
    except (OSError, ValueError) as exc:
        # A genuinely undecodable/unencodable image (truncated download, exotic
        # format) — fall back to the source for this one tile.
        logger.debug("Thumbnail resize failed for %s @w%d: %s", cache_id, width, exc)
        return source

    cache.put(variant_id, webp_bytes, "image/webp")
    return webp_bytes, "image/webp"
