"""Settings tab for release scoring/matching — registered from monitored branch."""
from __future__ import annotations

from shelfmark.core.settings_registry import (
    register_settings,
    NumberField,
    CheckboxField,
    SelectField,
    TagListField,
    OrderableListField,
    HeadingField,
)


# ---------------------------------------------------------------------------
# Option helpers
# ---------------------------------------------------------------------------

def _get_release_priority_source_options(content_type: str) -> list[dict[str, str]]:
    """Return release source options for the given content type."""
    from shelfmark.release_sources import list_available_sources

    options: list[dict[str, str]] = []
    for source in list_available_sources():
        supported = source.get("supported_content_types") or []
        if content_type not in supported:
            continue

        source_name = str(source.get("name") or "").strip()
        display_name = str(source.get("display_name") or source_name).strip()
        if not source_name or not display_name:
            continue

        state_text = "enabled" if source.get("enabled") else "disabled"
        options.append(
            {
                "id": f"source:{source_name}",
                "label": f"Source · {display_name}",
                "description": f"Release source ({state_text}).",
            }
        )
    return options


def _get_release_priority_prowlarr_indexer_options() -> list[dict[str, str]]:
    """Return Prowlarr indexer options for release priority controls."""
    from shelfmark.core.config import config
    from shelfmark.core.utils import normalize_http_url

    raw_url = config.get("PROWLARR_URL", "")
    api_key = config.get("PROWLARR_API_KEY", "")
    if not raw_url or not api_key:
        return []

    url = normalize_http_url(raw_url)
    if not url:
        return []

    try:
        from shelfmark.release_sources.prowlarr.api import ProwlarrClient

        client = ProwlarrClient(url, api_key)
        indexers = client.get_enabled_indexers_detailed()
    except Exception:
        return []

    options: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for idx in indexers:
        name = str(idx.get("name") or "").strip()
        if not name:
            continue

        option_id = f"indexer:{name}"
        option_key = option_id.lower()
        if option_key in seen_ids:
            continue
        seen_ids.add(option_key)

        protocol = str(idx.get("protocol") or "").strip().lower()
        has_books = bool(idx.get("has_books", False))
        detail_bits = [bit for bit in [protocol if protocol else None, "books" if has_books else None] if bit]
        detail = f" ({', '.join(detail_bits)})" if detail_bits else ""

        options.append(
            {
                "id": option_id,
                "label": f"Indexer · {name}",
                "description": f"Prowlarr indexer{detail}.",
            }
        )

    options.sort(key=lambda item: item["label"].lower())
    return options


def _get_release_priority_options(content_type: str) -> list[dict[str, str]]:
    """Build combined source + indexer options for release priority settings."""
    source_options = _get_release_priority_source_options(content_type)
    indexer_options = _get_release_priority_prowlarr_indexer_options()
    return [*source_options, *indexer_options]


def _get_ebook_release_priority_options() -> list[dict[str, str]]:
    return _get_release_priority_options("ebook")


def _get_audiobook_release_priority_options() -> list[dict[str, str]]:
    return _get_release_priority_options("audiobook")


def _get_ebook_format_priority_options() -> list[dict[str, str]]:
    """Return configurable ebook format priority options for release scoring."""
    from shelfmark.config.settings import _FORMAT_OPTIONS

    excluded = {"zip", "rar"}
    options: list[dict[str, str]] = []
    seen: set[str] = set()
    for fmt in _FORMAT_OPTIONS:
        value = str(fmt.get("value") or "").strip().lower()
        if not value or value in excluded or value in seen:
            continue
        seen.add(value)
        label = str(fmt.get("label") or value.upper())
        options.append(
            {
                "id": value,
                "label": label,
                "description": "Preferred ebook format when ranking close matches.",
            }
        )
    return options


def _get_audiobook_format_priority_options() -> list[dict[str, str]]:
    """Return configurable audiobook format priority options for release scoring."""
    ordered_formats = ["m4b", "mp3", "m4a", "flac", "opus"]
    options: list[dict[str, str]] = []
    for fmt in ordered_formats:
        options.append(
            {
                "id": fmt,
                "label": fmt.upper(),
                "description": "Preferred audiobook format when ranking close matches.",
            }
        )
    return options


# ---------------------------------------------------------------------------
# Settings tab registration
# ---------------------------------------------------------------------------

@register_settings("release_scoring", "Release Scoring", icon="wrench", order=14)
def release_scoring_settings():
    """Release matching and scoring behavior."""
    return [
        HeadingField(
            key="release_scoring_heading",
            title="Release Scoring",
            description="Control how release matches are scored and rejected for universal-mode release searches.",
        ),
        CheckboxField(
            key="SHOW_RELEASE_MATCH_SCORE",
            label="Show Match Score in Release List",
            description="Display the Match score badge in release rows.",
            default=True,
        ),
        SelectField(
            key="RELEASE_PRIMARY_DEFAULT_ACTION",
            label="Default Download Button Action",
            description="Set the default action for the main download button. Uses the same options as the action dropdown.",
            options=[
                {
                    "value": "ebook_interactive_search",
                    "label": "eBook — Interactive Search",
                    "description": "Main button opens eBook interactive release picker.",
                },
                {
                    "value": "ebook_auto_search_download",
                    "label": "eBook — Auto Search + Download",
                    "description": "Main button runs eBook auto search and downloads when match score passes cutoff.",
                },
                {
                    "value": "audiobook_interactive_search",
                    "label": "Audiobook — Interactive Search",
                    "description": "Main button opens audiobook interactive release picker.",
                },
                {
                    "value": "audiobook_auto_search_download",
                    "label": "Audiobook — Auto Search + Download",
                    "description": "Main button runs audiobook auto search and downloads when match score passes cutoff.",
                },
            ],
            default="ebook_interactive_search",
        ),
        NumberField(
            key="AUTO_DOWNLOAD_MIN_MATCH_SCORE",
            label="Auto-Download Minimum Match Score",
            description="Minimum match score required before auto-download should accept a release.",
            default=75,
            min_value=0,
            max_value=100,
        ),
        CheckboxField(
            key="RELEASE_PREFER_FREELEECH_OR_DIRECT",
            label="Prioritize FreeLeech or Direct Download",
            description="Add +10 ranking boost to releases marked freeleech or from Direct Download, after title/author cutoffs are met.",
            default=False,
        ),
        OrderableListField(
            key="EBOOK_RELEASE_PRIORITY",
            label="eBook Source & Indexer Priority",
            description="Boost preferred eBook sources/indexers when ranking close matches. Drag to reorder.",
            options=_get_ebook_release_priority_options,
            default=[],
        ),
        OrderableListField(
            key="AUDIOBOOK_RELEASE_PRIORITY",
            label="Audiobook Source & Indexer Priority",
            description="Boost preferred audiobook sources/indexers when ranking close matches. Drag to reorder.",
            options=_get_audiobook_release_priority_options,
            default=[],
        ),
        OrderableListField(
            key="EBOOK_FORMAT_PRIORITY",
            label="eBook Format Priority",
            description="Boost preferred eBook formats by priority order (+5 per rank step). Applied only after strong metadata matching.",
            options=_get_ebook_format_priority_options,
            default=[],
        ),
        OrderableListField(
            key="AUDIOBOOK_FORMAT_PRIORITY",
            label="Audiobook Format Priority",
            description="Boost preferred audiobook formats by priority order (+5 per rank step). Applied only after strong metadata matching.",
            options=_get_audiobook_format_priority_options,
            default=[],
        ),
        TagListField(
            key="RELEASE_MATCH_FORBIDDEN_TERMS",
            label="Rejected Terms",
            description="Release titles containing these terms are hard-rejected.",
            default=["abridged", "sample", "excerpt", "summary", "book summary"],
            normalize_urls=False,
        ),
        NumberField(
            key="RELEASE_MATCH_MIN_TITLE_SCORE",
            label="Minimum Title Score",
            description="Hard-reject releases when title match score is below this value.",
            default=24,
            min_value=0,
            max_value=60,
        ),
        NumberField(
            key="RELEASE_MATCH_MIN_AUTHOR_SCORE",
            label="Minimum Author Score",
            description="Hard-reject releases when author match score is below this value (if author exists on release).",
            default=8,
            min_value=0,
            max_value=30,
        ),
    ]
