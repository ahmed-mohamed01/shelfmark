import { getApiBase } from '../utils/basePath';
import { fetchJSON, ApiResponseError } from './api';

const API_BASE = getApiBase();

// ---------------------------------------------------------------------------
// Metadata author search
// ---------------------------------------------------------------------------

export interface MetadataAuthor {
  provider: string;
  provider_id: string;
  name: string;
  photo_url?: string | null;
  bio?: string | null;
  born_year?: number | string | null;
  source_url?: string | null;
  stats?: {
    books_count?: number | null;
    users_count?: number | null;
    ratings_count?: number | null;
    rating?: number | null;
  } | null;
}

export interface MetadataAuthorSearchResult {
  provider: string;
  query: string;
  page: number;
  totalFound?: number;
  hasMore?: boolean;
  supportsAuthors: boolean;
  authors: MetadataAuthor[];
}

export interface MetadataAuthorDetailsResult {
  provider: string;
  providerId: string;
  supportsAuthors: boolean;
  author: MetadataAuthor | null;
}

export const searchMetadataAuthors = async (
  query: string,
  limit: number = 20,
  page: number = 1,
  contentType: string = 'ebook'
): Promise<MetadataAuthorSearchResult> => {
  const q = query?.trim() || '';
  if (!q) {
    return {
      provider: '',
      query: '',
      page: 1,
      supportsAuthors: false,
      authors: [],
    };
  }

  const params = new URLSearchParams();
  params.set('query', q);
  params.set('limit', String(limit));
  params.set('page', String(page));
  params.set('content_type', contentType);

  const response = await fetchJSON<{
    provider: string;
    query: string;
    page: number;
    total_found?: number;
    has_more?: boolean;
    supports_authors: boolean;
    authors: MetadataAuthor[];
  }>(`${API_BASE}/metadata/authors/search?${params.toString()}`);

  return {
    provider: response.provider,
    query: response.query,
    page: response.page,
    totalFound: response.total_found,
    hasMore: response.has_more,
    supportsAuthors: response.supports_authors,
    authors: response.authors || [],
  };
};

export const getMetadataAuthorInfo = async (provider: string, authorId: string): Promise<MetadataAuthorDetailsResult> => {
  const response = await fetchJSON<{
    provider: string;
    provider_id: string;
    supports_authors: boolean;
    author: MetadataAuthor | null;
  }>(`${API_BASE}/metadata/authors/${encodeURIComponent(provider)}/${encodeURIComponent(authorId)}`);

  return {
    provider: response.provider,
    providerId: response.provider_id,
    supportsAuthors: response.supports_authors,
    author: response.author,
  };
};

// ---------------------------------------------------------------------------
// Monitored entities
// ---------------------------------------------------------------------------

export interface MonitoredEntity {
  id: number;
  user_id: number;
  kind: 'author' | 'book';
  provider: string | null;
  provider_id: string | null;
  name: string;
  enabled: number;
  last_checked_at?: string | null;
  last_error?: string | null;
  created_at?: string;
  updated_at?: string;
  settings?: Record<string, unknown>;
  cached_bio?: string | null;
  cached_source_url?: string | null;
  best_book_cover_url?: string | null;
  visibility?: 'public' | 'private';
}

export interface MonitoredBookRow {
  id: number;
  entity_id: number;
  provider: string | null;
  provider_book_id: string | null;
  title: string;
  authors?: string | null;
  publish_year?: number | null;
  release_date?: string | null;
  description?: string | null;
  cached_tags?: unknown | null;
  isbn_13?: string | null;
  cover_url?: string | null;
  series_name?: string | null;
  series_position?: number | null;
  series_count?: number | null;
  language?: string | null;
  rating?: number | null;
  ratings_count?: number | null;
  readers_count?: number | null;
  monitor_ebook?: number | boolean;
  monitor_audiobook?: number | boolean;
  hidden?: number | boolean;
  saved_monitor_ebook?: number | null;
  saved_monitor_audiobook?: number | null;
  ebook_last_search_status?: string | null;
  audiobook_last_search_status?: string | null;
  ebook_last_search_at?: string | null;
  audiobook_last_search_at?: string | null;
  has_ebook_available?: number | boolean;
  has_audiobook_available?: number | boolean;
  ebook_path?: string | null;
  audiobook_path?: string | null;
  ebook_available_format?: string | null;
  audiobook_available_format?: string | null;
  additional_series?: Array<{ name: string; position?: number; count?: number }>;
  all_series?: string | null;
  no_release_date?: boolean;
  release_date_manual?: number | boolean;
  state: string;
  first_seen_at: string;
}

export const listMonitoredEntities = async (): Promise<MonitoredEntity[]> => {
  return fetchJSON<MonitoredEntity[]>(`${API_BASE}/monitored`);
};

export const createMonitoredEntity = async (payload: {
  kind: 'author' | 'book';
  name: string;
  provider?: string;
  provider_id?: string;
  settings?: Record<string, unknown>;
  visibility?: 'public' | 'private';
}): Promise<MonitoredEntity> => {
  return fetchJSON<MonitoredEntity>(`${API_BASE}/monitored`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
};

export const deleteMonitoredEntity = async (entityId: number): Promise<{ ok: boolean }> => {
  return fetchJSON<{ ok: boolean }>(`${API_BASE}/monitored/${entityId}`, {
    method: 'DELETE',
  });
};

export const getMonitoredEntity = async (entityId: number): Promise<MonitoredEntity> => {
  return fetchJSON<MonitoredEntity>(`${API_BASE}/monitored/${entityId}`);
};

export const patchMonitoredEntity = async (
  entityId: number,
  payload: { settings: Record<string, unknown> }
): Promise<MonitoredEntity> => {
  try {
    return await fetchJSON<MonitoredEntity>(`${API_BASE}/monitored/${entityId}`, {
      method: 'PATCH',
      body: JSON.stringify(payload),
    });
  } catch (e) {
    if (e instanceof ApiResponseError && e.status === 405) {
      return fetchJSON<MonitoredEntity>(`${API_BASE}/monitored/${entityId}`, {
        method: 'PUT',
        body: JSON.stringify(payload),
      });
    }
    throw e;
  }
};

export const syncMonitoredEntity = async (entityId: number): Promise<{ ok: boolean; discovered?: number }> => {
  return fetchJSON<{ ok: boolean; discovered?: number }>(`${API_BASE}/monitored/${entityId}/sync`, {
    method: 'POST',
  });
};

export const syncAllMonitoredEntities = async (): Promise<{ ok: boolean; batch_id?: string; total?: number; already_running?: boolean }> => {
  return fetchJSON<{ ok: boolean; batch_id?: string; total?: number; already_running?: boolean }>(`${API_BASE}/monitored/sync-all`, {
    method: 'POST',
  });
};

export const deleteMonitoredBook = async (entityId: number, provider: string, providerBookId: string): Promise<{ ok: boolean; deleted: boolean }> => {
  return fetchJSON<{ ok: boolean; deleted: boolean }>(`${API_BASE}/monitored/${entityId}/books/${encodeURIComponent(provider)}/${encodeURIComponent(providerBookId)}`, {
    method: 'DELETE',
  });
};

// ---------------------------------------------------------------------------
// Monitored book files and history
// ---------------------------------------------------------------------------

export interface MonitoredBookFileRow {
  id: number;
  entity_id: number;
  provider: string | null;
  provider_book_id: string | null;
  path: string;
  ext?: string | null;
  file_type?: string | null;
  source?: string | null;
  size_bytes?: number | null;
  mtime?: string | null;
  confidence?: number | null;
  match_reason?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface MonitoredAuthorBookSearchRow {
  entity_id: number;
  author_name: string;
  author_provider?: string | null;
  author_provider_id?: string | null;
  author_photo_url?: string | null;
  book_provider?: string | null;
  book_provider_id?: string | null;
  book_title: string;
  book_authors?: string | null;
  publish_year?: number | null;
  cover_url?: string | null;
  series_name?: string | null;
  series_position?: number | null;
  series_count?: number | null;
  has_ebook_available?: number | boolean;
  has_audiobook_available?: number | boolean;
  ebook_path?: string | null;
  audiobook_path?: string | null;
  ebook_available_format?: string | null;
  audiobook_available_format?: string | null;
}

const inFlightMonitoredBookFilesRequests = new Map<number, Promise<{ files: MonitoredBookFileRow[] }>>();
const inFlightMonitoredBooksRequests = new Map<number, Promise<MonitoredBooksResponse>>();

export const searchMonitoredAuthorBooks = async (
  query: string,
  limit: number = 20,
): Promise<{ results: MonitoredAuthorBookSearchRow[] }> => {
  const params = new URLSearchParams();
  params.set('q', query);
  params.set('limit', String(limit));
  return fetchJSON<{ results: MonitoredAuthorBookSearchRow[] }>(`${API_BASE}/monitored/search/books?${params.toString()}`);
};

export const listMonitoredBookFiles = async (entityId: number): Promise<{ files: MonitoredBookFileRow[] }> => {
  const existing = inFlightMonitoredBookFilesRequests.get(entityId);
  if (existing) {
    return existing;
  }

  const request = fetchJSON<{ files: MonitoredBookFileRow[] }>(`${API_BASE}/monitored/${entityId}/files`).finally(() => {
    inFlightMonitoredBookFilesRequests.delete(entityId);
  });

  inFlightMonitoredBookFilesRequests.set(entityId, request);
  return request;
};

export interface MonitoredAutoSearchPrecheckResponse {
  ok: boolean;
  entity_id: number;
  provider: string;
  provider_book_id: string;
  content_type: 'ebook' | 'audiobook';
  skip: boolean;
  reason: 'history_final_path_exists' | 'existing_file' | null;
  detail?: string | null;
}

export const precheckMonitoredAutoSearch = async (
  entityId: number,
  payload: {
    provider: string;
    provider_book_id: string;
    content_type: 'ebook' | 'audiobook';
  },
): Promise<MonitoredAutoSearchPrecheckResponse> => {
  return fetchJSON<MonitoredAutoSearchPrecheckResponse>(`${API_BASE}/monitored/${entityId}/books/auto-search-precheck`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
};

interface RecordMonitoredBookAttemptPayload {
  provider: string;
  provider_book_id: string;
  content_type: 'ebook' | 'audiobook';
  status: 'queued' | 'no_match' | 'below_cutoff' | 'not_released' | 'download_failed' | 'error';
  source?: string;
  source_id?: string;
  release_title?: string;
  match_score?: number;
  error_message?: string;
}

const recordMonitoredBookAttempt = async (
  entityId: number,
  payload: RecordMonitoredBookAttemptPayload,
): Promise<{ ok: boolean }> => {
  return fetchJSON<{ ok: boolean }>(`${API_BASE}/monitored/${entityId}/books/attempt`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
};

export interface RecordAutoSearchAttemptParams {
  monitoredEntityId?: number | null;
  provider?: string | null;
  providerBookId?: string | null;
  contentType: RecordMonitoredBookAttemptPayload['content_type'];
  status: 'no_match' | 'below_cutoff' | 'not_released' | 'error';
  source?: string;
  sourceId?: string;
  releaseTitle?: string;
  matchScore?: number | null;
  errorMessage?: string;
}

export const recordMonitoredAutoSearchAttempt = async (
  params: RecordAutoSearchAttemptParams,
): Promise<void> => {
  const provider = String(params.provider || '').trim();
  const providerBookId = String(params.providerBookId || '').trim();
  if (!params.monitoredEntityId || !provider || !providerBookId) {
    return;
  }
  try {
    await recordMonitoredBookAttempt(params.monitoredEntityId, {
      provider,
      provider_book_id: providerBookId,
      content_type: params.contentType,
      status: params.status,
      source: params.source,
      source_id: params.sourceId,
      release_title: params.releaseTitle,
      match_score: typeof params.matchScore === 'number' ? params.matchScore : undefined,
      error_message: params.errorMessage,
    });
  } catch (err) {
    console.warn(
      'Failed to record monitored auto-search attempt:',
      err instanceof Error ? err.message : String(err)
    );
  }
};

export interface MonitoredFilesScanResult {
  ok: boolean;
  entity_id: number;
  scanned: {
    ebook_author_dir: string | null;
    audiobook_author_dir?: string | null;
  };
  stats: {
    ebook_files_scanned?: number;
    audiobook_folders_scanned?: number;
    matched: number;
    unmatched: number;
  };
  matched: Array<{
    path: string;
    ext?: string;
    file_type?: string;
    size_bytes?: number | null;
    mtime?: string | null;
    candidate?: string;
    match: {
      provider: string | null;
      provider_book_id: string | null;
      title: string | null;
      confidence: number;
      reason: string;
      top_matches?: Array<{
        title: string;
        provider: string | null;
        provider_book_id: string | null;
        score: number;
      }>;
    };
  }>;
  unmatched: Array<{
    path: string;
    ext?: string;
    file_type?: string;
    size_bytes?: number | null;
    mtime?: string | null;
    candidate?: string;
    best_score?: number;
    top_matches?: Array<{
      title: string;
      provider: string | null;
      provider_book_id: string | null;
      score: number;
    }>;
  }>;
  missing_books: Array<{
    provider: string;
    provider_book_id: string;
    title: string | null;
  }>;
  last_scan_at?: string;
}

export const scanMonitoredEntityFiles = async (entityId: number): Promise<MonitoredFilesScanResult> => {
  return fetchJSON<MonitoredFilesScanResult>(`${API_BASE}/monitored/${entityId}/scan-files`, {
    method: 'POST',
  });
};

export interface MonitoredBooksResponse {
  books: MonitoredBookRow[];
  last_checked_at: string | null;
  sync_status: 'idle' | 'syncing' | 'error';
}

export const listMonitoredBooks = async (entityId: number): Promise<MonitoredBooksResponse> => {
  const existing = inFlightMonitoredBooksRequests.get(entityId);
  if (existing) {
    return existing;
  }

  const request = fetchJSON<MonitoredBooksResponse>(`${API_BASE}/monitored/${entityId}/books`).finally(() => {
    inFlightMonitoredBooksRequests.delete(entityId);
  });

  inFlightMonitoredBooksRequests.set(entityId, request);
  return request;
};


export interface MonitoredBookMonitorFlagsPatch {
  provider: string;
  provider_book_id: string;
  monitor_ebook?: boolean;
  monitor_audiobook?: boolean;
  hidden?: boolean;
}

export interface MonitorFlagsResult {
  provider: string;
  provider_book_id: string;
  monitor_ebook: number;
  monitor_audiobook: number;
}

export const updateMonitoredBooksMonitorFlags = async (
  entityId: number,
  updates: MonitoredBookMonitorFlagsPatch[] | MonitoredBookMonitorFlagsPatch,
): Promise<{ ok: boolean; updated: number; results?: MonitorFlagsResult[] }> => {
  return fetchJSON<{ ok: boolean; updated: number; results?: MonitorFlagsResult[] }>(`${API_BASE}/monitored/${entityId}/books/monitor-flags`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });
};

// ---------------------------------------------------------------------------
// File system directory browser
// ---------------------------------------------------------------------------

export interface FsDirectoryEntry {
  name: string;
  path: string;
}

export interface FsListResponse {
  path: string | null;
  parent: string | null;
  directories: FsDirectoryEntry[];
}

export const fsListDirectories = async (path?: string | null): Promise<FsListResponse> => {
  const params = new URLSearchParams();
  if (path) {
    params.set('path', path);
  }
  const url = params.toString() ? `${API_BASE}/fs/list?${params.toString()}` : `${API_BASE}/fs/list`;
  return fetchJSON<FsListResponse>(url);
};

export const fsMkdir = async (parent: string, name: string): Promise<{ path: string }> => {
  return fetchJSON<{ path: string }>(`${API_BASE}/fs/mkdir`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ parent, name }),
  });
};

// ---------------------------------------------------------------------------
// External metadata author books (no DB — live from provider)
// ---------------------------------------------------------------------------

export interface ExternalBookRow {
  provider: string;
  provider_book_id: string;
  title: string;
  authors?: string | null;
  publish_year?: number | null;
  release_date?: string | null;
  cover_url?: string | null;
  description?: string | null;
  series_name?: string | null;
  series_position?: number | null;
  series_count?: number | null;
  isbn_13?: string | null;
}

export const getMetadataAuthorBooks = async (
  provider: string,
  authorId: string,
  limit = 200,
): Promise<{ provider: string; provider_id: string; books: ExternalBookRow[] }> => {
  return fetchJSON<{ provider: string; provider_id: string; books: ExternalBookRow[] }>(
    `${API_BASE}/metadata/authors/${encodeURIComponent(provider)}/${encodeURIComponent(authorId)}/books?limit=${limit}`,
  );
};

// ---------------------------------------------------------------------------
// Batch delete helper (was in monitoredAuthors.ts)
// ---------------------------------------------------------------------------

export interface DeleteMonitoredAuthorsResult {
  successfulIds: number[];
  failedIds: number[];
}

export const deleteMonitoredAuthorsByIds = async (entityIds: number[]): Promise<DeleteMonitoredAuthorsResult> => {
  const uniqueIds = Array.from(new Set(entityIds.filter((id) => Number.isFinite(id) && id > 0)));
  if (uniqueIds.length === 0) {
    return { successfulIds: [], failedIds: [] };
  }

  const results = await Promise.allSettled(uniqueIds.map((id) => deleteMonitoredEntity(id)));
  const successfulIds: number[] = [];
  const failedIds: number[] = [];

  results.forEach((result, index) => {
    if (result.status === 'fulfilled') {
      successfulIds.push(uniqueIds[index]);
    } else {
      failedIds.push(uniqueIds[index]);
    }
  });

  return { successfulIds, failedIds };
};

// ── Release date search ─────────────────────────────────────

export interface ReleaseDateSearchResult {
  asin: string;
  title: string;
  authors: string[];
  release_date: string | null;
  publish_year: number | null;
  cover_url: string | null;
  series_name: string | null;
  source: 'audible' | 'google' | 'hardcover';
}

export const searchReleaseDates = async (
  title: string,
  author?: string,
): Promise<ReleaseDateSearchResult[]> => {
  const params = new URLSearchParams();
  if (title) params.set('title', title);
  if (author) params.set('author', author);
  const resp = await fetchJSON<{ results: ReleaseDateSearchResult[] }>(
    `${API_BASE}/monitored/release-date-search?${params}`,
  );
  return resp.results;
};

export const setBookReleaseDate = async (
  entityId: number,
  provider: string,
  providerBookId: string,
  asin: string,
  releaseDate: string | null,
): Promise<{ ok: boolean }> => {
  return fetchJSON<{ ok: boolean }>(`${API_BASE}/monitored/${entityId}/books/release-date`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      provider,
      provider_book_id: providerBookId,
      asin,
      release_date: releaseDate,
    }),
  });
};

// ---------------------------------------------------------------------------
// Monitored Events (unified history)
// ---------------------------------------------------------------------------

export interface MonitoredEvent {
  id: number;
  event_type: string;
  entity_id: number | null;
  book_provider: string | null;
  book_provider_id: string | null;
  book_title: string | null;
  author_name: string | null;
  content_type: string | null;
  source: string | null;
  source_display_name: string | null;
  status: string | null;
  message: string | null;
  metadata_json: string | null;
  session_id: string | null;
  user_id: number | null;
  created_at: string;
}

export interface MonitoredEventStats {
  downloads: number;
  searches: number;
  syncs: number;
  authors_added: number;
  authors_removed: number;
  failures: number;
  raw: Record<string, number>;
}

export interface ListEventsParams {
  entity_id?: number;
  book_provider?: string;
  book_provider_id?: string;
  event_types?: string;
  since?: string;
  until?: string;
  limit?: number;
  offset?: number;
}

export const listMonitoredEvents = async (
  params: ListEventsParams = {},
): Promise<{ events: MonitoredEvent[]; total: number }> => {
  const query = new URLSearchParams();
  if (params.entity_id != null) query.set('entity_id', String(params.entity_id));
  if (params.book_provider) query.set('book_provider', params.book_provider);
  if (params.book_provider_id) query.set('book_provider_id', params.book_provider_id);
  if (params.event_types) query.set('event_types', params.event_types);
  if (params.since) query.set('since', params.since);
  if (params.until) query.set('until', params.until);
  if (params.limit != null) query.set('limit', String(params.limit));
  if (params.offset != null) query.set('offset', String(params.offset));
  return fetchJSON(`${API_BASE}/monitored/events?${query.toString()}`);
};

export const listMonitoredBookEvents = async (
  entityId: number,
  provider: string,
  providerBookId: string,
  limit: number = 50,
  offset: number = 0,
): Promise<{ events: MonitoredEvent[]; total: number }> => {
  const query = new URLSearchParams({
    provider,
    provider_book_id: providerBookId,
    limit: String(limit),
    offset: String(offset),
  });
  return fetchJSON(`${API_BASE}/monitored/${entityId}/books/events?${query.toString()}`);
};

export const getMonitoredEventStats = async (
  since?: string,
): Promise<MonitoredEventStats> => {
  const query = since ? `?since=${encodeURIComponent(since)}` : '';
  return fetchJSON(`${API_BASE}/monitored/events/stats${query}`);
};

export const deleteMonitoredEvents = async (
  params: { before?: string; entity_id?: number } = {},
): Promise<{ deleted: number }> => {
  const query = new URLSearchParams();
  if (params.before) query.set('before', params.before);
  if (params.entity_id != null) query.set('entity_id', String(params.entity_id));
  return fetchJSON(`${API_BASE}/monitored/events?${query.toString()}`, { method: 'DELETE' });
};

export const exportMonitoredEventsCsv = (
  params: { entity_id?: number; event_types?: string; since?: string; until?: string } = {},
): void => {
  const query = new URLSearchParams();
  if (params.entity_id != null) query.set('entity_id', String(params.entity_id));
  if (params.event_types) query.set('event_types', params.event_types);
  if (params.since) query.set('since', params.since);
  if (params.until) query.set('until', params.until);
  window.open(`${API_BASE}/monitored/events/export?${query.toString()}`, '_blank');
};
