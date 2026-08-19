from __future__ import annotations

import contextlib
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from flask import Flask, Response, jsonify, request, session

from shelfmark.core.logger import setup_logger
from shelfmark.core.monitored_db_ops import fetch_entity_metadata
from shelfmark.core.monitored_downloads import write_monitored_book_attempt
from shelfmark.core.monitored_files import (
    apply_monitor_modes_for_books,
    path_within_allowed_roots,
    resolve_allowed_roots,
)
from shelfmark.core.monitored_operations import (
    compute_book_availability,
    filter_search_candidates,
    record_scan_error,
    resolve_book_auto_search_precheck,
    resolve_monitored_output_overrides,
    run_batch_sync,
    search_missing_books,
    start_author_background_sync,
    sync_availability_sources,
)
from shelfmark.core.monitored_release_scoring import parse_release_date
from shelfmark.core.monitored_types import (
    AvailabilitySyncResult,
    MonitoredEntityNotFoundError,
    MonitoredPathError,
)
from shelfmark.core.monitored_utils import (
    extract_author_photo_url,
    normalize_preferred_languages,
    transform_cached_cover_urls,
    transform_cached_event_thumbnail_urls,
)
from shelfmark.core.request_policy import PolicyMode, normalize_content_type, resolve_policy_mode
from shelfmark.core.settings_registry import load_config_file

if TYPE_CHECKING:
    from shelfmark.core.monitored_db import MonitoredDB
    from shelfmark.core.user_db import UserDB

logger = setup_logger(__name__)

# Audio file extensions used to bucket monitored_book_files rows into
# audiobook-vs-ebook for the candidates endpoint. A row counts as audiobook
# when its file_type is the explicit string "audiobook" OR an audio extension.
_AUDIO_FILE_TYPES: frozenset[str] = frozenset(
    {
        "m4b",
        "mp3",
        "m4a",
        "flac",
        "opus",
        "aac",
        "ogg",
        "wav",
        "aax",
    }
)

# Total Link: rel=preload header size we'll emit on the entities-list
# response. Kept well under common nginx proxy_buffer_size (4 KiB) so
# reverse proxies don't 502 with "upstream sent too big header".
_MAX_LINK_HEADER_BYTES = 2048


def _row_is_audiobook(file_type: str | None) -> bool:
    ft = (file_type or "").lower()
    return ft == "audiobook" or ft in _AUDIO_FILE_TYPES


def _resolve_global_monitor_user_id(user_db: UserDB) -> int:
    user = user_db.get_user(username="global")
    if user:
        return int(user["id"])
    created = user_db.create_user(
        username="global",
        password_hash=None,
        email=None,
        display_name="Global",
        auth_source="builtin",
        role="admin",
    )
    return int(created["id"])


def _resolve_visible_user_ids(
    user_db: UserDB,
    *,
    resolve_auth_mode: Callable[[], str],
) -> tuple[int, int, list[int], tuple[Any, int] | None]:
    """Resolve session user, global user, and the combined visible user_ids.

    Returns (session_user_id, global_user_id, visible_user_ids, error_gate).
    In auth-none mode session_user_id == global_user_id (single user).
    """
    auth_mode = resolve_auth_mode()
    global_user_id = _resolve_global_monitor_user_id(user_db)

    if auth_mode == "none":
        raw = session.get("db_user_id")
        uid = None
        if raw is not None:
            with contextlib.suppress(TypeError, ValueError):
                uid = int(raw)
        uid = uid or global_user_id
        return uid, global_user_id, [uid] if uid == global_user_id else [uid, global_user_id], None

    raw = session.get("db_user_id")
    if raw is None:
        return (
            0,
            global_user_id,
            [],
            (
                jsonify({"error": "Authentication required", "code": "user_identity_unavailable"}),
                403,
            ),
        )
    try:
        session_user_id = int(raw)
    except TypeError, ValueError:
        return (
            0,
            global_user_id,
            [],
            (
                jsonify({"error": "Authentication required", "code": "user_identity_unavailable"}),
                403,
            ),
        )

    if session_user_id == global_user_id:
        return session_user_id, global_user_id, [session_user_id], None
    return session_user_id, global_user_id, [session_user_id, global_user_id], None


def _policy_allows_monitoring(
    *, user_db: UserDB, db_user_id: int | None
) -> tuple[bool, str | None]:
    try:
        global_settings = load_config_file("users")
    except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
        global_settings = {}

    user_settings: dict[str, Any] = {}
    if db_user_id is not None:
        try:
            user_settings = user_db.get_user_settings(db_user_id) or {}
        except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            user_settings = {}

    blocked_count = 0
    for ct in ("ebook", "audiobook"):
        mode = resolve_policy_mode(
            source="*",
            content_type=normalize_content_type(ct),
            global_settings=global_settings,
            user_settings=user_settings,
        )
        if mode == PolicyMode.BLOCKED:
            blocked_count += 1

    if blocked_count == 2:
        return False, "Monitoring is unavailable by policy"
    return True, None


def _resolve_preferred_languages_for_user(
    user_db: UserDB, db_user_id: int | None
) -> set[str] | None:
    from shelfmark.core.config import config as app_config

    user_langs: set[str] | None = None
    if db_user_id is not None:
        try:
            settings = user_db.get_user_settings(db_user_id) or {}
        except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            settings = {}
        user_langs = normalize_preferred_languages(settings.get("BOOK_LANGUAGE"))
        if user_langs:
            return user_langs

    return normalize_preferred_languages(app_config.get("BOOK_LANGUAGE", []))


def resolve_download_db_user_id(
    session_obj: Any, auth_mode: str, user_db: UserDB | None
) -> int | None:
    """Resolve DB user id for download queue ownership/history writes.

    In auth-none mode, sessions may not carry db_user_id. Fall back to the
    global monitor user so monitored history writes are still associated with
    the correct entity owner.
    """
    raw_db_user_id = session_obj.get("db_user_id")
    if raw_db_user_id is not None:
        try:
            return int(raw_db_user_id)
        except TypeError, ValueError:
            pass

    if auth_mode != "none" or user_db is None:
        return None

    try:
        return _resolve_global_monitor_user_id(user_db)
    except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
        return None


def resolve_requested_destination(
    raw_destination: Any,
    *,
    user_db: UserDB | None,
    db_user_id: int | None,
) -> str | None:
    """Validate a client-supplied save location for a standalone download.

    ``queue_release()`` applies ``destination_override`` verbatim, so an
    unchecked value from the browser would let any logged-in user write
    downloads anywhere the container can reach. Accept the path only when it
    resolves inside one of this user's allowed roots — the same set the folder
    browser and the library scanner already enforce.
    """
    if not isinstance(raw_destination, str):
        return None
    candidate = raw_destination.strip().rstrip("/")
    if not candidate or not candidate.startswith("/"):
        return None
    if user_db is None or db_user_id is None:
        return None

    try:
        resolved = Path(candidate).resolve()
    except OSError:
        return None

    try:
        roots = resolve_allowed_roots(user_db, db_user_id=int(db_user_id))
    except (OSError, AttributeError, TypeError, ValueError):
        # A root-resolution failure must deny, never allow.
        logger.warning("Could not resolve allowed roots for save location check", exc_info=True)
        return None

    if not path_within_allowed_roots(path=resolved, roots=roots):
        logger.warning("Rejected save location outside allowed roots: %s", candidate)
        return None

    return str(resolved)


def enrich_release_for_monitored(
    release_payload: dict[str, Any],
    monitored_db: MonitoredDB | None,
    db_user_id: int | None,
    user_db: UserDB | None = None,
) -> dict[str, Any]:
    """Inject output overrides for monitored-entity downloads.

    Normalises the monitored_entity_id field and, when the download targets an
    ebook or audiobook from a monitored author, sets destination / template
    overrides so the file lands in the correct author directory.

    Also the chokepoint for client-supplied save locations on standalone
    downloads: every download route runs through here, so validating (and
    otherwise stripping) the output overrides at the top guarantees an
    unvetted path can never reach the orchestrator.
    """
    # Layout overrides are server-decided; the standalone picker only chooses a
    # destination, so the global File Organization setting still applies. Drop
    # any client-sent layout fields before the monitored block below sets its own.
    if (
        release_payload.get("file_organization_override") is not None
        or release_payload.get("template_override") is not None
    ):
        release_payload = dict(release_payload)
        release_payload.pop("file_organization_override", None)
        release_payload.pop("template_override", None)

    if release_payload.get("destination_override") is not None:
        release_payload = dict(release_payload)
        validated = resolve_requested_destination(
            release_payload.get("destination_override"),
            user_db=user_db,
            db_user_id=db_user_id,
        )
        if validated:
            release_payload["destination_override"] = validated
        else:
            release_payload.pop("destination_override", None)

    monitored_entity_id = release_payload.get("monitored_entity_id")
    if monitored_entity_id is not None:
        try:
            release_payload = dict(release_payload)
            release_payload["monitored_entity_id"] = int(monitored_entity_id)
        except TypeError, ValueError:
            release_payload = dict(release_payload)
            release_payload.pop("monitored_entity_id", None)

    try:
        monitored_entity_id_int = release_payload.get("monitored_entity_id")
        ct = str(release_payload.get("content_type") or "").strip().lower()
        if (
            monitored_entity_id_int is not None
            and monitored_db is not None
            and db_user_id is not None
            and ct in ("ebook", "audiobook")
        ):
            # Build user_ids for lookup: session user + global user
            lookup_ids = [int(db_user_id)]
            if user_db is not None:
                try:
                    gid = _resolve_global_monitor_user_id(user_db)
                    if gid != int(db_user_id):
                        lookup_ids.append(gid)
                except Exception:  # noqa: BLE001, S110 — best-effort path inside a route handler; intentional swallow
                    pass
            entity = monitored_db.get_monitored_entity(
                user_ids=lookup_ids,
                entity_id=int(monitored_entity_id_int),
            )
            settings = entity.get("settings") if isinstance(entity, dict) else None
            if not isinstance(settings, dict):
                settings = {}

            dest_override, org_override, tmpl_override = resolve_monitored_output_overrides(
                settings,
                content_type=ct,
                user_id=db_user_id,
            )
            release_payload = dict(release_payload)
            if dest_override is not None:
                release_payload["destination_override"] = dest_override
            if org_override is not None:
                release_payload["file_organization_override"] = org_override
            if tmpl_override is not None:
                release_payload["template_override"] = tmpl_override

            # Inject the book's release_date from monitored DB so the orchestrator's
            # release-date precheck (queue_release) can block manual downloads of
            # unreleased books. Release sources don't populate release_date on
            # the Release object, so without this the precheck is silent.
            book_provider = str(release_payload.get("monitored_book_provider") or "").strip()
            book_provider_id = str(release_payload.get("monitored_book_provider_id") or "").strip()
            if book_provider and book_provider_id and not release_payload.get("release_date"):
                books = (
                    monitored_db.list_monitored_books(
                        user_ids=lookup_ids,
                        entity_id=int(monitored_entity_id_int),
                    )
                    or []
                )
                for b in books:
                    if (
                        str(b.get("provider") or "").strip() == book_provider
                        and str(b.get("provider_book_id") or "").strip() == book_provider_id
                    ):
                        rd = b.get("release_date")
                        if rd:
                            release_payload["release_date"] = rd
                        break
    except Exception:
        logger.warning(
            "Failed to enrich release for monitored entity %s", monitored_entity_id, exc_info=True
        )

    return release_payload


def get_monitored_config_additions(
    app_config: Any, raw_db_user_id: Any
) -> tuple[dict[str, Any], int | None]:
    """Return monitored-feature config dict entries and the resolved user_id.

    Extracted from api_config() to keep main.py lean. Returns a tuple of
    (additions_dict, config_user_id) where config_user_id should be forwarded
    to other per-user app_config.get() calls in the same request.
    """
    config_user_id: int | None = None
    try:
        config_user_id = int(raw_db_user_id) if raw_db_user_id is not None else None
    except TypeError, ValueError:
        config_user_id = None

    default_action_raw = (
        str(app_config.get("RELEASE_PRIMARY_DEFAULT_ACTION", "") or "").strip().lower()
    )

    default_action_map: dict[str, tuple[str, str]] = {
        "ebook_interactive_search": ("ebook", "interactive_search"),
        "ebook_auto_search_download": ("ebook", "auto_search_download"),
        "audiobook_interactive_search": ("audiobook", "interactive_search"),
        "audiobook_auto_search_download": ("audiobook", "auto_search_download"),
        "combined_interactive_search": ("combined", "interactive_search"),
        "combined_auto_search_download": ("combined", "auto_search_download"),
    }

    default_content_type, default_action = default_action_map.get(default_action_raw, (None, None))  # type: ignore[assignment]
    if default_content_type is None or default_action is None:
        # Backward compatibility for legacy split settings.
        fallback_content_type = app_config.get("RELEASE_PRIMARY_CONTENT_TYPE", "ebook")
        fallback_content_type = (
            "audiobook" if str(fallback_content_type).strip().lower() == "audiobook" else "ebook"
        )
        fallback_action = app_config.get(
            "RELEASE_PRIMARY_ACTION_AUDIOBOOK"
            if fallback_content_type == "audiobook"
            else "RELEASE_PRIMARY_ACTION_EBOOK",
            app_config.get("RELEASE_PRIMARY_ACTION", "interactive_search"),
        )
        fallback_action = (
            "auto_search_download"
            if str(fallback_action).strip().lower() == "auto_search_download"
            else "interactive_search"
        )
        default_content_type, default_action = fallback_content_type, fallback_action

    is_combined = default_content_type == "combined"

    return {
        "show_release_match_score": app_config.get(
            "SHOW_RELEASE_MATCH_SCORE", True, user_id=config_user_id
        ),
        "release_primary_default_action": f"{default_content_type}_{default_action}",
        "release_primary_content_type": "ebook" if is_combined else default_content_type,
        "release_combined_mode": is_combined,
        "release_primary_action_ebook": default_action
        if is_combined or default_content_type == "ebook"
        else "interactive_search",
        "release_primary_action_audiobook": default_action
        if is_combined or default_content_type == "audiobook"
        else "interactive_search",
        "auto_download_min_match_score": app_config.get(
            "AUTO_DOWNLOAD_MIN_MATCH_SCORE", 75, user_id=config_user_id
        ),
        "show_dual_get_buttons": app_config.get(
            "SHOW_DUAL_GET_BUTTONS", False, user_id=config_user_id
        ),
        "show_books_in_multiple_series": app_config.get("SHOW_BOOKS_IN_MULTIPLE_SERIES", True),
        "default_to_monitored_view": app_config.get(
            "DEFAULT_TO_MONITORED_VIEW", False, user_id=config_user_id
        ),
    }, config_user_id


def _backfill_search_author_photos(
    provider: Any,
    authors: list[dict],
    transform_fn: Callable,
) -> None:
    """Fetch photos for authors missing them via direct GraphQL (not Typesense).

    Mutates the author dicts in-place, setting ``photo_url`` where found.
    Uses a single batched query with ``id: {_in: [...]}`` to avoid N+1 round-trips.
    """
    id_map: dict[int, dict] = {}
    for a in authors:
        pid = a.get("provider_id")
        if pid is None:
            continue
        try:
            id_map[int(pid)] = a
        except ValueError, TypeError:
            continue
    if not id_map:
        return

    ids = list(id_map.keys())
    query = """
    query GetAuthorPhotos($ids: [Int!]!) {
        authors(where: {id: {_in: $ids}}) {
            id
            image { url }
            cached_image
        }
    }
    """
    result = provider._execute_query(query, {"ids": ids})
    if not result:
        return

    for author_row in result.get("authors") or []:
        aid = author_row.get("id")
        if aid not in id_map:
            continue
        photo_url = extract_author_photo_url(author_row)
        if photo_url:
            cache_id = f"hardcover_author_{aid}"
            id_map[aid]["photo_url"] = transform_fn(photo_url, cache_id)


def register_monitored_routes(
    app: Flask,
    user_db: UserDB,
    monitored_db: MonitoredDB,
    *,
    resolve_auth_mode: Callable[[], str],
    ws_manager: Any = None,
) -> None:

    @app.before_request
    def _ensure_auth_none_user() -> None:
        """Auto-provision a db_user_id for auth-none mode (Sonarr-style single user).

        Re-uses the same "global" user that _resolve_global_monitor_user_id
        creates so that auth-none mode has exactly ONE user identity.
        """
        if resolve_auth_mode() != "none":
            return
        if "db_user_id" in session:
            return
        global_uid = _resolve_global_monitor_user_id(user_db)
        session["user_id"] = "global"
        session["db_user_id"] = global_uid
        session["is_admin"] = True
        return

    def _can_edit_entity(entity: dict, *, db_user_id: int, global_user_id: int) -> bool:
        """Return True if the session user may edit/delete *entity*.

        - Private entities (user_id == db_user_id) → owner can edit.
        - Public entities (user_id == global_user_id) → creator OR admin can edit.
        """
        if int(entity["user_id"]) != global_user_id:
            # Private entity — only the owner
            return int(entity["user_id"]) == db_user_id
        # Public entity — creator or admin
        if session.get("is_admin"):
            return True
        created_by = (entity.get("settings") or {}).get("created_by")
        if created_by is not None:
            try:
                return int(created_by) == db_user_id
            except TypeError, ValueError:
                pass
        return False

    _batch_sync_lock = threading.Lock()

    def _parse_schedule_times(raw_value: Any) -> list[str]:
        raw = str(raw_value or "").strip()
        if not raw:
            raw = "02:00,14:00"

        unique: list[str] = []
        seen: set[str] = set()
        for part in (segment.strip() for segment in raw.split(",")):
            if not part:
                continue
            if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", part):
                continue
            if part not in seen:
                seen.add(part)
                unique.append(part)

        return unique or ["02:00", "14:00"]

    def _collect_enabled_author_entities() -> list[tuple[int, dict]]:
        """Collect all enabled author entities across all users. Returns ``(user_id, entity)`` tuples."""
        result: list[tuple[int, dict]] = []
        user_ids = {int(u.get("id")) for u in user_db.list_users() if u.get("id") is not None}
        global_user = user_db.get_user(username="global")
        if global_user and global_user.get("id") is not None:
            user_ids.add(int(global_user.get("id")))
        for uid in sorted(user_ids):
            entities = monitored_db.list_monitored_entities(user_ids=[uid])
            for entity in entities:
                if not bool(int(entity.get("enabled") or 0)):
                    continue
                if str(entity.get("kind") or "") != "author":
                    continue
                result.append((uid, entity))
        return result

    def _start_monitored_refresh_scheduler() -> None:
        if app.config.get("TESTING"):
            return
        if app.extensions.get("monitored_refresh_scheduler_started"):
            return

        stop_event = threading.Event()

        def _run_scheduled(ents: list, bid: str, s: str) -> None:
            try:
                from shelfmark.core.config import config as app_config

                refresh_enabled = app_config.get("MONITORED_SCHEDULED_REFRESH_ENABLED", True)
                auto_download_enabled = app_config.get(
                    "MONITORED_SCHEDULED_AUTO_DOWNLOAD_ENABLED", True
                )

                if refresh_enabled:
                    batch_result = run_batch_sync(
                        ents,
                        monitored_db,
                        ws_manager,
                        user_db,
                        batch_id=bid,
                        triggered_by="scheduled",
                    )
                    logger.info(
                        "Scheduled monitored refresh complete slot=%s total=%s successful=%s failed=%s retried=%s",
                        s,
                        batch_result.total,
                        batch_result.successful,
                        batch_result.failed,
                        batch_result.retried,
                    )
                else:
                    logger.info(
                        "Scheduled refresh skipped slot=%s: MONITORED_SCHEDULED_REFRESH_ENABLED=false",
                        s,
                    )

                if not auto_download_enabled:
                    logger.info(
                        "Scheduled auto-download skipped slot=%s: MONITORED_SCHEDULED_AUTO_DOWNLOAD_ENABLED=false",
                        s,
                    )
                    return

                # Compute total candidate count upfront so the run-started event
                # can show "N books to download" from t=0 in the History UI.
                total_candidates = 0
                for uid, entity in ents:
                    eid = int(entity.get("id") or 0)
                    try:
                        av = compute_book_availability(monitored_db, entity_id=eid, user_id=uid)
                    except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                        logger.debug("Skipping candidate count for entity %s: %s", eid, exc)
                        continue
                    total_candidates += len(filter_search_candidates(av.books, "ebook"))
                    total_candidates += len(filter_search_candidates(av.books, "audiobook"))

                run_id = str(uuid.uuid4())
                try:
                    from shelfmark.core.monitored_history import record_run_started

                    record_run_started(
                        run_id=run_id,
                        trigger="scheduled",
                        total_candidates=total_candidates,
                        slot=s,
                        user_id=None,
                    )
                except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                    logger.debug("Failed to record run_started for slot=%s: %s", s, exc)

                total_queued = 0
                total_searched = 0

                for uid, entity in ents:
                    eid = int(entity.get("id") or 0)
                    ename = str(entity.get("name") or "Author")

                    config_additions, _ = get_monitored_config_additions(
                        app_config, raw_db_user_id=uid
                    )
                    threshold = (
                        float(config_additions.get("auto_download_min_match_score", 75) or 75)
                        / 100.0
                    )

                    for content_type in ("ebook", "audiobook"):
                        try:
                            result = search_missing_books(
                                monitored_db,
                                entity_id=eid,
                                user_id=uid,
                                content_type=content_type,
                                min_match_score=threshold,
                                run_id=run_id,
                                triggered_by="scheduled",
                            )
                            if result.total_candidates > 0:
                                total_searched += result.total_candidates
                                total_queued += result.queued
                                logger.info(
                                    "Scheduled auto-search slot=%s entity=%s(%s) type=%s candidates=%s queued=%s unreleased=%s no_match=%s below_cutoff=%s skipped=%s failed=%s",
                                    s,
                                    ename,
                                    eid,
                                    content_type,
                                    result.total_candidates,
                                    result.queued,
                                    result.unreleased,
                                    result.no_match,
                                    result.below_cutoff,
                                    result.skipped_existing_file
                                    + result.skipped_history_final_path_exists,
                                    result.failed,
                                )
                        except MonitoredEntityNotFoundError:
                            logger.debug(
                                "Scheduled auto-search skipped slot=%s entity=%s(%s) — entity not found",
                                s,
                                ename,
                                eid,
                            )
                        except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                            logger.warning(
                                "Scheduled auto-search failed slot=%s entity=%s(%s) type=%s error=%s",
                                s,
                                ename,
                                eid,
                                content_type,
                                exc,
                            )

                if total_searched > 0:
                    logger.info(
                        "Scheduled auto-search complete slot=%s searched=%s queued=%s",
                        s,
                        total_searched,
                        total_queued,
                    )
            finally:
                with _batch_sync_lock:
                    app.extensions["monitored_batch_sync_running"] = False

        def _run() -> None:
            from shelfmark.core.config import config as app_config

            last_run_marker = ""
            while not stop_event.is_set():
                try:
                    refresh_enabled = app_config.get("MONITORED_SCHEDULED_REFRESH_ENABLED", True)
                    auto_download_enabled = app_config.get(
                        "MONITORED_SCHEDULED_AUTO_DOWNLOAD_ENABLED", True
                    )
                    if not (refresh_enabled or auto_download_enabled):
                        stop_event.wait(30)
                        continue

                    # Local time, not UTC: MONITORED_REFRESH_TIMES are
                    # user-configured wall-clock times. astimezone() respects
                    # the operator's TZ env var.
                    now = datetime.now().astimezone()
                    slot = now.strftime("%H:%M")
                    raw_times = app_config.get("MONITORED_REFRESH_TIMES", "02:00,14:00")
                    schedule_times = _parse_schedule_times(raw_times)

                    if slot in schedule_times:
                        marker = f"{now.strftime('%Y-%m-%d')}@{slot}"
                        if marker != last_run_marker:
                            last_run_marker = marker
                            with _batch_sync_lock:
                                if app.extensions.get("monitored_batch_sync_running"):
                                    logger.info(
                                        "Scheduled refresh skipped slot=%s — batch sync already running",
                                        slot,
                                    )
                                    continue
                                app.extensions["monitored_batch_sync_running"] = True

                            entities = _collect_enabled_author_entities()
                            if not entities:
                                with _batch_sync_lock:
                                    app.extensions["monitored_batch_sync_running"] = False
                                continue

                            threading.Thread(
                                target=_run_scheduled,
                                args=(entities, marker, slot),
                                daemon=True,
                                name=f"MonitoredBatchSync-{marker}",
                            ).start()
                except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                    logger.warning("Scheduled monitored refresh loop error: %s", exc)

                stop_event.wait(30)

        worker = threading.Thread(target=_run, daemon=True, name="MonitoredRefreshScheduler")
        worker.start()
        app.extensions["monitored_refresh_scheduler_started"] = True
        app.extensions["monitored_refresh_scheduler_stop_event"] = stop_event

    _start_monitored_refresh_scheduler()

    @app.route("/api/monitored/thumb/<cache_id>", methods=["GET"])
    def api_monitored_thumb(cache_id: str) -> Response | tuple[Response, int]:
        """Serve a width-resized WEBP thumbnail of a cached cover/portrait.

        Branch-only sibling of ``/api/covers`` that adds on-the-fly resizing
        backed by the same disk cache. Grid tiles request ``?w=<width>&url=…``;
        the full-size ``/api/covers`` route is left untouched for detail views.
        """
        import base64
        import binascii

        from shelfmark.config.env import is_covers_cache_enabled
        from shelfmark.core.monitored_thumbnails import (
            ALLOWED_THUMB_WIDTHS,
            get_or_create_thumbnail,
        )

        _db_user_id, _global_user_id, _visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        if not is_covers_cache_enabled():
            return jsonify({"error": "Cover caching is disabled"}), 404

        width = request.args.get("w", type=int)
        if width not in ALLOWED_THUMB_WIDTHS:
            return jsonify({"error": "Unsupported thumbnail width"}), 400

        original_url: str | None = None
        encoded_url = request.args.get("url")
        if encoded_url:
            try:
                original_url = base64.urlsafe_b64decode(encoded_url).decode()
            except binascii.Error, UnicodeDecodeError:
                return jsonify({"error": "Invalid image URL encoding"}), 400

        result = get_or_create_thumbnail(cache_id, url=original_url, width=width)
        if not result:
            return jsonify({"error": "Failed to build thumbnail"}), 404

        image_data, content_type = result
        response = app.response_class(response=image_data, status=200, mimetype=content_type)
        # Content-addressed (cache_id = provider_bookid) + fixed width → the
        # bytes for a given URL never change, so mark immutable for a year.
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    @app.route("/api/monitored/<int:entity_id>", methods=["GET"])
    def api_get_monitored(entity_id: int) -> Response | tuple[Response, int]:
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        entity = monitored_db.get_monitored_entity(user_ids=visible_user_ids, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        return jsonify(entity)

    @app.route("/api/monitored/<int:entity_id>", methods=["PATCH", "PUT"])
    def api_patch_monitored(entity_id: int) -> Response | tuple[Response, int]:
        db_user_id, global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify(
                {
                    "error": message or "Monitoring is unavailable by policy",
                    "code": "policy_blocked",
                }
            ), 403

        entity = monitored_db.get_monitored_entity(user_ids=visible_user_ids, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        if not _can_edit_entity(entity, db_user_id=db_user_id, global_user_id=global_user_id):
            return jsonify({"error": "You don't have permission to edit this author"}), 403

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        settings_patch = data.get("settings")
        if settings_patch is None:
            settings_patch = {}
        if not isinstance(settings_patch, dict):
            return jsonify({"error": "settings must be an object"}), 400

        settings = entity.get("settings")
        if not isinstance(settings, dict):
            settings = {}
        merged_settings = dict(settings)
        merged_settings.update(settings_patch)

        try:
            updated = monitored_db.create_monitored_entity(
                user_id=int(entity["user_id"]),
                kind=str(entity.get("kind") or "author"),
                provider=entity.get("provider"),
                provider_id=entity.get("provider_id"),
                name=str(entity.get("name") or "").strip() or "Unknown",
                enabled=bool(int(entity.get("enabled") or 0)),
                settings=merged_settings,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        should_reapply_monitor_modes = str(
            updated.get("kind") or ""
        ).strip().lower() == "author" and (
            "monitor_ebook_mode" in settings_patch or "monitor_audiobook_mode" in settings_patch
        )
        if should_reapply_monitor_modes:
            # User explicitly changed monitor mode — unlock all books so the new mode applies
            monitored_db.unlock_all_monitor_flags(entity_id=entity_id)
            books = (
                monitored_db.list_monitored_books(user_ids=visible_user_ids, entity_id=entity_id)
                or []
            )
            existing_files = (
                monitored_db.list_monitored_book_files(
                    user_ids=visible_user_ids, entity_id=entity_id
                )
                or []
            )
            if books and existing_files:
                from shelfmark.core.monitored_files import (
                    expand_monitored_file_rows_for_equivalent_books,
                )

                existing_files = expand_monitored_file_rows_for_equivalent_books(
                    books=books,
                    file_rows=existing_files,
                )
            apply_monitor_modes_for_books(
                monitored_db,
                db_user_id=int(updated["user_id"]),
                entity=updated,
                books=books,
                file_rows=existing_files,
            )

        return jsonify(updated)

    @app.route("/api/monitored", methods=["GET"])
    def api_list_monitored() -> Response | tuple[Response, int]:
        import time as _time

        _t0 = _time.perf_counter()

        db_user_id, global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        rows = monitored_db.list_monitored_entities(user_ids=visible_user_ids)
        _t_db = _time.perf_counter()

        # Enrich with cached author details (bio, source_url) if available
        try:
            from shelfmark.core.metadata_cache import get_metadata_file_cache

            mcache = get_metadata_file_cache()
            for row in rows:
                provider = row.get("provider")
                provider_id = row.get("provider_id")
                if not provider or not provider_id:
                    continue
                cached = mcache.get("authors", provider, provider_id)
                if cached and isinstance(cached, dict):
                    author_data = cached.get("author")
                    if isinstance(author_data, dict):
                        row["cached_bio"] = author_data.get("bio")
                        row["cached_source_url"] = author_data.get("source_url")
        except Exception:  # noqa: BLE001, S110 — best-effort path inside a route handler; intentional swallow
            pass  # Best-effort enrichment
        _t_meta = _time.perf_counter()

        # For author entities without a profile photo, compute best book cover as fallback.
        # Fetch all covers in one query to avoid N+1 DB round-trips.
        author_ids_needing_cover = [
            int(row["id"])
            for row in rows
            if row.get("kind") == "author" and not (row.get("settings") or {}).get("photo_url")
        ]
        if author_ids_needing_cover:
            try:
                from shelfmark.core.utils import transform_cover_url

                cover_by_entity = monitored_db.get_best_book_cover_urls_batch(
                    user_ids=visible_user_ids, entity_ids=author_ids_needing_cover
                )
                for row in rows:
                    entity_id = int(row.get("id", 0))
                    info = cover_by_entity.get(entity_id)
                    if info:
                        # Use the same cache_id format as the sync prefetch so we
                        # always get a disk-cache hit after the first sync.
                        provider = info["provider"]
                        book_id = info["provider_book_id"]
                        cache_id = (
                            f"{provider}_{book_id}"
                            if provider and book_id
                            else f"monitored_author_{entity_id}"
                        )
                        row["best_book_cover_url"] = transform_cover_url(
                            info["cover_url"], cache_id
                        )
            except Exception:
                logger.warning("Failed to compute best book covers for fallback", exc_info=True)
        _t_covers = _time.perf_counter()

        # Tag each entity with its visibility (public = global user, private = session user)
        for row in rows:
            row["visibility"] = (
                "private"
                if row.get("user_id") == db_user_id and db_user_id != global_user_id
                else "public"
            )

        resp = jsonify(rows)
        resp.headers["Server-Timing"] = (
            f"db;dur={(_t_db - _t0) * 1000:.1f},"
            f"meta;dur={(_t_meta - _t_db) * 1000:.1f},"
            f"covers;dur={(_t_covers - _t_meta) * 1000:.1f},"
            f"total;dur={(_t_covers - _t0) * 1000:.1f}"
        )

        # Emit Link: rel=preload for cover images so the browser can start
        # fetching them while the JS is still parsing the JSON response body.
        try:
            preload_urls: list[str] = []
            total_bytes = 0
            for row in rows:
                settings = row.get("settings") or {}
                url = settings.get("photo_url") or row.get("best_book_cover_url")
                if url and isinstance(url, str) and url.startswith("/"):
                    entry = f"<{url}>; rel=preload; as=image"
                    added = len(entry) + (2 if preload_urls else 0)  # ", " separator
                    if total_bytes + added > _MAX_LINK_HEADER_BYTES:
                        break
                    preload_urls.append(entry)
                    total_bytes += added
                    if len(preload_urls) >= 20:
                        break
            if preload_urls:
                resp.headers["Link"] = ", ".join(preload_urls)
        except Exception:  # noqa: BLE001, S110 — best-effort path inside a route handler; intentional swallow
            pass

        return resp

    @app.route("/api/monitored/search/books", methods=["GET"])
    def api_search_monitored_author_books() -> Response | tuple[Response, int]:
        db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        query = str(request.args.get("q") or "").strip()
        if not query:
            return jsonify({"results": []})

        raw_limit = request.args.get("limit")
        try:
            limit = int(raw_limit) if raw_limit is not None else 20
        except TypeError, ValueError:
            limit = 20

        rows = monitored_db.search_monitored_author_books(
            user_ids=visible_user_ids, query=query, limit=limit
        )
        if rows:
            from shelfmark.core.monitored_files import (
                expand_monitored_file_rows_for_equivalent_books,
                summarize_monitored_book_availability,
            )

            rows_by_entity: dict[int, list[dict[str, Any]]] = {}
            for row in rows:
                try:
                    entity_id = int(row.get("entity_id"))
                except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                    continue
                rows_by_entity.setdefault(entity_id, []).append(row)

            availability_by_entity: dict[int, dict[tuple[str, str], dict[str, Any]]] = {}
            for entity_id, entity_rows in rows_by_entity.items():
                files = (
                    monitored_db.list_monitored_book_files(
                        user_ids=visible_user_ids, entity_id=entity_id
                    )
                    or []
                )
                if not files:
                    availability_by_entity[entity_id] = {}
                    continue

                books_for_alias: list[dict[str, Any]] = [
                    {
                        "provider": entity_row.get("book_provider"),
                        "provider_book_id": entity_row.get("book_provider_id"),
                        "title": entity_row.get("book_title"),
                        "series_name": entity_row.get("series_name"),
                        "series_position": entity_row.get("series_position"),
                    }
                    for entity_row in entity_rows
                ]
                expanded_files = expand_monitored_file_rows_for_equivalent_books(
                    books=books_for_alias,
                    file_rows=files,
                )
                availability_by_entity[entity_id] = summarize_monitored_book_availability(
                    file_rows=expanded_files,
                    user_id=db_user_id,
                )

            for row in rows:
                try:
                    entity_id = int(row.get("entity_id"))
                except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                    entity_id = -1
                provider = str(row.get("book_provider") or "").strip()
                provider_book_id = str(row.get("book_provider_id") or "").strip()
                payload = availability_by_entity.get(entity_id, {}).get(
                    (provider, provider_book_id), {}
                )
                row["has_ebook_available"] = bool(payload.get("has_ebook_available", False))
                row["has_audiobook_available"] = bool(payload.get("has_audiobook_available", False))
                row["ebook_path"] = payload.get("ebook_path")
                row["audiobook_path"] = payload.get("audiobook_path")
                row["ebook_available_format"] = payload.get("ebook_available_format")
                row["audiobook_available_format"] = payload.get("audiobook_available_format")
        transform_cached_cover_urls(
            rows, provider_key="book_provider", provider_id_key="book_provider_id"
        )
        return jsonify({"results": rows})

    @app.route("/api/monitored", methods=["POST"])
    def api_create_monitored() -> Response | tuple[Response, int]:
        db_user_id, global_user_id, _visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify(
                {
                    "error": message or "Monitoring is unavailable by policy",
                    "code": "policy_blocked",
                }
            ), 403

        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "Invalid payload"}), 400

        kind = str(data.get("kind") or "").strip().lower()
        if kind not in {"author", "book"}:
            return jsonify({"error": "kind must be 'author' or 'book'"}), 400

        name = str(data.get("name") or "").strip()
        if not name:
            return jsonify({"error": "name is required"}), 400

        provider = data.get("provider")
        provider_id = data.get("provider_id")
        provider = str(provider).strip() if isinstance(provider, str) and provider.strip() else None
        provider_id = (
            str(provider_id).strip()
            if isinstance(provider_id, str) and provider_id.strip()
            else None
        )

        if kind == "book" and (not provider or not provider_id):
            return jsonify({"error": "provider and provider_id are required for kind='book'"}), 400

        settings = data.get("settings")
        if settings is None:
            settings = {}
        if not isinstance(settings, dict):
            return jsonify({"error": "settings must be an object"}), 400

        # Determine owner based on visibility toggle
        visibility = str(data.get("visibility") or "public").strip().lower()
        if visibility not in ("public", "private"):
            visibility = "public"
        # In auth-none mode, ignore visibility (single user)
        if resolve_auth_mode() == "none":
            owner_user_id = db_user_id
            visibility = "public"
        else:
            owner_user_id = global_user_id if visibility == "public" else db_user_id

        # Track who created a public entity so they retain edit rights
        if visibility == "public" and db_user_id != global_user_id:
            settings["created_by"] = db_user_id

        # Auto-convert: if creating as public, check if any user has this as private.
        # Preserve the original owner as created_by so they keep edit rights.
        if visibility == "public" and provider and provider_id:
            for uid_row in user_db.list_users() or []:
                uid_int = uid_row.get("id")
                if uid_int is None or int(uid_int) == global_user_id:
                    continue
                try:
                    existing_id = monitored_db.find_entity_id_by_provider(
                        user_id=int(uid_int),
                        kind=kind,
                        provider=provider,
                        provider_id=provider_id,
                    )
                    if existing_id is not None:
                        monitored_db.reassign_entity_owner(
                            entity_id=existing_id,
                            old_user_id=int(uid_int),
                            new_user_id=global_user_id,
                        )
                        # Stamp created_by onto the resulting global entity so
                        # the original owner retains edit rights.
                        target = monitored_db.find_entity_id_by_provider(
                            user_id=global_user_id,
                            kind=kind,
                            provider=provider,
                            provider_id=provider_id,
                        )
                        if target is not None:
                            ent = monitored_db.get_monitored_entity(
                                user_ids=[global_user_id], entity_id=target
                            )
                            if ent and not (ent.get("settings") or {}).get("created_by"):
                                merged = dict(ent.get("settings") or {})
                                merged["created_by"] = int(uid_int)
                                monitored_db.create_monitored_entity(
                                    user_id=global_user_id,
                                    kind=kind,
                                    provider=provider,
                                    provider_id=provider_id,
                                    name=ent["name"],
                                    enabled=bool(int(ent.get("enabled") or 0)),
                                    settings=merged,
                                )
                except Exception:  # noqa: BLE001, S110 — best-effort path inside a route handler; intentional swallow
                    pass

        try:
            row = monitored_db.create_monitored_entity(
                user_id=owner_user_id,
                kind=kind,
                provider=provider,
                provider_id=provider_id,
                name=name,
                enabled=True,
                settings=settings,
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400

        row["visibility"] = visibility

        try:
            from shelfmark.core.monitored_history import record_author_added

            record_author_added(
                entity_id=int(row["id"]),
                author_name=name,
                provider=provider,
                provider_id=provider_id,
                user_id=owner_user_id,
            )
        except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            logger.debug("Failed to record author_added event: %s", exc)

        if kind == "book" and provider and provider_id:
            fetch_entity_metadata(
                monitored_db,
                entity=row,
                user_id=owner_user_id,
                preferred_languages=_resolve_preferred_languages_for_user(user_db, db_user_id),
            )
        elif kind == "author":
            monitored_db.update_entity_sync_status(int(row["id"]), "syncing")
            start_author_background_sync(
                int(row["id"]),
                owner_user_id,
                monitored_db,
                ws_manager=ws_manager,
                user_db=user_db,
            )

        return jsonify(row), 201

    @app.route("/api/monitored/<int:entity_id>", methods=["DELETE"])
    def api_delete_monitored(entity_id: int) -> Response | tuple[Response, int]:
        db_user_id, global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify(
                {
                    "error": message or "Monitoring is unavailable by policy",
                    "code": "policy_blocked",
                }
            ), 403

        # Check ownership: only admins can delete public (global) entities
        entity = monitored_db.get_monitored_entity(user_ids=visible_user_ids, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404
        if not _can_edit_entity(entity, db_user_id=db_user_id, global_user_id=global_user_id):
            return jsonify({"error": "You don't have permission to delete this author"}), 403

        # Record event BEFORE delete: the FK on monitored_events.entity_id is
        # enforced (PRAGMA foreign_keys=ON), and the row's ON DELETE SET NULL
        # cascade nulls the link automatically once the entity is gone.
        # Recording after delete would IntegrityError on the FK.
        try:
            from shelfmark.core.monitored_history import record_author_removed

            record_author_removed(
                entity_id=entity_id,
                author_name=str(entity.get("name") or "Unknown"),
                user_id=db_user_id,
            )
        except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            logger.debug("Failed to record author_removed event: %s", exc)

        deleted = monitored_db.delete_monitored_entity(
            user_ids=visible_user_ids, entity_id=entity_id
        )
        if not deleted:
            return jsonify({"error": "Not found"}), 404

        return jsonify({"ok": True})

    @app.route("/api/monitored/<int:entity_id>/books", methods=["GET"])
    def api_list_monitored_books(entity_id: int) -> Response | tuple[Response, int]:
        db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        rows = monitored_db.list_monitored_books(user_ids=visible_user_ids, entity_id=entity_id)
        if rows is None:
            return jsonify({"error": "Not found"}), 404

        for row in rows:
            row["no_release_date"] = parse_release_date(row.get("release_date")) is None

        files = (
            monitored_db.list_monitored_book_files(user_ids=visible_user_ids, entity_id=entity_id)
            or []
        )
        if rows and files:
            from shelfmark.core.monitored_files import (
                expand_monitored_file_rows_for_equivalent_books,
            )

            files = expand_monitored_file_rows_for_equivalent_books(
                books=rows,
                file_rows=files,
            )

        from shelfmark.core.monitored_files import with_monitored_book_availability

        rows = with_monitored_book_availability(
            books=rows,
            file_rows=files,
            user_id=db_user_id,
        )
        transform_cached_cover_urls(rows)

        # Enrich books with additional_series from the metadata file cache
        try:
            from shelfmark.core.metadata_cache import get_metadata_file_cache

            mcache = get_metadata_file_cache()
            for row in rows:
                row_provider = row.get("provider")
                provider_book_id = row.get("provider_book_id")
                if not row_provider or not provider_book_id:
                    continue
                cached_meta = mcache.get("books", row_provider, provider_book_id)
                if cached_meta and isinstance(cached_meta, dict):
                    extra = cached_meta.get("additional_series")
                    if extra:
                        row["additional_series"] = extra
        except Exception:  # noqa: BLE001, S110 — best-effort path inside a route handler; intentional swallow
            pass  # Best-effort enrichment

        # Include sync_status and last_checked_at for the frontend
        entity = monitored_db.get_monitored_entity(user_ids=visible_user_ids, entity_id=entity_id)
        last_checked_at = entity.get("last_checked_at") if entity else None
        sync_status = entity.get("sync_status", "idle") if entity else "idle"

        return jsonify(
            {"books": rows, "last_checked_at": last_checked_at, "sync_status": sync_status}
        )

    @app.route("/api/monitored/<int:entity_id>/files", methods=["GET"])
    def api_list_monitored_book_files(entity_id: int) -> Response | tuple[Response, int]:
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        rows = monitored_db.list_monitored_book_files(
            user_ids=visible_user_ids, entity_id=entity_id
        )
        if rows is None:
            return jsonify({"error": "Not found"}), 404
        books = (
            monitored_db.list_monitored_books(user_ids=visible_user_ids, entity_id=entity_id) or []
        )
        if books and rows:
            from shelfmark.core.monitored_files import (
                expand_monitored_file_rows_for_equivalent_books,
            )

            rows = expand_monitored_file_rows_for_equivalent_books(
                books=books,
                file_rows=rows,
            )

        # Parse evidence_json (v2 attribution breakdown) so the frontend gets a
        # structured object rather than a string.
        import json as _json

        for r in rows:
            raw = r.get("evidence_json")
            if isinstance(raw, str) and raw:
                try:
                    r["evidence"] = _json.loads(raw)
                except _json.JSONDecodeError:
                    r["evidence"] = None
            else:
                r["evidence"] = None
            # Drop the raw string from the response to keep payloads compact.
            r.pop("evidence_json", None)

        return jsonify({"files": rows})

    def _build_match_candidates(
        *,
        entity_id: int,
        target_book: dict[str, Any],
        file_type: str,
        anchor_file_id: int | None,
        author_name: str | None,
        existing_rows: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Rank candidate files for attaching to ``target_book``.

        Includes existing matched rows in ``monitored_book_files`` AND
        unmatched items live-fetched from configured integrations
        (AudioBookShelf for audiobooks, Grimmory for ebooks). Unmatched
        items have ``file.id = None`` — the apply step uses (source, path)
        as the identifier for those.
        """
        import json as _json
        from dataclasses import asdict as _asdict

        from shelfmark.core.monitored_attribution_metadata import read_embedded_metadata
        from shelfmark.core.monitored_attribution_v2 import (
            SourceMetadata,
            evaluate_match,
        )

        is_audiobook = file_type == "audiobook" or file_type in _AUDIO_FILE_TYPES
        seen_keys: set[tuple[str, str]] = set()
        ranked: list[dict[str, Any]] = []

        def _score_and_append(
            *,
            cand_id: int | None,
            cand_path: str,
            cand_source: str,
            cand_ext: str | None,
            cand_file_type: str | None,
            embedded: Any,
            src_meta: SourceMetadata | None,
            attached_provider: str | None,
            attached_pbid: str | None,
        ) -> None:
            ev = evaluate_match(
                path=cand_path,
                book=target_book,
                author_name=author_name,
                embedded=embedded,
                source_metadata=src_meta,
            )
            ranked.append(
                {
                    "file": {
                        "id": cand_id,
                        "path": cand_path,
                        "source": cand_source,
                        "ext": cand_ext,
                        "file_type": cand_file_type,
                        "currently_attached_book": {
                            "provider": attached_provider,
                            "provider_book_id": attached_pbid,
                        },
                    },
                    "confidence": ev.confidence,
                    "net_score": ev.net_score,
                    "is_current": cand_id is not None and cand_id == anchor_file_id,
                    "evidence": _asdict(ev),
                }
            )

        # ---- 1. Existing monitored_book_files rows matching this file_type ----
        for cand in existing_rows:
            if _row_is_audiobook(cand.get("file_type")) != is_audiobook:
                continue
            cand_path = cand.get("path") or ""
            cand_source = cand.get("source") or "filesystem"
            seen_keys.add((cand_source, cand_path))

            embedded = None
            src_meta = None
            if cand_source == "filesystem" and cand_path:
                try:
                    embedded = read_embedded_metadata(cand_path)
                except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                    embedded = None
            elif cand_source in ("audiobookshelf", "grimmory"):
                stored = cand.get("evidence_json")
                source_data: dict[str, Any] = {}
                if isinstance(stored, str) and stored:
                    try:
                        parsed = _json.loads(stored)
                        if isinstance(parsed, dict):
                            source_data = parsed.get("source_data") or {}
                    except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                        source_data = {}
                # Restore all_series_pairs from the serialised form
                # (list of [name, pos] pairs) so the Fix-match re-score
                # benefits from multi-series matching the same way the
                # original sync did.
                raw_pairs = source_data.get("all_series_pairs") or []
                parsed_pairs: list[tuple[str, float]] = []
                if isinstance(raw_pairs, list):
                    for entry in raw_pairs:
                        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                            name = entry[0]
                            try:
                                pos = float(entry[1])
                            except TypeError, ValueError:
                                continue
                            if isinstance(name, str) and name.strip():
                                parsed_pairs.append((name.strip(), pos))
                src_meta = SourceMetadata(
                    title=source_data.get("title"),
                    author=(source_data.get("authors") or [None])[0]
                    if isinstance(source_data.get("authors"), list)
                    else None,
                    series_name=source_data.get("series_name"),
                    series_position=source_data.get("series_position"),
                    all_series_pairs=parsed_pairs,
                    isbn_13=source_data.get("isbn_13"),
                    isbn_10=source_data.get("isbn_10"),
                    asin=source_data.get("asin"),
                    source_label="abs" if cand_source == "audiobookshelf" else "grimmory",
                )

            _score_and_append(
                cand_id=cand.get("id"),
                cand_path=cand_path,
                cand_source=cand_source,
                cand_ext=cand.get("ext"),
                cand_file_type=cand.get("file_type"),
                embedded=embedded,
                src_meta=src_meta,
                attached_provider=cand.get("provider"),
                attached_pbid=cand.get("provider_book_id"),
            )

        # ---- 2. Live-fetched unmatched items from integrations ----
        # ABS items for audiobooks, Grimmory books for ebooks. Each fetch is
        # best-effort; if the integration is misconfigured we just skip it.
        if is_audiobook and author_name:
            try:
                from shelfmark.core.monitored_audiobookshelf_integration import (
                    _abs_item_to_source_metadata,
                    _find_abs_author_items,
                    _get_abs_library_ids,
                    get_abs_config,
                )

                cfg = get_abs_config()
                if cfg:
                    seen_item_ids: set[str] = set()
                    for lib_id in _get_abs_library_ids(cfg["url"], cfg["token"]):
                        for item in _find_abs_author_items(
                            cfg["url"], cfg["token"], lib_id, author_name
                        ):
                            item_id = str(item.get("id") or "")
                            if item_id and item_id in seen_item_ids:
                                continue
                            seen_item_ids.add(item_id)
                            if item.get("isMissing") or item.get("isInvalid"):
                                continue
                            item_path = (item.get("path") or "").strip()
                            if not item_path or ("audiobookshelf", item_path) in seen_keys:
                                continue
                            seen_keys.add(("audiobookshelf", item_path))
                            _score_and_append(
                                cand_id=None,
                                cand_path=item_path,
                                cand_source="audiobookshelf",
                                cand_ext=None,
                                cand_file_type="audiobook",
                                embedded=None,
                                src_meta=_abs_item_to_source_metadata(item),
                                attached_provider=None,
                                attached_pbid=None,
                            )
            except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                logger.warning(
                    "Failed to live-fetch ABS candidates for entity %s: %s", entity_id, exc
                )

        # Grimmory live-fetch deliberately omitted: Grimmory's path-per-book
        # requires an N+1 follow-up API call. Add later if users need it.

        ranked.sort(key=lambda r: r["net_score"], reverse=True)
        return ranked[:50]

    @app.route("/api/monitored/<int:entity_id>/files/<int:file_id>/candidates", methods=["GET"])
    def api_list_match_candidates(entity_id: int, file_id: int) -> Response | tuple[Response, int]:
        """Return ranked candidate FILES for the book this row is attached to.

        Used by Fix Match when the user wants to swap which file represents
        a book that already has an attribution.
        """
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if gate is not None:
            return gate

        rows = monitored_db.list_monitored_book_files(
            user_ids=visible_user_ids,
            entity_id=entity_id,
        )
        if rows is None:
            return jsonify({"error": "Not found"}), 404
        file_row = next((r for r in rows if r.get("id") == file_id), None)
        if file_row is None:
            return jsonify({"error": "File not found"}), 404

        books = (
            monitored_db.list_monitored_books(
                user_ids=visible_user_ids,
                entity_id=entity_id,
            )
            or []
        )
        target_book = next(
            (
                b
                for b in books
                if b.get("provider") == file_row.get("provider")
                and b.get("provider_book_id") == file_row.get("provider_book_id")
            ),
            None,
        )
        if target_book is None:
            return jsonify({"error": "Currently-attached book not found"}), 404

        entity = monitored_db.get_monitored_entity(
            user_ids=visible_user_ids,
            entity_id=entity_id,
        )
        author_name = (entity or {}).get("name") if entity else None
        file_type = "audiobook" if _row_is_audiobook(file_row.get("file_type")) else "ebook"

        candidates = _build_match_candidates(
            entity_id=entity_id,
            target_book=target_book,
            file_type=file_type,
            anchor_file_id=file_id,
            author_name=author_name,
            existing_rows=rows,
        )
        return jsonify(
            {
                "candidates": candidates,
                "target_book": {
                    "title": target_book.get("title"),
                    "series_name": target_book.get("series_name"),
                    "series_position": target_book.get("series_position"),
                    "provider": target_book.get("provider"),
                    "provider_book_id": target_book.get("provider_book_id"),
                },
            }
        )

    @app.route(
        "/api/monitored/<int:entity_id>/books/<string:provider>/<path:provider_book_id>/candidates",
        methods=["GET"],
    )
    def api_list_match_candidates_for_book(
        entity_id: int, provider: str, provider_book_id: str
    ) -> Response | tuple[Response, int]:
        """Return ranked candidate FILES for a (book, file_type) pair that has
        no current attribution yet — backs the "+ Add audiobook/ebook" flow.
        """
        ft_param = (request.args.get("file_type") or "").strip().lower()
        if ft_param not in {"ebook", "audiobook"}:
            return jsonify({"error": "file_type must be ebook or audiobook"}), 400

        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if gate is not None:
            return gate

        rows = monitored_db.list_monitored_book_files(
            user_ids=visible_user_ids,
            entity_id=entity_id,
        )
        if rows is None:
            return jsonify({"error": "Not found"}), 404

        books = (
            monitored_db.list_monitored_books(
                user_ids=visible_user_ids,
                entity_id=entity_id,
            )
            or []
        )
        target_book = next(
            (
                b
                for b in books
                if b.get("provider") == provider and b.get("provider_book_id") == provider_book_id
            ),
            None,
        )
        if target_book is None:
            return jsonify({"error": "Book not found"}), 404

        entity = monitored_db.get_monitored_entity(
            user_ids=visible_user_ids,
            entity_id=entity_id,
        )
        author_name = (entity or {}).get("name") if entity else None

        candidates = _build_match_candidates(
            entity_id=entity_id,
            target_book=target_book,
            file_type=ft_param,
            anchor_file_id=None,
            author_name=author_name,
            existing_rows=rows,
        )
        return jsonify(
            {
                "candidates": candidates,
                "target_book": {
                    "title": target_book.get("title"),
                    "series_name": target_book.get("series_name"),
                    "series_position": target_book.get("series_position"),
                    "provider": target_book.get("provider"),
                    "provider_book_id": target_book.get("provider_book_id"),
                },
            }
        )

    def _apply_manual_attribution(
        *,
        entity_id: int,
        visible_user_ids: list[int],
        target_book: dict[str, Any],
        chosen_source: str,
        chosen_path: str,
        chosen_file_type: str,
        chosen_ext: str | None,
        chosen_size_bytes: int | None,
        chosen_mtime: str | None,
        author_name: str | None,
    ) -> None:
        """Upsert a manual-override row attributing target_book to (source, path).

        Re-scores the (path, book) pair so evidence_json reflects the manual
        choice; confidence forced to 1.0. Shared by both match endpoints.
        """
        import json as _json
        from dataclasses import asdict as _asdict

        from shelfmark.core.monitored_attribution_metadata import read_embedded_metadata
        from shelfmark.core.monitored_attribution_v2 import evaluate_match

        embedded = None
        if chosen_source == "filesystem" and chosen_path:
            try:
                embedded = read_embedded_metadata(chosen_path)
            except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                embedded = None
        ev = evaluate_match(
            path=chosen_path if chosen_source == "filesystem" else "",
            book=target_book,
            author_name=author_name,
            embedded=embedded,
        )
        monitored_db.upsert_monitored_book_file(
            user_ids=visible_user_ids,
            entity_id=entity_id,
            provider=target_book.get("provider"),
            provider_book_id=target_book.get("provider_book_id"),
            path=chosen_path,
            ext=chosen_ext,
            file_type=chosen_file_type,
            size_bytes=chosen_size_bytes,
            mtime=chosen_mtime,
            confidence=1.0,
            match_reason="manual_override",
            source=chosen_source,
            evidence_json=_json.dumps(_asdict(ev), default=str),
            manual_override=True,
        )

    @app.route("/api/monitored/<int:entity_id>/files/<int:file_id>/match", methods=["POST"])
    def api_set_manual_match(entity_id: int, file_id: int) -> Response | tuple[Response, int]:
        """User swaps which file is attached to the book of an existing row,
        or detaches the row entirely.

        Body shapes:
          { "file_id": N }                               → swap to existing file N
          { "source": "...", "path": "...", "file_type": "..." }
                                                         → swap to (source, path)
                                                           (creates the row if it
                                                           doesn't already exist —
                                                           virtual candidates)
          { "detach": true }                             → delete this row
        """
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if gate is not None:
            return gate

        payload = request.get_json(silent=True) or {}

        rows = monitored_db.list_monitored_book_files(
            user_ids=visible_user_ids,
            entity_id=entity_id,
        )
        if rows is None:
            return jsonify({"error": "Not found"}), 404
        anchor_row = next((r for r in rows if r.get("id") == file_id), None)
        if anchor_row is None:
            return jsonify({"error": "File not found"}), 404

        if payload.get("detach"):
            # Record the (file, book) pair as user-rejected so future syncs
            # won't re-attribute it. Other books can still be matched to this
            # file. Done before delete so the row's provider/pbid are still
            # available.
            anchor_provider = (anchor_row.get("provider") or "").strip()
            anchor_pbid = (anchor_row.get("provider_book_id") or "").strip()
            anchor_path = (anchor_row.get("path") or "").strip()
            anchor_source = (anchor_row.get("source") or "").strip()
            if anchor_provider and anchor_pbid and anchor_path and anchor_source:
                monitored_db.record_file_rejection(
                    user_ids=visible_user_ids,
                    entity_id=entity_id,
                    source=anchor_source,
                    path=anchor_path,
                    provider=anchor_provider,
                    provider_book_id=anchor_pbid,
                )
            monitored_db.delete_monitored_book_file_by_id(
                user_ids=visible_user_ids,
                entity_id=entity_id,
                file_id=file_id,
            )
            return jsonify({"ok": True, "detached": True})

        # Resolve the chosen file — either an existing row (by id or by
        # source+path) or a virtual candidate (source+path only).
        chosen_row: dict[str, Any] | None = None
        chosen_id = payload.get("file_id")
        chosen_source = (payload.get("source") or "").strip()
        chosen_path = (payload.get("path") or "").strip()
        chosen_file_type = (payload.get("file_type") or "").strip()
        if isinstance(chosen_id, int):
            chosen_row = next((r for r in rows if r.get("id") == chosen_id), None)
            if chosen_row is None:
                return jsonify({"error": "Chosen file not found in this entity"}), 400
            chosen_source = chosen_row.get("source") or "filesystem"
            chosen_path = chosen_row.get("path") or ""
            chosen_file_type = chosen_row.get("file_type") or chosen_file_type
        elif chosen_source and chosen_path:
            chosen_row = next(
                (
                    r
                    for r in rows
                    if (r.get("source") or "") == chosen_source
                    and (r.get("path") or "") == chosen_path
                ),
                None,
            )
            if chosen_row is not None:
                chosen_file_type = chosen_row.get("file_type") or chosen_file_type
        else:
            return jsonify({"error": "Must provide file_id or (source, path)"}), 400

        if not chosen_file_type:
            # Fall back to the anchor row's file_type for virtual candidates.
            chosen_file_type = anchor_row.get("file_type") or ""
        if not chosen_file_type:
            return jsonify({"error": "file_type could not be inferred"}), 400

        # Target book is whatever the anchor row was attached to.
        target_provider = anchor_row.get("provider")
        target_pbid = anchor_row.get("provider_book_id")
        if not target_provider or not target_pbid:
            return jsonify({"error": "Anchor row has no attached book"}), 400

        books = (
            monitored_db.list_monitored_books(
                user_ids=visible_user_ids,
                entity_id=entity_id,
            )
            or []
        )
        target_book = next(
            (
                b
                for b in books
                if b.get("provider") == target_provider and b.get("provider_book_id") == target_pbid
            ),
            None,
        )
        if target_book is None:
            return jsonify({"error": "Target book not found in this entity"}), 404

        entity = monitored_db.get_monitored_entity(
            user_ids=visible_user_ids,
            entity_id=entity_id,
        )
        author_name = (entity or {}).get("name") if entity else None

        _apply_manual_attribution(
            entity_id=entity_id,
            visible_user_ids=visible_user_ids,
            target_book=target_book,
            chosen_source=chosen_source,
            chosen_path=chosen_path,
            chosen_file_type=chosen_file_type,
            chosen_ext=(chosen_row.get("ext") if chosen_row else None),
            chosen_size_bytes=(chosen_row.get("size_bytes") if chosen_row else None),
            chosen_mtime=(chosen_row.get("mtime") if chosen_row else None),
            author_name=author_name,
        )
        return jsonify({"ok": True})

    @app.route("/api/monitored/<int:entity_id>/files/<int:file_id>/promote", methods=["POST"])
    def api_promote_candidate(entity_id: int, file_id: int) -> Response | tuple[Response, int]:
        """User accepts a Possible Candidate. Promotes the row's status from
        'candidate' to 'matched' and sets manual_override=1 so the scanner
        won't demote it back.
        """
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if gate is not None:
            return gate

        try:
            ok = monitored_db.promote_candidate_to_matched(
                user_ids=visible_user_ids,
                entity_id=entity_id,
                file_id=file_id,
            )
        except ValueError as exc:
            # Raised when a manual_override matched row already occupies
            # the slot — promoting would violate the partial unique index.
            return jsonify({"error": str(exc), "code": "conflict_manual_match"}), 409
        if not ok:
            return jsonify({"error": "Candidate not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/monitored/<int:entity_id>/files/<int:file_id>/reject", methods=["POST"])
    def api_reject_candidate(entity_id: int, file_id: int) -> Response | tuple[Response, int]:
        """User rejects a Possible Candidate. Records the (file, book) pair
        in monitored_file_rejections so future scans won't re-attribute, then
        deletes the row. Mirrors the `detach` branch of api_set_manual_match.
        """
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if gate is not None:
            return gate

        rows = monitored_db.list_monitored_book_files(
            user_ids=visible_user_ids,
            entity_id=entity_id,
        )
        if rows is None:
            return jsonify({"error": "Not found"}), 404
        anchor_row = next((r for r in rows if r.get("id") == file_id), None)
        if anchor_row is None:
            return jsonify({"error": "File not found"}), 404

        anchor_provider = (anchor_row.get("provider") or "").strip()
        anchor_pbid = (anchor_row.get("provider_book_id") or "").strip()
        anchor_path = (anchor_row.get("path") or "").strip()
        anchor_source = (anchor_row.get("source") or "").strip()
        if anchor_provider and anchor_pbid and anchor_path and anchor_source:
            monitored_db.record_file_rejection(
                user_ids=visible_user_ids,
                entity_id=entity_id,
                source=anchor_source,
                path=anchor_path,
                provider=anchor_provider,
                provider_book_id=anchor_pbid,
            )
        monitored_db.delete_monitored_book_file_by_id(
            user_ids=visible_user_ids,
            entity_id=entity_id,
            file_id=file_id,
        )
        return jsonify({"ok": True, "rejected": True})

    @app.route(
        "/api/monitored/<int:entity_id>/books/<string:provider>/<path:provider_book_id>/match",
        methods=["POST"],
    )
    def api_attach_book_match(
        entity_id: int, provider: str, provider_book_id: str
    ) -> Response | tuple[Response, int]:
        """Attach a file to a book that has no current attribution (or replace
        one). Backs the "+ Add audiobook/ebook" flow.

        Body shapes:
          { "file_id": N }                         → use existing file row N
          { "source": "...", "path": "...",
            "file_type": "ebook" | "audiobook" }   → use (source, path);
                                                     row is created if missing
        """
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db,
            resolve_auth_mode=resolve_auth_mode,
        )
        if gate is not None:
            return gate

        payload = request.get_json(silent=True) or {}

        rows = monitored_db.list_monitored_book_files(
            user_ids=visible_user_ids,
            entity_id=entity_id,
        )
        if rows is None:
            return jsonify({"error": "Not found"}), 404

        books = (
            monitored_db.list_monitored_books(
                user_ids=visible_user_ids,
                entity_id=entity_id,
            )
            or []
        )
        target_book = next(
            (
                b
                for b in books
                if b.get("provider") == provider and b.get("provider_book_id") == provider_book_id
            ),
            None,
        )
        if target_book is None:
            return jsonify({"error": "Book not found"}), 404

        chosen_row: dict[str, Any] | None = None
        chosen_id = payload.get("file_id")
        chosen_source = (payload.get("source") or "").strip()
        chosen_path = (payload.get("path") or "").strip()
        chosen_file_type = (payload.get("file_type") or "").strip()
        if isinstance(chosen_id, int):
            chosen_row = next((r for r in rows if r.get("id") == chosen_id), None)
            if chosen_row is None:
                return jsonify({"error": "Chosen file not found in this entity"}), 400
            chosen_source = chosen_row.get("source") or "filesystem"
            chosen_path = chosen_row.get("path") or ""
            chosen_file_type = chosen_row.get("file_type") or chosen_file_type
        elif chosen_source and chosen_path and chosen_file_type:
            chosen_row = next(
                (
                    r
                    for r in rows
                    if (r.get("source") or "") == chosen_source
                    and (r.get("path") or "") == chosen_path
                ),
                None,
            )
        else:
            return jsonify({"error": "Must provide file_id or (source, path, file_type)"}), 400

        if chosen_file_type not in {"ebook", "audiobook"}:
            return jsonify({"error": "file_type must be ebook or audiobook"}), 400

        entity = monitored_db.get_monitored_entity(
            user_ids=visible_user_ids,
            entity_id=entity_id,
        )
        author_name = (entity or {}).get("name") if entity else None

        _apply_manual_attribution(
            entity_id=entity_id,
            visible_user_ids=visible_user_ids,
            target_book=target_book,
            chosen_source=chosen_source,
            chosen_path=chosen_path,
            chosen_file_type=chosen_file_type,
            chosen_ext=(chosen_row.get("ext") if chosen_row else None),
            chosen_size_bytes=(chosen_row.get("size_bytes") if chosen_row else None),
            chosen_mtime=(chosen_row.get("mtime") if chosen_row else None),
            author_name=author_name,
        )
        return jsonify({"ok": True})

    @app.route("/api/monitored/<int:entity_id>/books/history", methods=["GET"])
    def api_list_monitored_book_history(entity_id: int) -> Response | tuple[Response, int]:
        """Return structured download/attempt history for a book.

        Reads the structured ``monitored_book_download_history`` and
        ``monitored_book_attempt_history`` tables (source of truth for the
        auto-search precheck). The ``/api/monitored/events`` endpoints are
        the audit-log layer; this endpoint exposes the structured rows.
        """
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        provider = str(request.args.get("provider") or "").strip()
        provider_book_id = str(request.args.get("provider_book_id") or "").strip()
        if not provider or not provider_book_id:
            return jsonify({"error": "provider and provider_book_id are required"}), 400

        raw_limit = request.args.get("limit")
        try:
            limit = int(raw_limit) if raw_limit is not None else 50
        except TypeError, ValueError:
            limit = 50

        rows = monitored_db.list_monitored_book_download_history(
            user_ids=visible_user_ids,
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            limit=limit,
        )
        if rows is None:
            return jsonify({"error": "Not found"}), 404
        attempt_rows = monitored_db.list_monitored_book_attempt_history(
            user_ids=visible_user_ids,
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            limit=limit,
        )
        if attempt_rows is None:
            return jsonify({"error": "Not found"}), 404
        return jsonify({"history": rows, "attempt_history": attempt_rows})

    @app.route("/api/monitored/<int:entity_id>/books/auto-search-precheck", methods=["POST"])
    def api_monitored_auto_search_precheck(entity_id: int) -> Response | tuple[Response, int]:
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"error": "Invalid payload"}), 400

        provider = str(payload.get("provider") or "").strip()
        provider_book_id = str(payload.get("provider_book_id") or "").strip()
        content_type = str(payload.get("content_type") or "ebook").strip().lower()
        if content_type not in {"ebook", "audiobook"}:
            return jsonify({"error": "content_type must be ebook or audiobook"}), 400
        if not provider or not provider_book_id:
            return jsonify({"error": "provider and provider_book_id are required"}), 400

        entity = monitored_db.get_monitored_entity(user_ids=visible_user_ids, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        # Manual flow anchors a session here so subsequent download events
        # group under the same row in History. Scheduled flow records its
        # search_started inside search_missing_books (no double-emission).
        session_id = str(payload.get("session_id") or "").strip() or None
        run_id = str(payload.get("run_id") or "").strip() or None
        book_title = str(payload.get("book_title") or "").strip() or None
        if session_id:
            try:
                from shelfmark.core.monitored_history import record_search_started

                record_search_started(
                    entity_id=entity_id,
                    book_provider=provider,
                    book_provider_id=provider_book_id,
                    book_title=book_title,
                    content_type=content_type,
                    session_id=session_id,
                    user_id=int(entity["user_id"]),
                    metadata={"run_id": run_id} if run_id else None,
                    triggered_by="manual",
                )
            except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                logger.debug(
                    "Failed to record search_started for precheck %s/%s: %s",
                    provider,
                    provider_book_id,
                    exc,
                )

        try:
            skip, reason, detail = resolve_book_auto_search_precheck(
                monitored_db,
                entity_id=entity_id,
                user_id=int(entity["user_id"]),
                provider=provider,
                provider_book_id=provider_book_id,
                content_type=content_type,
            )
        except MonitoredEntityNotFoundError:
            return jsonify({"error": "Not found"}), 404

        return jsonify(
            {
                "ok": True,
                "entity_id": entity_id,
                "provider": provider,
                "provider_book_id": provider_book_id,
                "content_type": content_type,
                "skip": bool(skip),
                "reason": reason,
                "detail": detail,
            }
        )

    @app.route("/api/monitored/<int:entity_id>/books/attempt", methods=["POST"])
    def api_record_monitored_book_attempt(entity_id: int) -> Response | tuple[Response, int]:
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        entity = monitored_db.get_monitored_entity(user_ids=visible_user_ids, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"error": "Invalid payload"}), 400

        provider = str(payload.get("provider") or "").strip()
        provider_book_id = str(payload.get("provider_book_id") or "").strip()
        content_type = str(payload.get("content_type") or "").strip().lower()
        status = str(payload.get("status") or "").strip().lower()

        if not provider or not provider_book_id:
            return jsonify({"error": "provider and provider_book_id are required"}), 400
        if content_type not in {"ebook", "audiobook"}:
            return jsonify({"error": "content_type must be ebook or audiobook"}), 400
        if status not in {
            "queued",
            "no_match",
            "below_cutoff",
            "not_released",
            "download_failed",
            "error",
        }:
            return jsonify({"error": "invalid status"}), 400

        source = str(payload.get("source") or "").strip() or None
        source_id = str(payload.get("source_id") or "").strip() or None
        release_title = str(payload.get("release_title") or "").strip() or None
        error_message = str(payload.get("error_message") or "").strip() or None
        book_title = str(payload.get("book_title") or "").strip() or None

        raw_match_score = payload.get("match_score")
        match_score: float | None = None
        if raw_match_score is not None:
            try:
                match_score = float(raw_match_score)
            except TypeError, ValueError:
                match_score = None

        attempt_session_id = str(payload.get("session_id") or "").strip() or None
        attempt_run_id = str(payload.get("run_id") or "").strip() or None
        attempt_metadata = {"run_id": attempt_run_id} if attempt_run_id else None

        write_monitored_book_attempt(
            monitored_db,
            user_id=int(entity["user_id"]),
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            content_type=content_type,
            status=status,
            source=source,
            source_id=source_id,
            release_title=release_title,
            match_score=match_score,
            error_message=error_message,
            book_title=book_title,
            session_id=attempt_session_id,
            metadata=attempt_metadata,
        )
        return jsonify({"ok": True})

    @app.route("/api/monitored/<int:entity_id>/scan-files", methods=["POST"])
    def api_scan_monitored_files(entity_id: int) -> Response | tuple[Response, int]:
        db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify(
                {
                    "error": message or "Monitoring is unavailable by policy",
                    "code": "policy_blocked",
                }
            ), 403

        entity = monitored_db.get_monitored_entity(user_ids=visible_user_ids, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404
        if entity.get("kind") != "author":
            return jsonify({"error": "Scan is only supported for author entities"}), 400

        if not resolve_allowed_roots(user_db, db_user_id=int(db_user_id or 0)):
            return jsonify({"error": "No allowed roots configured"}), 400

        entity_owner_id = int(entity["user_id"])

        sync_result: AvailabilitySyncResult = sync_availability_sources(
            monitored_db,
            entity_id=entity_id,
            entity_name=entity.get("name") or "",
            user_id=entity_owner_id,
            user_db=user_db,
        )

        # Translate any filesystem-scan exception captured by the helper into
        # the appropriate HTTP response. ABS/Grimmory errors don't fail the
        # whole call — they're surfaced inside their result dicts.
        fs_err = sync_result.fs_error
        if fs_err is not None:
            if isinstance(fs_err, MonitoredEntityNotFoundError):
                return jsonify({"error": "Not found"}), 404
            if isinstance(fs_err, MonitoredPathError):
                msg = str(fs_err)
                if msg == "ebook_author_dir or audiobook_author_dir must be set":
                    return jsonify({"error": msg}), 400
                if msg in {
                    "ebook_author_dir is not within allowed roots",
                    "audiobook_author_dir is not within allowed roots",
                }:
                    return jsonify({"error": "Path not allowed"}), 403
                if msg in {"Invalid ebook_author_dir", "Invalid audiobook_author_dir"}:
                    return jsonify({"error": msg}), 400
                if msg == "directories_not_found":
                    return jsonify(
                        {"error": "Directory not found", "details": {}, "files_cleared": True}
                    ), 404
                return jsonify({"error": msg}), 400
            # Unknown exception — log a scan_error event for the History UI.
            settings = entity.get("settings") or {}
            record_scan_error(
                monitored_db,
                entity_id=entity_id,
                user_id=entity_owner_id,
                error=fs_err,
                ebook_dir=str(settings.get("ebook_author_dir") or "").strip().rstrip("/"),
                audiobook_dir=str(settings.get("audiobook_author_dir") or "").strip().rstrip("/"),
            )
            return jsonify({"error": "Scan failed"}), 500

        scan = sync_result.fs_scan
        if scan is None:
            return jsonify({"error": "Scan produced no result"}), 500

        return jsonify(
            {
                "ok": True,
                "entity_id": entity_id,
                "scanned": {
                    "ebook_author_dir": scan.ebook_dir,
                    "audiobook_author_dir": scan.audiobook_dir,
                },
                "warnings": scan.warnings,
                "stats": {
                    "ebook_files_scanned": scan.scanned_ebook_files,
                    "audiobook_folders_scanned": scan.scanned_audio_folders,
                    "matched": len(scan.matched),
                    "unmatched": len(scan.unmatched),
                    "truncated": scan.truncated,
                },
                "matched": scan.matched,
                "unmatched": scan.unmatched,
                "missing_books": scan.missing_books,
                "abs": sync_result.abs,
                "grimmory": sync_result.gm,
            }
        )

    @app.route("/api/monitored/<int:entity_id>/books/monitor-flags", methods=["PATCH"])
    def api_update_monitored_books_monitor_flags(entity_id: int) -> Response | tuple[Response, int]:
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        payload = request.get_json(silent=True)
        if isinstance(payload, dict):
            rows = [payload]
        elif isinstance(payload, list):
            rows = payload
        else:
            return jsonify({"error": "Expected a JSON object or array"}), 400

        updated = 0
        results: list[dict[str, Any]] = []
        for item in rows:
            if not isinstance(item, dict):
                continue

            provider = str(item.get("provider") or "").strip()
            provider_book_id = str(item.get("provider_book_id") or "").strip()
            if not provider or not provider_book_id:
                continue

            monitor_ebook = item.get("monitor_ebook") if "monitor_ebook" in item else None
            monitor_audiobook = (
                item.get("monitor_audiobook") if "monitor_audiobook" in item else None
            )
            hidden = item.get("hidden") if "hidden" in item else None

            if monitor_ebook is not None:
                monitor_ebook = bool(monitor_ebook)
            if monitor_audiobook is not None:
                monitor_audiobook = bool(monitor_audiobook)
            if hidden is not None:
                hidden = bool(hidden)

            if monitor_ebook is None and monitor_audiobook is None and hidden is None:
                continue

            # Lock monitor flags when user explicitly changes them (not via hide/unhide)
            lock = (
                True
                if hidden is None and (monitor_ebook is not None or monitor_audiobook is not None)
                else None
            )

            result = monitored_db.set_monitored_book_monitor_flags(
                user_ids=visible_user_ids,
                entity_id=entity_id,
                provider=provider,
                provider_book_id=provider_book_id,
                monitor_ebook=monitor_ebook,
                monitor_audiobook=monitor_audiobook,
                hidden=hidden,
                monitor_locked=lock,
            )
            if result is not None:
                updated += 1
                results.append(
                    {
                        "provider": provider,
                        "provider_book_id": provider_book_id,
                        **result,
                    }
                )

        return jsonify({"ok": True, "updated": updated, "results": results})

    # ── Release-date lookup (AudiMeta + Google Books) ───────────

    def _search_audimeta(title: str, author: str) -> list[dict[str, Any]]:
        """Search AudiMeta for books, returning normalised result dicts."""
        from shelfmark.core.config import config as app_config

        base_url = str(app_config.get("AUDIBLE_BASE_URL", "https://audimeta.de")).rstrip("/")
        region = str(app_config.get("AUDIBLE_REGION", "us"))
        user_agent = str(
            app_config.get(
                "AUDIBLE_USER_AGENT",
                "Shelfmark Audible Provider/1.0 (+https://github.com/calibrain/shelfmark; metadata-provider)",
            )
        )
        params: dict[str, Any] = {"cache": "true", "region": region, "limit": "10"}
        if title:
            params["title"] = title
        if author:
            params["author"] = author
        try:
            import requests as http_requests

            from shelfmark.download.network import get_ssl_verify

            resp = http_requests.get(
                f"{base_url}/search",
                params=params,
                headers={"User-Agent": user_agent},
                timeout=15,
                verify=get_ssl_verify(base_url),
            )
            resp.raise_for_status()
            items = resp.json()
            if not isinstance(items, list):
                return []
        except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            logger.warning("AudiMeta search failed: %s", exc)
            return []

        results: list[dict[str, Any]] = []
        for item in items:
            raw_date = item.get("releaseDate") or ""
            release_date = raw_date[:10] if len(raw_date) >= 10 else None
            pub_year = None
            if release_date:
                with contextlib.suppress(ValueError, TypeError):
                    pub_year = int(release_date[:4])
            authors = [
                a.get("name", "") for a in (item.get("authors") or []) if isinstance(a, dict)
            ]
            results.append(
                {
                    "asin": item.get("asin") or "",
                    "title": item.get("title") or "",
                    "authors": authors,
                    "release_date": release_date,
                    "publish_year": pub_year,
                    "cover_url": item.get("imageUrl") or None,
                    "series_name": next(
                        (
                            s.get("name")
                            for s in (item.get("series") or [])
                            if isinstance(s, dict) and s.get("name")
                        ),
                        None,
                    ),
                    "source": "audible",
                }
            )
        return results

    @app.route("/api/monitored/release-date-search")
    def api_release_date_search() -> Response | tuple[Response, int]:
        _db_user_id, _global_user_id, _visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        title = request.args.get("title", "").strip()
        author = request.args.get("author", "").strip()
        if not title and not author:
            return jsonify({"error": "title or author required"}), 400

        from shelfmark.core.config import config as app_config
        from shelfmark.core.monitored_release_enricher import search_google_books

        api_key = str(app_config.get("GOOGLEBOOKS_API_KEY", "") or "")

        try:
            audimeta_results = _search_audimeta(title, author)
        except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            audimeta_results = []
        try:
            google_results = search_google_books(title, author, api_key=api_key)
        except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            google_results = []

        hardcover_results: list[dict[str, Any]] = []
        try:
            from shelfmark.metadata_providers import get_configured_provider
            from shelfmark.metadata_providers.hardcover import HardcoverProvider

            provider = get_configured_provider()
            if isinstance(provider, HardcoverProvider):
                from shelfmark.metadata_providers.base import (
                    MetadataSearchOptions,
                    SearchType,
                    SortOrder,
                )

                query = title or author
                fields: dict[str, str] = {}
                if title:
                    fields["title"] = title
                if author:
                    fields["author"] = author
                sr = provider.search(
                    MetadataSearchOptions(
                        query=query,
                        search_type=SearchType.BOOK,
                        sort=SortOrder.RELEVANCE,
                        limit=10,
                        page=1,
                        fields=fields,
                    )
                )
                hardcover_results.extend(
                    {
                        "asin": "",
                        "title": book.title or "",
                        "authors": list(book.authors) if book.authors else [],
                        "release_date": book.release_date,
                        "publish_year": book.publish_year,
                        "cover_url": book.cover_url,
                        "series_name": book.series_name,
                        "source": "hardcover",
                    }
                    for book in sr.books
                )
        except Exception:  # noqa: BLE001, S110 — best-effort path inside a route handler; intentional swallow
            pass

        results = hardcover_results + audimeta_results + google_results

        return jsonify({"results": results})

    @app.route("/api/monitored/<int:entity_id>/books/release-date", methods=["PATCH"])
    def api_set_release_date(entity_id: int) -> Response | tuple[Response, int]:
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        payload = request.get_json(silent=True) or {}
        provider = str(payload.get("provider") or "").strip()
        provider_book_id = str(payload.get("provider_book_id") or "").strip()
        asin = str(payload.get("asin") or "").strip()
        release_date = payload.get("release_date") or None

        if not provider or not provider_book_id:
            return jsonify({"error": "provider and provider_book_id required"}), 400

        ok = monitored_db.update_book_release_date(
            user_ids=visible_user_ids,
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
            release_date=release_date,
            audible_asin=asin or None,
        )
        if not ok:
            return jsonify({"error": "Book not found or not authorized"}), 404

        return jsonify({"ok": True})

    @app.route("/api/monitored/<int:entity_id>/search", methods=["POST"])
    def api_search_monitored_entity(entity_id: int) -> Response | tuple[Response, int]:
        db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify(
                {
                    "error": message or "Monitoring is unavailable by policy",
                    "code": "policy_blocked",
                }
            ), 403

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"error": "Invalid payload"}), 400

        content_type = str(payload.get("content_type") or "ebook").strip().lower()
        if content_type not in {"ebook", "audiobook"}:
            return jsonify({"error": "content_type must be ebook or audiobook"}), 400

        entity = monitored_db.get_monitored_entity(user_ids=visible_user_ids, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        from shelfmark.core.config import config as app_config

        threshold = float(
            app_config.get("AUTO_DOWNLOAD_MIN_MATCH_SCORE", 75, user_id=int(db_user_id or 0)) or 75
        )

        try:
            result = search_missing_books(
                monitored_db,
                entity_id=entity_id,
                user_id=int(entity["user_id"]),
                content_type=content_type,
                min_match_score=threshold / 100.0,
            )
        except MonitoredEntityNotFoundError:
            return jsonify({"error": "Not found"}), 404

        return jsonify(
            {
                "ok": True,
                "entity_id": result.entity_id,
                "content_type": result.content_type,
                "total_candidates": result.total_candidates,
                "skipped_history_final_path_exists": result.skipped_history_final_path_exists,
                "skipped_existing_file": result.skipped_existing_file,
                "queued": result.queued,
                "unreleased": result.unreleased,
                "no_match": result.no_match,
                "below_cutoff": result.below_cutoff,
                "failed": result.failed,
            }
        )

    @app.route("/api/monitored/<int:entity_id>/sync", methods=["POST"])
    def api_sync_monitored(entity_id: int) -> Response | tuple[Response, int]:
        db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify(
                {
                    "error": message or "Monitoring is unavailable by policy",
                    "code": "policy_blocked",
                }
            ), 403

        entity = monitored_db.get_monitored_entity(user_ids=visible_user_ids, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        if entity.get("kind") == "author":
            if entity.get("sync_status") == "syncing":
                return jsonify({"ok": True, "syncing": True, "already_syncing": True})
            monitored_db.update_entity_sync_status(entity_id, "syncing")
            start_author_background_sync(
                entity_id,
                int(entity["user_id"]),
                monitored_db,
                ws_manager=ws_manager,
                user_db=user_db,
            )
            return jsonify({"ok": True, "syncing": True})

        return jsonify({"ok": True, "syncing": False})

    # ------------------------------------------------------------------
    # Delete a single monitored book
    # ------------------------------------------------------------------

    @app.route(
        "/api/monitored/<int:entity_id>/books/<provider>/<provider_book_id>", methods=["DELETE"]
    )
    def api_delete_monitored_book(
        entity_id: int, provider: str, provider_book_id: str
    ) -> Response | tuple[Response, int]:
        db_user_id, global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify(
                {
                    "error": message or "Monitoring is unavailable by policy",
                    "code": "policy_blocked",
                }
            ), 403

        entity = monitored_db.get_monitored_entity(user_ids=visible_user_ids, entity_id=entity_id)
        if entity is None:
            return jsonify({"error": "Not found"}), 404

        if not _can_edit_entity(entity, db_user_id=db_user_id, global_user_id=global_user_id):
            return jsonify({"error": "You don't have permission to modify this author"}), 403

        deleted = monitored_db.delete_monitored_book(
            entity_id=entity_id,
            provider=provider,
            provider_book_id=provider_book_id,
        )
        return jsonify({"ok": True, "deleted": deleted})

    # ------------------------------------------------------------------
    # Sync all authors
    # ------------------------------------------------------------------

    @app.route("/api/monitored/sync-all", methods=["POST"])
    def api_sync_all_monitored() -> Response | tuple[Response, int]:
        db_user_id, _global_user_id, _visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        allowed, message = _policy_allows_monitoring(user_db=user_db, db_user_id=db_user_id)
        if not allowed:
            return jsonify(
                {
                    "error": message or "Monitoring is unavailable by policy",
                    "code": "policy_blocked",
                }
            ), 403

        # Guard against concurrent batch syncs (atomic check-then-set)
        with _batch_sync_lock:
            if app.extensions.get("monitored_batch_sync_running"):
                return jsonify({"ok": True, "already_running": True}), 409
            app.extensions["monitored_batch_sync_running"] = True

        entities = _collect_enabled_author_entities()
        if not entities:
            with _batch_sync_lock:
                app.extensions["monitored_batch_sync_running"] = False
            return jsonify({"ok": True, "total": 0})

        batch_id = f"manual-{datetime.now().astimezone().strftime('%Y%m%d-%H%M%S')}"

        def _run_and_clear() -> None:
            try:
                run_batch_sync(entities, monitored_db, ws_manager, user_db, batch_id=batch_id)
            finally:
                with _batch_sync_lock:
                    app.extensions["monitored_batch_sync_running"] = False

        t = threading.Thread(
            target=_run_and_clear, daemon=True, name=f"MonitoredBatchSync-{batch_id}"
        )
        t.start()

        return jsonify({"ok": True, "batch_id": batch_id, "total": len(entities)})

    @app.route("/api/monitored/run-started", methods=["POST"])
    def api_record_monitored_run_started() -> Response | tuple[Response, int]:
        """Record a manual batch auto-download run.

        Frontend calls this once at the start of a bulk auto-download to
        anchor the History UI's run grouping. Scheduled runs call the
        recorder directly from the scheduler thread.
        """
        db_user_id, _, _, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        payload = request.get_json(silent=True) or {}
        if not isinstance(payload, dict):
            return jsonify({"error": "Invalid payload"}), 400

        run_id = str(payload.get("run_id") or "").strip()
        trigger = str(payload.get("trigger") or "manual").strip().lower()
        if trigger not in {"scheduled", "manual"}:
            return jsonify({"error": "trigger must be scheduled or manual"}), 400
        if not run_id:
            return jsonify({"error": "run_id is required"}), 400

        try:
            total_candidates = int(payload.get("total_candidates") or 0)
        except TypeError, ValueError:
            total_candidates = 0

        slot = str(payload.get("slot") or "").strip() or None

        try:
            from shelfmark.core.monitored_history import record_run_started

            record_run_started(
                run_id=run_id,
                trigger=trigger,
                total_candidates=total_candidates,
                slot=slot,
                user_id=int(db_user_id) if db_user_id is not None else None,
            )
        except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            logger.warning("Failed to record run_started for %s: %s", run_id, exc)
            return jsonify({"error": "Failed to record run"}), 500

        return jsonify({"ok": True, "run_id": run_id})

    # ------------------------------------------------------------------
    # Save locations for standalone (non-monitored) downloads
    # ------------------------------------------------------------------

    @app.route("/api/download-destinations", methods=["GET"])
    def api_download_destinations() -> Response | tuple[Response, int]:
        """List the save locations offered for a standalone download.

        Query parameters:
          - content_type: "ebook" or "audiobook"; scopes the list so an ebook
            is not offered audiobook-only folders.

        The first entry is the configured default for that content type, which
        is what the download uses when the picker is left alone.
        """
        from shelfmark.core.utils import get_destination

        if user_db is None:
            return jsonify({"error": "Save locations unavailable"}), 503

        raw_user_id = session.get("db_user_id")
        try:
            db_user_id = int(raw_user_id)
        except TypeError, ValueError:
            return jsonify({"error": "Invalid user context"}), 400

        content_type = (request.args.get("content_type") or "").strip().lower()
        if content_type not in ("ebook", "audiobook"):
            return jsonify({"error": "content_type must be ebook or audiobook"}), 400

        # The session stores the username under "user_id" (see api_download_release);
        # get_destination() needs it to expand a {username} placeholder in DESTINATION.
        username = session.get("user_id")
        try:
            default_path = get_destination(
                is_audiobook=content_type == "audiobook",
                user_id=db_user_id,
                username=str(username) if username else None,
            ).resolve()
        except OSError:
            logger.warning("Could not resolve default destination", exc_info=True)
            return jsonify({"error": "Could not resolve default save location"}), 500

        destinations: list[dict[str, Any]] = [
            {"path": str(default_path), "label": "Default", "is_default": True}
        ]
        seen = {str(default_path)}

        try:
            roots = resolve_allowed_roots(user_db, db_user_id=db_user_id, content_type=content_type)
        except (OSError, AttributeError, TypeError, ValueError):
            # Route-boundary defensive catch; an empty list still leaves the default usable.
            logger.warning("Could not resolve save locations", exc_info=True)
            roots = []

        for root in roots:
            path_str = str(root)
            if path_str in seen:
                continue
            seen.add(path_str)
            destinations.append(
                {"path": path_str, "label": root.name or path_str, "is_default": False}
            )

        return jsonify({"destinations": destinations})

    # ------------------------------------------------------------------
    # File system directory browser (for monitored folder picker UI)
    # ------------------------------------------------------------------

    @app.route("/api/fs/list", methods=["GET"])
    def api_fs_list() -> Response | tuple[Response, int]:
        """List directories for folder browsing UI.

        Query parameters:
          - path: absolute path to list; if omitted, returns allowed roots.

        Safety:
          Only lists directories within allowed roots derived from config and per-user settings.
        """
        from shelfmark.core.config import config as app_config

        if user_db is None:
            return jsonify({"error": "Filesystem browsing unavailable"}), 503

        raw_user_id = session.get("db_user_id")
        try:
            db_user_id = int(raw_user_id)
        except TypeError, ValueError:
            return jsonify({"error": "Invalid user context"}), 400

        requested = (request.args.get("path") or "").strip()

        def _normalize_root(value: Any) -> str | None:
            if not isinstance(value, str):
                return None
            v = value.strip().rstrip("/")
            if not v or not v.startswith("/"):
                return None
            return v

        # Allowed roots: configured destinations + remembered monitored roots.
        allowed_roots: list[Path] = []
        try:
            dest = _normalize_root(app_config.get("DESTINATION", "/books", user_id=db_user_id))
            if dest:
                allowed_roots.append(Path(dest).resolve())
            dest_audio = _normalize_root(
                app_config.get("DESTINATION_AUDIOBOOK", "", user_id=db_user_id)
            )
            if dest_audio:
                allowed_roots.append(Path(dest_audio).resolve())
        except Exception:  # noqa: BLE001, S110 — best-effort path inside a route handler; intentional swallow
            pass

        try:
            user_settings = user_db.get_user_settings(db_user_id)
        except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            user_settings = {}

        for key in ("MONITORED_EBOOK_ROOTS", "MONITORED_AUDIOBOOK_ROOTS"):
            roots_value = user_settings.get(key)
            if isinstance(roots_value, list):
                for item in roots_value:
                    root = _normalize_root(item)
                    if root:
                        allowed_roots.append(Path(root).resolve())

        # De-dupe
        unique_roots: list[Path] = []
        seen: set[str] = set()
        for root in allowed_roots:
            s = str(root)
            if s not in seen:
                seen.add(s)
                unique_roots.append(root)

        if not requested:
            return jsonify(
                {
                    "path": None,
                    "parent": None,
                    "directories": [
                        {"name": p.name or str(p), "path": str(p)} for p in unique_roots
                    ],
                }
            )

        if not requested.startswith("/"):
            return jsonify({"error": "path must be absolute"}), 400

        try:
            requested_path = Path(requested).resolve()
        except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            return jsonify({"error": "Invalid path"}), 400

        # Ensure requested path is within at least one allowed root.
        allowed = False
        for root in unique_roots:
            try:
                requested_path.relative_to(root)
                allowed = True
                break
            except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                continue

        if not allowed:
            return jsonify({"error": "Path not allowed"}), 403

        if not requested_path.exists() or not requested_path.is_dir():
            return jsonify({"error": "Directory not found"}), 404

        try:
            children: list[dict[str, str]] = []
            for entry in sorted(requested_path.iterdir(), key=lambda p: p.name.lower()):
                try:
                    if entry.is_dir():
                        children.append({"name": entry.name, "path": str(entry)})
                except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                    continue
        except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            return jsonify({"error": f"Failed to list directory: {exc}"}), 500

        parent: str | None = None
        try:
            if requested_path.parent != requested_path:
                for root in unique_roots:
                    try:
                        requested_path.parent.relative_to(root)
                        parent = str(requested_path.parent)
                        break
                    except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                        continue
        except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            parent = None

        return jsonify(
            {
                "path": str(requested_path),
                "parent": parent,
                "directories": children,
            }
        )

    @app.route("/api/fs/mkdir", methods=["POST"])
    def api_fs_mkdir() -> Response | tuple[Response, int]:
        """Create a new directory within an allowed root.

        Request body (JSON):
          - parent: absolute path of the parent directory
          - name: new folder name (no path separators allowed)
        """
        from shelfmark.core.config import config as app_config

        if user_db is None:
            return jsonify({"error": "Filesystem operations unavailable"}), 503

        raw_user_id = session.get("db_user_id")
        try:
            db_user_id = int(raw_user_id)
        except TypeError, ValueError:
            return jsonify({"error": "Invalid user context"}), 400

        data = request.get_json(silent=True) or {}
        parent_raw = (data.get("parent") or "").strip()
        name_raw = (data.get("name") or "").strip()

        if not parent_raw or not name_raw:
            return jsonify({"error": "parent and name are required"}), 400
        if "/" in name_raw or "\\" in name_raw or name_raw in (".", ".."):
            return jsonify({"error": "Invalid folder name"}), 400

        def _normalize_root(value: Any) -> str | None:
            if not isinstance(value, str):
                return None
            v = value.strip().rstrip("/")
            if not v or not v.startswith("/"):
                return None
            return v

        allowed_roots: list[Path] = []
        try:
            dest = _normalize_root(app_config.get("DESTINATION", "/books", user_id=db_user_id))
            if dest:
                allowed_roots.append(Path(dest).resolve())
            dest_audio = _normalize_root(
                app_config.get("DESTINATION_AUDIOBOOK", "", user_id=db_user_id)
            )
            if dest_audio:
                allowed_roots.append(Path(dest_audio).resolve())
        except Exception:  # noqa: BLE001, S110 — best-effort path inside a route handler; intentional swallow
            pass

        try:
            user_settings = user_db.get_user_settings(db_user_id)
        except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            user_settings = {}

        for key in ("MONITORED_EBOOK_ROOTS", "MONITORED_AUDIOBOOK_ROOTS"):
            roots_value = user_settings.get(key)
            if isinstance(roots_value, list):
                for item in roots_value:
                    root = _normalize_root(item)
                    if root:
                        allowed_roots.append(Path(root).resolve())

        try:
            parent_path = Path(parent_raw).resolve()
        except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            return jsonify({"error": "Invalid parent path"}), 400

        allowed = False
        for root in allowed_roots:
            try:
                parent_path.relative_to(root)
                allowed = True
                break
            except Exception:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
                continue
        if not allowed:
            return jsonify({"error": "Path not allowed"}), 403

        if not parent_path.exists() or not parent_path.is_dir():
            return jsonify({"error": "Parent directory not found"}), 404

        new_dir = parent_path / name_raw
        if new_dir.exists():
            return jsonify({"path": str(new_dir)}), 200

        try:
            new_dir.mkdir(parents=False, exist_ok=True)
        except Exception as exc:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            return jsonify({"error": f"Failed to create directory: {exc}"}), 500

        return jsonify({"path": str(new_dir)}), 201

    # ------------------------------------------------------------------
    # Metadata author search (hardcover-specific, used by monitored UI)
    # ------------------------------------------------------------------

    @app.route("/api/metadata/authors/search", methods=["GET"])
    def api_metadata_author_search() -> Response | tuple[Response, int]:
        """Search for authors using the configured metadata provider."""
        _db_user_id, _global_user_id, _visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        try:
            from shelfmark.core.utils import transform_cover_url
            from shelfmark.metadata_providers import get_configured_provider

            query = request.args.get("query", "").strip()
            content_type = request.args.get("content_type", "ebook").strip()

            try:
                limit = min(int(request.args.get("limit", 20)), 50)
            except ValueError:
                limit = 20

            try:
                page = max(1, int(request.args.get("page", 1)))
            except ValueError:
                page = 1

            if not query:
                return jsonify({"error": "'query' is required"}), 400

            provider = get_configured_provider(content_type=content_type)
            if not provider:
                return jsonify(
                    {
                        "error": "No metadata provider configured",
                        "message": "No metadata provider configured. Enable one in Settings.",
                    }
                ), 503

            if not provider.is_available():
                return jsonify(
                    {
                        "error": f"Metadata provider '{provider.name}' is not available",
                        "message": f"{getattr(provider, 'display_name', provider.name)} is not available. Check configuration in Settings.",
                    }
                ), 503

            if provider.name != "hardcover":
                return jsonify(
                    {
                        "provider": provider.name,
                        "query": query,
                        "page": page,
                        "supports_authors": False,
                        "authors": [],
                    }
                )

            from shelfmark.metadata_providers.hardcover import HardcoverProvider

            if not isinstance(provider, HardcoverProvider):
                return jsonify(
                    {
                        "provider": provider.name,
                        "query": query,
                        "page": page,
                        "supports_authors": False,
                        "authors": [],
                    }
                )

            graphql_query = """
            query SearchAuthors($query: String!, $limit: Int!, $page: Int!) {
                search(query: $query, query_type: "Author", per_page: $limit, page: $page) {
                    results
                }
            }
            """

            result = provider._execute_query(
                graphql_query,
                {
                    "query": query,
                    "limit": limit,
                    "page": page,
                },
            )
            if not result:
                return jsonify(
                    {
                        "provider": provider.name,
                        "query": query,
                        "page": page,
                        "supports_authors": True,
                        "authors": [],
                    }
                )

            results_obj = result.get("search", {}).get("results", {})
            hits = []
            found_count = 0
            if isinstance(results_obj, dict):
                hits = results_obj.get("hits", [])
                found_count = results_obj.get("found", 0) or 0
            elif isinstance(results_obj, list):
                hits = results_obj

            authors = []
            for hit in hits:
                item = hit.get("document", hit) if isinstance(hit, dict) else hit
                if not isinstance(item, dict):
                    continue

                author_id = item.get("id")
                name = item.get("name")
                if not author_id or not name:
                    continue

                raw_photo = extract_author_photo_url(item)
                photo_url = (
                    transform_cover_url(raw_photo, f"hardcover_author_{author_id}")
                    if raw_photo
                    else None
                )

                author_payload: dict[str, Any] = {
                    "provider": "hardcover",
                    "provider_id": str(author_id),
                    "name": str(name),
                    "photo_url": photo_url,
                    "bio": item.get("bio") or item.get("description"),
                    "born_year": item.get("born_year") or item.get("birth_year"),
                    "source_url": None,
                    "stats": {
                        "books_count": item.get("books_count") or item.get("works_count"),
                        "users_count": item.get("users_count"),
                        "ratings_count": item.get("ratings_count"),
                        "rating": item.get("rating"),
                    },
                }

                slug = item.get("slug")
                if slug and isinstance(slug, str):
                    author_payload["source_url"] = f"https://hardcover.app/authors/{slug}"

                authors.append(author_payload)

            # Typesense search index often lacks author photos.
            # Batch-fetch missing photos from the direct GraphQL API.
            authors_missing_photo = [a for a in authors if not a.get("photo_url")]
            if authors_missing_photo:
                try:
                    _backfill_search_author_photos(
                        provider, authors_missing_photo, transform_cover_url
                    )
                except Exception:
                    logger.debug("Failed to backfill search author photos", exc_info=True)

            has_more = False
            if found_count and isinstance(found_count, int):
                results_so_far = (page - 1) * limit + len(hits)
                has_more = results_so_far < found_count
            else:
                has_more = len(authors) >= limit

            return jsonify(
                {
                    "provider": provider.name,
                    "query": query,
                    "page": page,
                    "total_found": found_count,
                    "has_more": has_more,
                    "supports_authors": True,
                    "authors": authors,
                }
            )

        except Exception as e:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            logger.error_trace(f"Metadata author search error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/metadata/authors/<provider>/<author_id>", methods=["GET"])
    def api_metadata_author(provider: str, author_id: str) -> Response | tuple[Response, int]:
        """Get detailed author information from a metadata provider."""
        _db_user_id, _global_user_id, _visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        try:
            from shelfmark.core.utils import transform_cover_url
            from shelfmark.metadata_providers import (
                get_provider,
                get_provider_kwargs,
                is_provider_registered,
            )

            if not is_provider_registered(provider):
                return jsonify({"error": f"Unknown metadata provider: {provider}"}), 400

            kwargs = get_provider_kwargs(provider)
            prov = get_provider(provider, **kwargs)
            if not prov.is_available():
                return jsonify({"error": f"Provider '{provider}' is not available"}), 503

            if provider != "hardcover":
                return jsonify(
                    {
                        "provider": provider,
                        "provider_id": str(author_id),
                        "supports_authors": False,
                        "author": None,
                    }
                )

            from shelfmark.metadata_providers.hardcover import HardcoverProvider

            if not isinstance(prov, HardcoverProvider):
                return jsonify(
                    {
                        "provider": provider,
                        "provider_id": str(author_id),
                        "supports_authors": False,
                        "author": None,
                    }
                )

            from shelfmark.core.metadata_cache import get_metadata_file_cache

            mcache = get_metadata_file_cache()
            cached = mcache.get("authors", provider, author_id)
            if cached is not None:
                return jsonify(cached)

            graphql_query = """
            query GetAuthor($id: Int!) {
                authors(where: {id: {_eq: $id}}, limit: 1) {
                    id
                    name
                    slug
                    bio
                    image { url }
                    books_count
                }
            }
            """

            try:
                author_id_int = int(author_id)
            except ValueError:
                return jsonify({"error": "Invalid author_id"}), 400

            result = prov._execute_query(graphql_query, {"id": author_id_int})
            if not result:
                return jsonify(
                    {
                        "provider": provider,
                        "provider_id": str(author_id),
                        "supports_authors": True,
                        "author": None,
                    }
                ), 404

            authors = result.get("authors", [])
            if not authors:
                return jsonify(
                    {
                        "provider": provider,
                        "provider_id": str(author_id),
                        "supports_authors": True,
                        "author": None,
                    }
                ), 404

            author = authors[0]

            photo_url = extract_author_photo_url(author)
            if photo_url:
                cache_id = f"hardcover_author_{author.get('id')}"
                photo_url = transform_cover_url(photo_url, cache_id)

            payload: dict[str, Any] = {
                "provider": "hardcover",
                "provider_id": str(author.get("id")),
                "name": author.get("name") or "",
                "photo_url": photo_url,
                "bio": author.get("bio"),
                "born_year": author.get("born_year"),
                "source_url": None,
                "stats": {
                    "books_count": author.get("books_count"),
                    "users_count": author.get("users_count"),
                    "ratings_count": author.get("ratings_count"),
                    "rating": author.get("rating"),
                },
            }

            slug = author.get("slug")
            if slug and isinstance(slug, str):
                payload["source_url"] = f"https://hardcover.app/authors/{slug}"

            response_data = {
                "provider": provider,
                "provider_id": str(author_id),
                "supports_authors": True,
                "author": payload,
            }

            mcache.set("authors", provider, author_id, response_data)
            return jsonify(response_data)

        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        except Exception as e:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            logger.error_trace(f"Metadata author details error: {e}")
            return jsonify({"error": str(e)}), 500

    @app.route("/api/metadata/authors/<provider>/<author_id>/books", methods=["GET"])
    def api_metadata_author_books(provider: str, author_id: str) -> Response | tuple[Response, int]:
        """Fetch an author's book list directly from a metadata provider (no DB writes)."""
        _db_user_id, _global_user_id, _visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        try:
            limit = min(int(request.args.get("limit", 200)), 500)
        except TypeError, ValueError:
            limit = 200

        try:
            from shelfmark.core.utils import transform_cover_url
            from shelfmark.metadata_providers import (
                get_provider,
                get_provider_kwargs,
                is_provider_registered,
            )

            if not is_provider_registered(provider):
                return jsonify({"error": f"Unknown metadata provider: {provider}"}), 400

            kwargs = get_provider_kwargs(provider)
            prov = get_provider(provider, **kwargs)
            if not prov.is_available():
                return jsonify({"error": f"Provider '{provider}' is not available"}), 503

            if provider != "hardcover":
                return jsonify({"provider": provider, "provider_id": str(author_id), "books": []})

            from shelfmark.core.monitored_hardcover_ext import MonitoredHardcoverProvider

            if not isinstance(prov, MonitoredHardcoverProvider):
                # Upgrade the provider instance to the monitored subclass
                mono_prov = MonitoredHardcoverProvider(**kwargs)
            else:
                mono_prov = prov

            if not mono_prov.is_available():
                return jsonify({"error": "Provider not available"}), 503

            raw_books = []
            offset = 0
            page_size = min(limit, 100)
            while len(raw_books) < limit:
                batch = mono_prov.browse_author_books(author_id, offset=offset, limit=page_size)
                if not batch:
                    break
                raw_books.extend(batch)
                if len(batch) < page_size:
                    break
                offset += page_size

            raw_books = raw_books[:limit]

            def _normalize_book(book: dict) -> dict:
                # Series: prefer featured_book_series, fall back to book_series[0].
                # featured_book_series may be a single dict (Hardcover returns just the
                # featured entry) while book_series is always a list.
                fbs = book.get("featured_book_series")
                first: dict | None = None
                if isinstance(fbs, dict) and fbs:
                    first = fbs
                elif isinstance(fbs, list) and fbs:
                    first = fbs[0]
                else:
                    bs = book.get("book_series")
                    if isinstance(bs, list) and bs:
                        first = bs[0]
                series_name: str | None = None
                series_position: float | None = None
                series_count: int | None = None
                if first:
                    s = first.get("series") or {}
                    series_name = s.get("name") or None
                    series_position = first.get("position")
                    series_count = s.get("primary_books_count")

                # Cover URL
                cover_url: str | None = None
                image = book.get("image")
                if isinstance(image, dict):
                    cover_url = image.get("url")
                elif isinstance(image, str):
                    cover_url = image
                if cover_url:
                    cache_id = f"hardcover_book_{book.get('id')}"
                    cover_url = transform_cover_url(cover_url, cache_id)

                # Release date / year
                release_date: str | None = book.get("release_date")
                edition = book.get("default_physical_edition") or {}
                if not release_date:
                    release_date = edition.get("release_date")
                publish_year: int | None = None
                if release_date:
                    with contextlib.suppress(ValueError, TypeError):
                        publish_year = int(str(release_date)[:4])

                # ISBN
                isbn_13: str | None = None
                preferred = book.get("preferred_isbns") or []
                if preferred:
                    isbn_13 = preferred[0].get("isbn_13")
                if not isbn_13:
                    isbn_13 = edition.get("isbn_13")

                return {
                    "provider": "hardcover",
                    "provider_book_id": str(book.get("id")),
                    "title": book.get("title") or "",
                    "publish_year": publish_year,
                    "release_date": release_date,
                    "cover_url": cover_url,
                    "description": book.get("description"),
                    "series_name": series_name,
                    "series_position": series_position,
                    "series_count": series_count,
                    "isbn_13": isbn_13,
                }

            books = [_normalize_book(b) for b in raw_books]
            return jsonify({"provider": provider, "provider_id": str(author_id), "books": books})

        except Exception as e:  # noqa: BLE001 — route-boundary defensive catch; logs the error and degrades to a safe response rather than crashing the request
            logger.error_trace(f"Metadata author books error: {e}")
            return jsonify({"error": str(e)}), 500

    # =========================================================================
    # Monitored Events (unified history / activity log)
    # =========================================================================

    def _parse_optional_int(value: str | None) -> int | None:
        if not value:
            return None
        try:
            return int(value)
        except TypeError, ValueError:
            return None

    @app.route("/api/monitored/events", methods=["GET"])
    def api_list_monitored_events() -> Response | tuple[Response, int]:
        """List monitored events with optional filters and pagination."""
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        entity_id = _parse_optional_int(request.args.get("entity_id"))
        book_provider = request.args.get("book_provider") or None
        book_provider_id = request.args.get("book_provider_id") or None
        event_types_raw = request.args.get("event_types", "")
        event_types = [t.strip() for t in event_types_raw.split(",") if t.strip()] or None
        since = request.args.get("since") or None
        until = request.args.get("until") or None

        try:
            limit = int(request.args.get("limit", 100))
        except TypeError, ValueError:
            limit = 100
        try:
            offset = int(request.args.get("offset", 0))
        except TypeError, ValueError:
            offset = 0

        events, total = monitored_db.list_events(
            user_ids=visible_user_ids,
            entity_id=entity_id,
            book_provider=book_provider,
            book_provider_id=book_provider_id,
            event_types=event_types,
            limit=max(1, min(limit, 500)),
            offset=max(0, offset),
            since=since,
            until=until,
        )
        transform_cached_event_thumbnail_urls(events)
        return jsonify({"events": events, "total": total})

    @app.route("/api/monitored/<int:entity_id>/books/events", methods=["GET"])
    def api_list_monitored_book_events(entity_id: int) -> Response | tuple[Response, int]:
        """List events for a specific book within a monitored entity."""
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        provider = request.args.get("provider", "").strip()
        provider_book_id = request.args.get("provider_book_id", "").strip()

        try:
            limit = int(request.args.get("limit", 50))
        except TypeError, ValueError:
            limit = 50
        try:
            offset = int(request.args.get("offset", 0))
        except TypeError, ValueError:
            offset = 0

        events, total = monitored_db.list_events(
            user_ids=visible_user_ids,
            entity_id=entity_id,
            book_provider=provider or None,
            book_provider_id=provider_book_id or None,
            limit=max(1, min(limit, 500)),
            offset=max(0, offset),
        )
        transform_cached_event_thumbnail_urls(events)
        return jsonify({"events": events, "total": total})

    @app.route("/api/monitored/events/stats", methods=["GET"])
    def api_monitored_event_stats() -> Response | tuple[Response, int]:
        """Return event counts by type for the stats dashboard."""
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        since = request.args.get("since") or None
        raw_counts = monitored_db.count_events_by_type(user_ids=visible_user_ids, since=since)

        downloads = sum(v for k, v in raw_counts.items() if k.startswith("download_"))
        searches = sum(v for k, v in raw_counts.items() if k.startswith("search_"))
        syncs = monitored_db.count_sync_batches(user_ids=visible_user_ids, since=since)
        authors_added = raw_counts.get("author_added", 0)
        authors_removed = raw_counts.get("author_removed", 0)
        failures = sum(
            v for k, v in raw_counts.items() if k in ("download_failed", "author_sync_failed")
        )

        return jsonify(
            {
                "downloads": downloads,
                "searches": searches,
                "syncs": syncs,
                "authors_added": authors_added,
                "authors_removed": authors_removed,
                "failures": failures,
                "raw": raw_counts,
            }
        )

    @app.route("/api/monitored/events", methods=["DELETE"])
    def api_clear_monitored_events() -> Response | tuple[Response, int]:
        """Clear monitored event history."""
        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        before = request.args.get("before") or None
        entity_id = _parse_optional_int(request.args.get("entity_id"))

        deleted = monitored_db.delete_events(
            user_ids=visible_user_ids,
            entity_id=entity_id,
            before=before,
        )
        return jsonify({"deleted": deleted})

    @app.route("/api/monitored/events/export", methods=["GET"])
    def api_export_monitored_events() -> Response | tuple[Response, int]:
        """Export monitored events as CSV."""
        import csv
        import io

        _db_user_id, _global_user_id, visible_user_ids, gate = _resolve_visible_user_ids(
            user_db, resolve_auth_mode=resolve_auth_mode
        )
        if gate is not None:
            return gate

        entity_id = _parse_optional_int(request.args.get("entity_id"))
        event_types_raw = request.args.get("event_types", "")
        event_types = [t.strip() for t in event_types_raw.split(",") if t.strip()] or None
        since = request.args.get("since") or None
        until = request.args.get("until") or None

        rows = monitored_db.export_events(
            user_ids=visible_user_ids,
            entity_id=entity_id,
            event_types=event_types,
            since=since,
            until=until,
        )

        output = io.StringIO()
        if rows:
            writer = csv.DictWriter(output, fieldnames=rows[0].keys())
            writer.writeheader()
            writer.writerows(rows)
        else:
            output.write("No events found.\n")

        from flask import Response as FlaskResponse

        return FlaskResponse(
            output.getvalue(),
            mimetype="text/csv",
            headers={"Content-Disposition": "attachment; filename=monitored_events.csv"},
        )
