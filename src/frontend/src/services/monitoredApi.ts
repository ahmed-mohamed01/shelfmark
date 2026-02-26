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
  isbn_13?: string | null;
  cover_url?: string | null;
  series_name?: string | null;
  series_position?: number | null;
  series_count?: number | null;
  rating?: number | null;
  ratings_count?: number | null;
  readers_count?: number | null;
  monitor_ebook?: number | boolean;
  monitor_audiobook?: number | boolean;
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
  no_release_date?: boolean;
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
  size_bytes?: number | null;
  mtime?: string | null;
  confidence?: number | null;
  match_reason?: string | null;
  created_at?: string;
  updated_at?: string;
}

export interface MonitoredBookDownloadHistoryRow {
  id: number;
  entity_id: number;
  provider: string;
  provider_book_id: string;
  downloaded_at: string;
  source?: string | null;
  source_display_name?: string | null;
  title_after_rename?: string | null;
  match_score?: number | null;
  downloaded_filename?: string | null;
  final_path?: string | null;
  overwritten_path?: string | null;
  created_at?: string;
}

export interface MonitoredBookAttemptHistoryRow {
  id: number;
  entity_id: number;
  provider: string;
  provider_book_id: string;
  content_type: 'ebook' | 'audiobook';
  attempted_at: string;
  status: string;
  source?: string | null;
  source_id?: string | null;
  release_title?: string | null;
  match_score?: number | null;
  error_message?: string | null;
  created_at?: string;
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

export const listMonitoredBookDownloadHistory = async (
  entityId: number,
  provider: string,
  providerBookId: string,
  limit: number = 50,
): Promise<{ history: MonitoredBookDownloadHistoryRow[]; attempt_history: MonitoredBookAttemptHistoryRow[] }> => {
  const params = new URLSearchParams();
  params.set('provider', provider);
  params.set('provider_book_id', providerBookId);
  params.set('limit', String(limit));
  return fetchJSON<{ history: MonitoredBookDownloadHistoryRow[]; attempt_history: MonitoredBookAttemptHistoryRow[] }>(
    `${API_BASE}/monitored/${entityId}/books/history?${params.toString()}`
  );
};

export interface MonitoredSearchRunResult {
  ok: boolean;
  entity_id: number;
  content_type: 'ebook' | 'audiobook';
  total_candidates: number;
  queued: number;
  no_match: number;
  below_cutoff: number;
  failed: number;
}

export interface RecordMonitoredBookAttemptPayload {
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

export const recordMonitoredBookAttempt = async (
  entityId: number,
  payload: RecordMonitoredBookAttemptPayload,
): Promise<{ ok: boolean }> => {
  return fetchJSON<{ ok: boolean }>(`${API_BASE}/monitored/${entityId}/books/attempt`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
};

export const runMonitoredEntitySearch = async (
  entityId: number,
  contentType: 'ebook' | 'audiobook',
): Promise<MonitoredSearchRunResult> => {
  return fetchJSON<MonitoredSearchRunResult>(`${API_BASE}/monitored/${entityId}/search`, {
    method: 'POST',
    body: JSON.stringify({ content_type: contentType }),
  });
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

export const updateMonitoredBooksSeries = async (
  entityId: number,
  updates: Array<{
    provider: string;
    provider_book_id: string;
    series_name: string;
    series_position?: number | null;
    series_count?: number | null;
  }>,
): Promise<{ ok: boolean; updated: number }> => {
  return fetchJSON<{ ok: boolean; updated: number }>(`${API_BASE}/monitored/${entityId}/books/series`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });
};

export interface MonitoredBookMonitorFlagsPatch {
  provider: string;
  provider_book_id: string;
  monitor_ebook?: boolean;
  monitor_audiobook?: boolean;
}

export const updateMonitoredBooksMonitorFlags = async (
  entityId: number,
  updates: MonitoredBookMonitorFlagsPatch[] | MonitoredBookMonitorFlagsPatch,
): Promise<{ ok: boolean; updated: number }> => {
  return fetchJSON<{ ok: boolean; updated: number }>(`${API_BASE}/monitored/${entityId}/books/monitor-flags`, {
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
