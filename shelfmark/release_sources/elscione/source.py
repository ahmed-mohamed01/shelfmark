"""Elscione release source - searches the local copy of the Elscione index.

Every file the mirrors host is already in the cached index (see index.py), so search
is a local substring match, not a scrape. The index has no author field, so author
terms are deliberately never added to a query: they would hide every match.
"""

import hashlib
import math
from typing import TYPE_CHECKING, ClassVar

from shelfmark.core.config import config
from shelfmark.core.utils import transform_cover_url
from shelfmark.release_sources import (
    BrowseRecord,
    ColumnAlign,
    ColumnColorHint,
    ColumnRenderType,
    ColumnSchema,
    Release,
    ReleaseColumnConfig,
    ReleaseProtocol,
    ReleaseSource,
    register_source,
)
from shelfmark.release_sources.elscione import index
from shelfmark.release_sources.elscione.placeholder import placeholder_cover

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

_DEFAULT_MAX_RESULTS = 50

_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")
_SIZE_BASE = 1024


def _human_size(size_bytes: int) -> str:
    """Format a byte count the way the other release sources do (e.g. "156.5 KB")."""
    size = float(size_bytes)
    unit_index = 0
    while size >= _SIZE_BASE and unit_index < len(_SIZE_UNITS) - 1:
        size /= _SIZE_BASE
        unit_index += 1
    if unit_index == 0:
        return f"{int(size)} {_SIZE_UNITS[unit_index]}"
    return f"{size:.1f} {_SIZE_UNITS[unit_index]}"


def _file_stem(name: str) -> str:
    """The file name without its extension."""
    head, dot, _ = name.rpartition(".")
    return head if dot else name


def _build_queries(plan: ReleaseSearchPlan, book: BookMetadata) -> list[tuple[str, bool]]:
    """Build ordered, de-duplicated ``(query, allow_fallback)`` pairs.

    Never the author: the index has no author data, so an author term would
    filter every release out. Each title variant gets its own query, and the
    first one with hits wins (the caller tries them in order). A manual query
    is exactly what the user typed, so it is never shortened to a series name
    (``allow_fallback`` False); metadata titles are shortened, because their
    long forms never match the file names.
    """
    candidates: list[tuple[str, bool]] = []
    if plan.manual_query:
        candidates.append((plan.manual_query.strip(), False))
    else:
        for variant in plan.title_variants:
            title = (variant.title or "").strip()
            if title:
                candidates.append((title, True))
    if not candidates and book.title:
        candidates.append((book.title.strip(), True))

    queries: list[tuple[str, bool]] = []
    seen: set[str] = set()
    for query, allow_fallback in candidates:
        if not query or query.casefold() in seen:
            continue
        seen.add(query.casefold())
        queries.append((query, allow_fallback))
    return queries


def _series_volume(book: BookMetadata) -> float | None:
    """The book's series position as a volume preference, when usable.

    A provider can move the volume out of the title and into the series
    position ("#1 of 7"), leaving the plan's title variants with no volume to
    parse, so the metadata's number is the only volume the request has.
    Values that are not finite positive whole numbers are ignored: position 0
    or NaN would filter out every numbered file, and a fractional position
    (1.5) names a novella that site numbering usually does not carry, so it is
    left out of the request rather than risk narrowing the hits to nothing.
    """
    position = book.series_position
    if position is None:
        return None
    volume = float(position)
    if not math.isfinite(volume) or volume <= 0 or not volume.is_integer():
        return None
    return volume


@register_source("elscione")
class ElscioneSource(ReleaseSource):
    """Release source that searches the cached Elscione file index."""

    name = "elscione"
    display_name = "Elscione"
    supported_content_types: ClassVar[list[str]] = ["ebook"]  # the index is epub/pdf only

    def is_available(self) -> bool:
        """Enabled, with the internal bypasser, which downloads require.

        With an external bypasser the index could still be fetched, but no
        download could ever succeed (FlareSolverr cannot run code in a page), so
        the source hides itself rather than offer dead releases.
        """
        return (
            config.get("ELSCIONE_ENABLED", False) is True
            and config.get("USE_CF_BYPASS", True) is True
            and config.get("USING_EXTERNAL_BYPASSER", False) is not True
        )

    def search(
        self,
        book: BookMetadata,
        plan: ReleaseSearchPlan,
        *,
        expand_search: bool = False,
        content_type: str = "ebook",
    ) -> list[Release]:
        """Search the cached index; first query with hits wins."""
        if content_type != "ebook":
            return []
        if not self.is_available():
            return []

        queries = _build_queries(plan, book)
        if not queries:
            return []
        max_results = index.coerce_int(
            config.get("ELSCIONE_MAX_RESULTS", _DEFAULT_MAX_RESULTS),
            _DEFAULT_MAX_RESULTS,
            lo=1,
            hi=200,
        )

        # The title variants may carry no volume at all (the provider moved it
        # into the series position), so the metadata's number stands in as a
        # volume preference: index.search narrows the hits to entries covering
        # it and falls back to the unfiltered hits when none does, because the
        # metadata numbering need not match the site's file numbering. A manual
        # query is what the user typed and is never narrowed by it.
        default_volume = _series_volume(book)
        for query, allow_fallback in queries:
            # index.search raises SourceUnavailableError while the index builds,
            # and logs the variant that produced whatever hits it returns.
            candidate_volume = default_volume if allow_fallback else None
            hits = index.search(
                query,
                max_results,
                allow_fallback=allow_fallback,
                default_volume=candidate_volume,
            )
            if hits:
                requested, _origin = index.requested_volume(query, candidate_volume)
                return [self._entry_to_release(entry, book, requested) for entry in hits]
        return []

    def _entry_to_release(
        self, entry: index.IndexEntry, book: BookMetadata, requested: tuple[float, float] | None
    ) -> Release:
        """Map an index entry to a Release.

        ``source_id`` is the bare root-relative path, as requested: it contains
        slashes and spaces, which the API routes accept. ``download_url`` stays
        None because the handler builds the URL from the index entry, never from
        this payload.
        """
        extra: dict = {
            "book_title": entry.folder,
            "root": entry.root,
            "index_fetched_at": index.fetched_at_iso(),
            "language": None,  # the index has no language data; nothing is guessed
        }
        if (
            requested is not None
            and book.cover_url
            and entry.volumes is not None
            and entry.volumes[0] <= requested[0]
            and requested[1] <= entry.volumes[1]
        ):
            # The release modal renders extra.preview as its leading thumbnail
            # cell; the cover belongs to the entry that is actually the
            # requested volume, not to every file the series matched.
            # Proxied here with a slash-free cache id: the release serializer would
            # otherwise key the cover cache on source_id, a file path whose slashes
            # the /api/covers/<id> route cannot match. The id mixes the cover URL
            # in, so one omnibus entry (Volume 01-17) caches book #1's cover and
            # book #2's cover separately instead of serving the first forever. A
            # local URL is left alone there.
            cache_id = (
                "elscione_"
                + hashlib.sha256(f"{entry.path}\n{book.cover_url}".encode()).hexdigest()[:16]
            )
            extra["preview"] = transform_cover_url(book.cover_url, cache_id)
        else:
            # Every other row would render the grey "No Cover" box: the index
            # has no cover images, so fill the cell with a generated
            # Bauhaus-style cover keyed by the series and the entry itself.
            extra["preview"] = placeholder_cover(
                entry.folder, entry.folder, entry.volumes, entry.path
            )
        return Release(
            source="elscione",
            source_id=entry.path,
            # The extension lives in ``format``; Shelfmark's naming adds it back, so a
            # title ending in ".epub" became "<name>.epub.epub" in the library.
            title=_file_stem(entry.name),
            format=entry.format,
            size=_human_size(entry.size_bytes),
            size_bytes=entry.size_bytes,
            download_url=None,
            info_url=None,
            protocol=ReleaseProtocol.HTTP,
            indexer="Elscione",
            content_type="ebook",
            extra=extra,
        )

    def search_results_are_releases(self) -> bool:
        """Elscione search rows are concrete, directly downloadable files."""
        return True

    def get_record(
        self,
        record_id: str,
        *,
        fetch_download_count: bool = True,
    ) -> BrowseRecord | None:
        """Resolve an index path to a browse record, or None when no longer indexed."""
        entry = index.get_entry(record_id)
        if entry is None:
            return None
        return BrowseRecord(
            id=entry.path,
            title=entry.name,
            source="elscione",
            content="ebook",
            format=entry.format,
            size=_human_size(entry.size_bytes),
        )

    def get_column_config(self) -> ReleaseColumnConfig:
        """Format and size badges - the Libgen layout without the language column."""
        return ReleaseColumnConfig(
            columns=[
                ColumnSchema(
                    key="format",
                    label="Format",
                    render_type=ColumnRenderType.BADGE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                    color_hint=ColumnColorHint(type="map", value="format"),
                    uppercase=True,
                ),
                ColumnSchema(
                    key="size",
                    label="Size",
                    render_type=ColumnRenderType.SIZE,
                    align=ColumnAlign.CENTER,
                    width="80px",
                ),
            ],
            grid_template="minmax(0,2fr) 80px 80px",
            supported_filters=["format"],
        )
