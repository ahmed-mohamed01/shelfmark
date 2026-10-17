"""Elscione release source - searches the local copy of the Elscione index.

Every file the mirrors host is already in the cached index (see index.py), so search
is a local substring match, not a scrape. The index has no author field, so author
terms are deliberately never added to a query: they would hide every match.
"""

from typing import TYPE_CHECKING, ClassVar

from shelfmark.core.config import config
from shelfmark.core.logger import setup_logger
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

if TYPE_CHECKING:
    from shelfmark.core.search_plan import ReleaseSearchPlan
    from shelfmark.metadata_providers import BookMetadata

logger = setup_logger(__name__)

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


def _build_queries(plan: ReleaseSearchPlan, book: BookMetadata) -> list[str]:
    """Build ordered, de-duplicated title queries.

    Never the author: the index has no author data, so an author term would
    filter every release out. Each title variant gets its own query, and the
    first one with hits wins (the caller tries them in order).
    """
    queries: list[str] = []
    if plan.manual_query:
        queries.append(plan.manual_query.strip())
    else:
        for variant in plan.title_variants:
            title = (variant.title or "").strip()
            if title:
                queries.append(title)
    if not queries and book.title:
        queries.append(book.title.strip())

    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        if not query or query.casefold() in seen:
            continue
        seen.add(query.casefold())
        deduped.append(query)
    return deduped


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

        for query in queries:
            # index.search raises SourceUnavailableError while the index builds.
            hits = index.search(query, max_results)
            logger.info("Searching Elscione for: %s (%d hits)", query, len(hits))
            if hits:
                return [self._entry_to_release(entry) for entry in hits]
        return []

    def _entry_to_release(self, entry: index.IndexEntry) -> Release:
        """Map an index entry to a Release.

        ``source_id`` is the bare root-relative path, as requested: it contains
        slashes and spaces, which the API routes accept. ``download_url`` stays
        None because the handler builds the URL from the index entry, never from
        this payload.
        """
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
            extra={
                "book_title": entry.folder,
                "root": entry.root,
                "index_fetched_at": index.fetched_at_iso(),
                "language": None,  # the index has no language data; nothing is guessed
            },
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
