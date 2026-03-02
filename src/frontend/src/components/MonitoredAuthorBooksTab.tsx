import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { Book, ContentType, OpenReleasesOptions, ReleasePrimaryAction, StatusData } from '../types';
import {
  listMonitoredBooks,
  MonitoredBookRow,
  MonitoredBooksResponse,
  syncMonitoredEntity,
  scanMonitoredEntityFiles,
  updateMonitoredBooksMonitorFlags,
} from '../services/monitoredApi';
import { useSocket } from '../contexts/SocketContext';
import { Dropdown } from './Dropdown';
import { BookDetailsModal } from './BookDetailsModal';
import { MonitoredBookCompactTile } from './MonitoredBookCompactTile';
import { MonitoredBookTableRow } from './MonitoredBookTableRow';
import { ViewModeToggle } from './ViewModeToggle';
import { FormatStatusBadge } from './FormatStatusBadge';
import {
  isEnabledMonitoredFlag,
  isMonitoredBookDormantState,
  monitoredBookHasFormatAvailable,
  monitoredBookTracksAudiobook,
  monitoredBookTracksEbook,
  getFormatStatus,
} from '../utils/monitoredBookState';
import type { AuthorModalAuthor } from './AuthorModal';
import { RowThumbnail } from './RowThumbnail';

const BookIcon = ({ className = 'w-4 h-4 sm:w-5 sm:h-5' }: { className?: string }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
  </svg>
);

const AudiobookIcon = ({ className = 'w-4 h-4 sm:w-5 sm:h-5' }: { className?: string }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
  </svg>
);

// ---------------------------------------------------------------------------
// Types and constants
// ---------------------------------------------------------------------------

const SEARCH_DROPDOWN_OPTIONS: Array<{
  contentType: ContentType;
  action: ReleasePrimaryAction;
  label: string;
}> = [
  { contentType: 'ebook', action: 'interactive_search', label: 'eBook — Interactive Search' },
  { contentType: 'ebook', action: 'auto_search_download', label: 'eBook — Auto Search' },
  { contentType: 'audiobook', action: 'interactive_search', label: 'Audiobook — Interactive Search' },
  { contentType: 'audiobook', action: 'auto_search_download', label: 'Audiobook — Auto Search' },
];

type AuthorBooksSort = 'year_desc' | 'year_asc' | 'title_asc' | 'series_asc' | 'series_desc' | 'popular' | 'rating';
type AuthorBooksViewMode = 'table' | 'compact';

const AUTHOR_BOOKS_COMPACT_MIN_WIDTH_MIN = 112;
const AUTHOR_BOOKS_COMPACT_MIN_WIDTH_MAX = 220;
const AUTHOR_BOOKS_COMPACT_MIN_WIDTH_DEFAULT = 150;

type AvailabilityFilterMode =
  | 'all'
  | 'missing_any'
  | 'missing_ebook'
  | 'missing_audiobook'
  | 'available_any'
  | 'ebook_available'
  | 'audiobook_available'
  | 'both_available';

type UpcomingWindowMode = 'any' | '30d' | '90d' | 'this_year';

type AuthorBooksFilters = {
  availability: AvailabilityFilterMode;
  showUpcoming: boolean;
  upcomingWindow: UpcomingWindowMode;
  showNoReleaseDate: boolean;
  seriesKeys: string[];
};

const createDefaultAuthorBooksFilters = (): AuthorBooksFilters => ({
  availability: 'all',
  showUpcoming: false,
  upcomingWindow: 'any',
  showNoReleaseDate: false,
  seriesKeys: [],
});

const AVAILABILITY_FILTER_LABELS: Record<AvailabilityFilterMode, string> = {
  all: 'Availability: Any',
  missing_any: 'Missing',
  missing_ebook: 'Missing eBook',
  missing_audiobook: 'Missing audiobook',
  available_any: 'Available',
  ebook_available: 'eBook available',
  audiobook_available: 'Audiobook available',
  both_available: 'Both formats available',
};

const AVAILABLE_FILTER_MODES: AvailabilityFilterMode[] = [
  'available_any',
  'ebook_available',
  'audiobook_available',
  'both_available',
];

const MISSING_FILTER_MODES: AvailabilityFilterMode[] = [
  'missing_any',
  'missing_ebook',
  'missing_audiobook',
];

const UPCOMING_WINDOW_LABELS: Record<UpcomingWindowMode, string> = {
  any: 'Any time',
  '30d': 'Next 30 days',
  '90d': 'Next 90 days',
  this_year: 'This year',
};

// ---------------------------------------------------------------------------
// Pure helper functions
// ---------------------------------------------------------------------------

const parseFloatFromText = (value: string): number | null => {
  const match = value.match(/-?\d+(?:\.\d+)?/);
  if (!match) return null;
  const parsed = Number.parseFloat(match[0]);
  return Number.isFinite(parsed) ? parsed : null;
};

const parseIntFromText = (value: string): number | null => {
  const digitsOnly = value.replace(/[^\d]/g, '');
  if (!digitsOnly) return null;
  const parsed = Number.parseInt(digitsOnly, 10);
  return Number.isFinite(parsed) ? parsed : null;
};

const MONTHS_BY_NAME: Record<string, number> = {
  january: 0, jan: 0, february: 1, feb: 1, march: 2, mar: 2,
  april: 3, apr: 3, may: 4, june: 5, jun: 5, july: 6, jul: 6,
  august: 7, aug: 7, september: 8, sep: 8, sept: 8,
  october: 9, oct: 9, november: 10, nov: 10, december: 11, dec: 11,
};

const parseReleaseDateValue = (value: string): { date: Date | null; yearOnly: number | null } => {
  const input = value.trim();
  if (!input) return { date: null, yearOnly: null };

  const yearOnlyMatch = input.match(/^(\d{4})$/);
  if (yearOnlyMatch) {
    const year = Number.parseInt(yearOnlyMatch[1], 10);
    return { date: null, yearOnly: Number.isFinite(year) ? year : null };
  }
  const isoYmd = input.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (isoYmd) {
    const d = new Date(Number(isoYmd[1]), Number(isoYmd[2]) - 1, Number(isoYmd[3]));
    if (!Number.isNaN(d.getTime())) return { date: d, yearOnly: null };
  }
  const isoYm = input.match(/^(\d{4})-(\d{1,2})$/);
  if (isoYm) {
    const d = new Date(Number(isoYm[1]), Number(isoYm[2]) - 1, 1);
    if (!Number.isNaN(d.getTime())) return { date: d, yearOnly: null };
  }
  const monthYear = input.match(/^([A-Za-z]{3,9})\s+(\d{4})$/);
  if (monthYear) {
    const month = MONTHS_BY_NAME[monthYear[1].toLowerCase()];
    const year = Number.parseInt(monthYear[2], 10);
    if (month != null && Number.isFinite(year)) return { date: new Date(year, month, 1), yearOnly: null };
  }
  const yearMonth = input.match(/^(\d{4})\s+([A-Za-z]{3,9})$/);
  if (yearMonth) {
    const month = MONTHS_BY_NAME[yearMonth[2].toLowerCase()];
    const year = Number.parseInt(yearMonth[1], 10);
    if (month != null && Number.isFinite(year)) return { date: new Date(year, month, 1), yearOnly: null };
  }
  const fallback = new Date(input);
  if (!Number.isNaN(fallback.getTime())) return { date: fallback, yearOnly: null };

  const embeddedYear = input.match(/\b(19|20)\d{2}\b/);
  if (embeddedYear) {
    const year = Number.parseInt(embeddedYear[0], 10);
    return { date: null, yearOnly: Number.isFinite(year) ? year : null };
  }
  return { date: null, yearOnly: null };
};

const extractReleaseDateCandidate = (book: Book): string | null => {
  const explicit = (book.release_date || '').trim();
  if (explicit) return explicit;
  const fields = Array.isArray(book.display_fields) ? book.display_fields : [];
  for (const field of fields) {
    const label = String(field?.label || '').trim().toLowerCase();
    const value = String(field?.value || '').trim();
    if (!label || !value) continue;
    const isReleaseLabel =
      label.includes('released') || label.includes('release date') ||
      label.includes('publish date') || label.includes('publication date') ||
      label === 'release' || label === 'published' || label === 'publication';
    if (isReleaseLabel) return value;
  }
  return null;
};

const extractBookPopularity = (book: Book): {
  rating: number | null;
  ratingsCount: number | null;
  readersCount: number | null;
} => {
  const fields = Array.isArray(book.display_fields) ? book.display_fields : [];
  let rating: number | null = null;
  let ratingsCount: number | null = null;
  let readersCount: number | null = null;

  for (const field of fields) {
    const icon = (field.icon || '').toLowerCase();
    const label = (field.label || '').toLowerCase();
    const value = String(field.value || '');

    if (rating === null && (icon === 'star' || /rating/.test(label))) {
      const maybeRating = parseFloatFromText(value);
      if (maybeRating !== null && maybeRating <= 10) rating = maybeRating;
      const parenCount = value.match(/\(([^)]+)\)/);
      if (parenCount) {
        const parsedCount = parseIntFromText(parenCount[1]);
        if (parsedCount !== null) ratingsCount = parsedCount;
      }
      continue;
    }
    if (ratingsCount === null && /ratings?/.test(label)) {
      const parsedCount = parseIntFromText(value);
      if (parsedCount !== null) ratingsCount = parsedCount;
      continue;
    }
    if (readersCount === null && (icon === 'users' || /readers?|users?|followers?|people/.test(label))) {
      const parsedReaders = parseIntFromText(value);
      if (parsedReaders !== null) readersCount = parsedReaders;
    }
  }
  return { rating, ratingsCount, readersCount };
};

const withMonitoredAvailability = (book: Book, rows: MonitoredBookRow[]): Book => {
  const provider = (book.provider || '').trim();
  const providerId = (book.provider_id || '').trim();
  if (!provider || !providerId) return book;
  const row = rows.find((r) => r.provider === provider && r.provider_book_id === providerId);
  if (!row) return book;
  return {
    ...book,
    has_ebook_available: isEnabledMonitoredFlag(row.has_ebook_available),
    has_audiobook_available: isEnabledMonitoredFlag(row.has_audiobook_available),
    ebook_path: row.ebook_path || undefined,
    audiobook_path: row.audiobook_path || undefined,
    ebook_available_format: row.ebook_available_format || undefined,
    audiobook_available_format: row.audiobook_available_format || undefined,
    ebook_last_search_status: row.ebook_last_search_status ?? undefined,
    audiobook_last_search_status: row.audiobook_last_search_status ?? undefined,
  };
};

// ---------------------------------------------------------------------------
// Pure utilities (no component state — defined at module level)
// ---------------------------------------------------------------------------

const normalizeStatusKeyPart = (value: string | null | undefined): string => {
  const s = String(value || '').trim().toLowerCase();
  if (!s) return '';
  return s.replace(/[^a-z0-9]+/g, ' ').replace(/\s+/g, ' ').trim();
};

const buildBookStatusKeys = (b: Book): string[] => {
  const keys: string[] = [];
  const prov = b.provider || '';
  const bid = b.provider_id || '';
  const providerBookId = String((b as Book & { provider_book_id?: string }).provider_book_id || '');
  if (prov && bid) keys.push(`p:${prov}:${bid}`);
  if (prov && providerBookId) keys.push(`p:${prov}:${providerBookId}`);
  if (b.id !== null && b.id !== undefined) {
    const sid = String(b.id);
    keys.push(`id:${sid}`);
    keys.push(`rk:${normalizeStatusKeyPart(sid)}`);
  }
  if (bid) keys.push(`rk:${normalizeStatusKeyPart(bid)}`);
  if (providerBookId) keys.push(`rk:${normalizeStatusKeyPart(providerBookId)}`);
  const t = normalizeStatusKeyPart(b.title);
  const a = normalizeStatusKeyPart(b.author);
  const st = normalizeStatusKeyPart(b.search_title);
  const sa = normalizeStatusKeyPart(b.search_author);
  const firstAuthor = normalizeStatusKeyPart(Array.isArray(b.authors) ? b.authors[0] : '');
  if (t && a) keys.push(`ta:${t}|${a}`);
  if (st && sa) keys.push(`ta:${st}|${sa}`);
  if (t && firstAuthor) keys.push(`ta:${t}|${firstAuthor}`);
  if (t) keys.push(`t:${t}`);
  if (st) keys.push(`t:${st}`);
  return keys;
};

// ---------------------------------------------------------------------------
// Props
// ---------------------------------------------------------------------------

export interface MonitoredAuthorBooksTabProps {
  author: AuthorModalAuthor | null;
  monitoredEntityId?: number | null;
  status?: StatusData;
  isPageMode: boolean;
  activeBooksQuery: string;
  updateBooksQuery: (value: string) => void;
  initialBookProvider?: string | null;
  initialBookProviderId?: string | null;
  onGetReleases?: (
    book: Book,
    contentType: ContentType,
    monitoredEntityId?: number | null,
    actionOverride?: ReleasePrimaryAction,
    options?: OpenReleasesOptions,
  ) => Promise<void>;
  defaultReleaseContentType?: ContentType;
  defaultReleaseActionEbook?: ReleasePrimaryAction;
  defaultReleaseActionAudiobook?: ReleasePrimaryAction;
  renderEmbeddedSearch?: (book: Book, contentType: ContentType) => ReactNode;
  onFallbackPhotoChange?: (url: string | null) => void;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const MonitoredAuthorBooksTab = ({
  author,
  monitoredEntityId,
  status,
  isPageMode,
  activeBooksQuery,
  updateBooksQuery,
  initialBookProvider,
  initialBookProviderId,
  onGetReleases,
  defaultReleaseContentType = 'ebook',
  defaultReleaseActionEbook = 'interactive_search',
  defaultReleaseActionAudiobook = 'interactive_search',
  renderEmbeddedSearch,
  onFallbackPhotoChange,
}: MonitoredAuthorBooksTabProps) => {
  // --- state ---
  const [booksSort, setBooksSort] = useState<AuthorBooksSort>(() => {
    const saved = localStorage.getItem('authorBooksSort');
    return saved === 'year_desc' || saved === 'year_asc' || saved === 'title_asc' ||
      saved === 'series_asc' || saved === 'series_desc' || saved === 'popular' || saved === 'rating'
      ? saved : 'series_asc';
  });
  const [booksViewMode, setBooksViewMode] = useState<AuthorBooksViewMode>(() => {
    const saved = localStorage.getItem('authorBooksViewMode');
    if (saved === 'compact' || saved === 'card') return 'compact';
    return 'table';
  });
  const [showMultipleSeries, setShowMultipleSeries] = useState<boolean>(() => {
    return localStorage.getItem('authorBooksShowMultipleSeries') === 'true';
  });
  const [booksCompactMinWidth, setBooksCompactMinWidth] = useState<number>(() => {
    const raw = localStorage.getItem('authorBooksCompactMinWidth');
    const parsed = raw ? Number(raw) : Number.NaN;
    if (!Number.isFinite(parsed)) return AUTHOR_BOOKS_COMPACT_MIN_WIDTH_DEFAULT;
    return Math.max(AUTHOR_BOOKS_COMPACT_MIN_WIDTH_MIN, Math.min(AUTHOR_BOOKS_COMPACT_MIN_WIDTH_MAX, parsed));
  });
  const [booksFilters, setBooksFilters] = useState<AuthorBooksFilters>(() => createDefaultAuthorBooksFilters());
  const [isSeriesFilterMenuOpen, setIsSeriesFilterMenuOpen] = useState(false);
  const [closeSeriesFilterOnSelect, setCloseSeriesFilterOnSelect] = useState(false);
  const [isAvailabilityFilterMenuOpen, setIsAvailabilityFilterMenuOpen] = useState(false);
  const [isMissingFilterMenuOpen, setIsMissingFilterMenuOpen] = useState(false);
  const [books, setBooks] = useState<Book[]>([]);
  const [isLoadingBooks, setIsLoadingBooks] = useState(false);
  const [booksError, setBooksError] = useState<string | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const [selectedBookIds, setSelectedBookIds] = useState<Record<string, boolean>>({});
  const [bulkDownloadRunningByType, setBulkDownloadRunningByType] = useState<Record<ContentType, boolean>>({
    ebook: false, audiobook: false,
  });
  const [monitorSearchBusyByType, setMonitorSearchBusyByType] = useState<Record<ContentType, boolean>>({
    ebook: false, audiobook: false,
  });
  const [monitorSearchSummary, setMonitorSearchSummary] = useState<string | null>(null);
  const [monitoredBookRows, setMonitoredBookRows] = useState<MonitoredBookRow[]>([]);
  const [autoRefreshBusy, setAutoRefreshBusy] = useState(false);
  const [syncStatus, setSyncStatus] = useState<'idle' | 'syncing' | 'error'>('idle');
  const [syncPhase, setSyncPhase] = useState<string | null>(null);
  const [activeBookDetails, setActiveBookDetails] = useState<Book | null>(null);
  const [hasAppliedInitialBookSelection, setHasAppliedInitialBookSelection] = useState(false);
  const [isBooksToolbarPinned, setIsBooksToolbarPinned] = useState(false);

  const booksToolbarRef = useRef<HTMLDivElement | null>(null);
  const pendingScrollToSeriesRef = useRef<string | null>(null);
  const lastAutoRefreshSignatureRef = useRef({ value: '' });

  const { socket } = useSocket();

  // --- callbacks ---
  const resolvePrimaryActionForContentType = useCallback((contentType: ContentType): ReleasePrimaryAction => {
    return contentType === 'audiobook' ? defaultReleaseActionAudiobook : defaultReleaseActionEbook;
  }, [defaultReleaseActionAudiobook, defaultReleaseActionEbook]);

  const triggerReleaseSearch = useCallback(async (
    book: Book,
    contentType: ContentType,
    actionOverride?: ReleasePrimaryAction,
    options?: OpenReleasesOptions,
  ) => {
    if (!onGetReleases) return;
    await onGetReleases(book, contentType, monitoredEntityId, actionOverride, options);
  }, [onGetReleases, monitoredEntityId]);

  // --- effects ---

  // Toolbar pin (page mode)
  useEffect(() => {
    if (!isPageMode) { setIsBooksToolbarPinned(false); return; }
    const stickyTop = 76;
    const updatePinned = () => {
      const top = booksToolbarRef.current?.getBoundingClientRect().top;
      if (typeof top !== 'number') return;
      setIsBooksToolbarPinned(top <= stickyTop + 0.5);
    };
    updatePinned();
    window.addEventListener('scroll', updatePinned, { passive: true });
    window.addEventListener('resize', updatePinned);
    return () => {
      window.removeEventListener('scroll', updatePinned);
      window.removeEventListener('resize', updatePinned);
    };
  }, [isPageMode]);

  // Reset initial book selection flag on author/initial-provider change
  useEffect(() => {
    setHasAppliedInitialBookSelection(false);
  }, [author?.name, initialBookProvider, initialBookProviderId]);

  // Apply initial book selection
  useEffect(() => {
    if (hasAppliedInitialBookSelection) return;
    const provider = (initialBookProvider || '').trim();
    const providerId = (initialBookProviderId || '').trim();
    if (!provider || !providerId) { setHasAppliedInitialBookSelection(true); return; }
    if (isLoadingBooks) return;
    const match = books.find((book) => (book.provider || '') === provider && (book.provider_id || '') === providerId);
    if (match) setActiveBookDetails(withMonitoredAvailability(match, monitoredBookRows));
    setHasAppliedInitialBookSelection(true);
  }, [books, hasAppliedInitialBookSelection, initialBookProvider, initialBookProviderId, isLoadingBooks, monitoredBookRows]);

  // handleRefreshAndScan
  const handleRefreshAndScan = useCallback(async () => {
    if (!author) return;
    if (!monitoredEntityId) { setRefreshKey((k) => k + 1); return; }
    setIsRefreshing(true);
    setSyncStatus('syncing');
    try {
      setRefreshKey((k) => k + 1);
      await syncMonitoredEntity(monitoredEntityId);
      await scanMonitoredEntityFiles(monitoredEntityId);
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Refresh & scan failed';
      console.warn('MonitoredAuthorBooksTab: Refresh & scan failed', message);
    } finally {
      try {
        const booksResp = await listMonitoredBooks(monitoredEntityId);
        setMonitoredBookRows(booksResp.books || []);
      } catch (e) {
        console.warn('MonitoredAuthorBooksTab: failed to reload after refresh', e);
      }
      setIsRefreshing(false);
    }
  }, [monitoredEntityId]);

  // handleRunMonitoredSearch
  const handleRunMonitoredSearch = useCallback(async (contentType: ContentType) => {
    if (!monitoredEntityId || !onGetReleases) return;
    setMonitorSearchBusyByType((prev) => ({ ...prev, [contentType]: true }));
    setMonitorSearchSummary(null);
    try {
      await scanMonitoredEntityFiles(monitoredEntityId);
      const booksResp = await listMonitoredBooks(monitoredEntityId);
      setMonitoredBookRows(booksResp.books || []);
    } catch (e) {
      console.warn('MonitoredAuthorBooksTab: auto refresh after download complete failed', e);
    } finally {
      setAutoRefreshBusy(false);
    }
  }, [monitoredEntityId, onGetReleases]);

  // Auto-refresh after download completion
  useEffect(() => {
    if (!monitoredEntityId || !status || autoRefreshBusy) return;
    const authorBookKeys = new Set(books.flatMap((b) => buildBookStatusKeys(b)));
    if (authorBookKeys.size === 0) return;
    const completedEntries = status.complete ? Object.entries(status.complete) : [];
    const relevantCompleted = completedEntries.filter(([, b]) => {
      const keys = buildBookStatusKeys(b);
      return keys.some((k) => authorBookKeys.has(k));
    });
    if (relevantCompleted.length === 0) return;
    const completionSignature = relevantCompleted
      .map(([recordKey, b]) => {
        const keyPart = buildBookStatusKeys(b).sort().join(',');
        const ts = typeof b.added_time === 'number' ? b.added_time : 0;
        return `${recordKey}:${ts}:${keyPart}`;
      })
      .sort().join('|');
    if (!completionSignature || completionSignature === lastAutoRefreshSignatureRef.current.value) return;
    lastAutoRefreshSignatureRef.current.value = completionSignature;
    setAutoRefreshBusy(true);
    void (async () => {
      try {
        await scanMonitoredEntityFiles(monitoredEntityId);
        const booksResp = await listMonitoredBooks(monitoredEntityId);
        setMonitoredBookRows(booksResp.books || []);
      } catch (e) {
        console.warn('MonitoredAuthorBooksTab: auto refresh after download complete failed', e);
      } finally {
        setAutoRefreshBusy(false);
      }
    })();
  }, [monitoredEntityId, status, autoRefreshBusy, books]);

  // Update active book details when monitored rows change
  useEffect(() => {
    setActiveBookDetails((prev) => (prev ? withMonitoredAvailability(prev, monitoredBookRows) : prev));
  }, [monitoredBookRows]);

  // localStorage persistence
  useEffect(() => { try { localStorage.setItem('authorBooksSort', booksSort); } catch { /* ignore */ } }, [booksSort]);
  useEffect(() => { try { localStorage.setItem('authorBooksViewMode', booksViewMode); } catch { /* ignore */ } }, [booksViewMode]);
  useEffect(() => { try { localStorage.setItem('authorBooksShowMultipleSeries', showMultipleSeries ? 'true' : 'false'); } catch { /* ignore */ } }, [showMultipleSeries]);
  useEffect(() => { try { localStorage.setItem('authorBooksCompactMinWidth', String(booksCompactMinWidth)); } catch { /* ignore */ } }, [booksCompactMinWidth]);

  // Reset books state when author clears
  useEffect(() => {
    if (!author) {
      setBooks([]);
      setBooksError(null);
      setIsLoadingBooks(false);
    }
  }, [author]);

  // Load books (from monitored DB + provider)
  useEffect(() => {
    if (!author) return;
    let isCancelled = false;

    const monitoredBookToBook = (row: MonitoredBookRow): Book => ({
      id: `${row.provider || 'unknown'}:${row.provider_book_id || row.id}`,
      title: row.title,
      author: row.authors || author?.name || '',
      year: row.publish_year != null ? String(row.publish_year) : undefined,
      release_date: row.release_date || undefined,
      preview: row.cover_url || undefined,
      isbn_13: row.isbn_13 || undefined,
      provider: row.provider || undefined,
      provider_id: row.provider_book_id || undefined,
      series_name: (row.series_name || '').trim() || undefined,
      series_position: row.series_position != null ? row.series_position : undefined,
      series_count: row.series_count != null ? row.series_count : undefined,
      additional_series: (() => {
        const primaryName = (row.series_name || '').trim();
        const raw = row.additional_series || (() => {
          if (!row.all_series || typeof row.all_series !== 'string') return undefined;
          try { const p = JSON.parse(row.all_series); return Array.isArray(p) ? p as Array<{ name: string; position?: number; count?: number }> : undefined; } catch { return undefined; }
        })();
        if (!raw) return undefined;
        // Exclude the primary series — the modal renders it separately via series_name
        const filtered = raw.filter((s) => (s.name || '').trim() !== primaryName);
        return filtered.length > 0 ? filtered : undefined;
      })(),
      language: (row.language || '').trim() || undefined,
      description: (typeof row.description === 'string' && row.description.trim()) ? row.description.trim() : undefined,
      display_fields: [
        { label: 'Release Date', value: (typeof row.release_date === 'string' && row.release_date.trim()) ? row.release_date.trim() : 'TBA' },
        ...(typeof row.rating === 'number'
          ? [{ label: 'Rating', value: `${row.rating.toFixed(1)}${typeof row.ratings_count === 'number' ? ` (${row.ratings_count.toLocaleString()})` : ''}`, icon: 'star' }]
          : []),
        ...(typeof row.readers_count === 'number'
          ? [{ label: 'Readers', value: row.readers_count.toLocaleString(), icon: 'users' }]
          : []),
      ],
    });

    const loadBooks = async () => {
      setBooks([]);
      setBooksError(null);
      setIsLoadingBooks(true);
      setIsRefreshing(false);
      try {
        if (!monitoredEntityId) return;
        const resp: MonitoredBooksResponse = await listMonitoredBooks(monitoredEntityId);
        if (isCancelled) return;
        setSyncStatus(resp.sync_status ?? 'idle');
        setMonitoredBookRows(resp.books);
        setBooks(resp.books.map(monitoredBookToBook));
      } catch (e) {
        if (isCancelled) return;
        const message = e instanceof Error ? e.message : 'Failed to load books';
        setBooksError(message);
      } finally {
        if (!isCancelled) { setIsLoadingBooks(false); setIsRefreshing(false); }
      }
    };

    void loadBooks();
    return () => { isCancelled = true; };
  }, [author, monitoredEntityId, refreshKey]);

  // WebSocket: listen for background sync events for this entity
  useEffect(() => {
    if (!socket || !monitoredEntityId) return;
    const handleSyncComplete = (data: { entity_id: number; books_count: number }) => {
      if (data.entity_id !== monitoredEntityId) return;
      setSyncStatus('idle');
      setSyncPhase(null);
      setRefreshKey((k) => k + 1);
    };
    const handleSyncError = (data: { entity_id: number; error: string }) => {
      if (data.entity_id !== monitoredEntityId) return;
      setSyncStatus('error');
      setSyncPhase(null);
    };
    const handleSyncProgress = (data: { entity_id: number; phase: string }) => {
      if (data.entity_id !== monitoredEntityId) return;
      setSyncPhase(data.phase);
    };
    socket.on('monitored_sync_complete', handleSyncComplete);
    socket.on('monitored_sync_error', handleSyncError);
    socket.on('monitored_sync_progress', handleSyncProgress);
    return () => {
      socket.off('monitored_sync_complete', handleSyncComplete);
      socket.off('monitored_sync_error', handleSyncError);
      socket.off('monitored_sync_progress', handleSyncProgress);
    };
  }, [socket, monitoredEntityId]);

  // Prune stale selections when books change
  useEffect(() => {
    setSelectedBookIds((prev) => {
      const next: Record<string, boolean> = {};
      let changed = false;
      for (const book of books) { if (prev[book.id]) next[book.id] = true; }
      const prevKeys = Object.keys(prev);
      const nextKeys = Object.keys(next);
      if (prevKeys.length !== nextKeys.length) { changed = true; } else {
        for (const key of prevKeys) { if (!next[key]) { changed = true; break; } }
      }
      return changed ? next : prev;
    });
  }, [books]);

  // --- computed values ---

  const groupedBooks = useMemo(() => {
    const parseYear = (value?: string) => {
      const n = value ? Number.parseInt(value, 10) : Number.NaN;
      return Number.isFinite(n) ? n : null;
    };
    // Returns a book's position within a specific series group, falling back to
    // additional_series when the group key differs from the book's primary series.
    const getSeriesPos = (book: Book, groupKey: string): number => {
      const primaryKey = (book.series_name || '').trim();
      if (!groupKey || groupKey === '__standalone__' || groupKey === primaryKey) {
        return book.series_position ?? Number.POSITIVE_INFINITY;
      }
      const match = book.additional_series?.find((a) => (a.name || '').trim() === groupKey);
      return match?.position ?? Number.POSITIVE_INFINITY;
    };
    const withinGroupSort = (a: Book, b: Book) => {
      if (booksSort === 'title_asc') return (a.title || '').localeCompare(b.title || '');
      if (booksSort === 'popular' || booksSort === 'rating') {
        const aP = extractBookPopularity(a);
        const bP = extractBookPopularity(b);
        if (booksSort === 'popular') {
          const aR = aP.readersCount ?? -1; const bR = bP.readersCount ?? -1;
          if (bR !== aR) return bR - aR;
          const aRc = aP.ratingsCount ?? -1; const bRc = bP.ratingsCount ?? -1;
          if (bRc !== aRc) return bRc - aRc;
          const aRt = aP.rating ?? -1; const bRt = bP.rating ?? -1;
          if (bRt !== aRt) return bRt - aRt;
          return (a.title || '').localeCompare(b.title || '');
        }
        const aRt = aP.rating ?? -1; const bRt = bP.rating ?? -1;
        if (bRt !== aRt) return bRt - aRt;
        const aRc = aP.ratingsCount ?? -1; const bRc = bP.ratingsCount ?? -1;
        if (bRc !== aRc) return bRc - aRc;
        const aR = aP.readersCount ?? -1; const bR = bP.readersCount ?? -1;
        if (bR !== aR) return bR - aR;
        return (a.title || '').localeCompare(b.title || '');
      }
      if (booksSort === 'series_asc' || booksSort === 'series_desc') {
        const aPos = a.series_position ?? Number.POSITIVE_INFINITY;
        const bPos = b.series_position ?? Number.POSITIVE_INFINITY;
        if (aPos !== bPos) return aPos - bPos;
        const ay = parseYear(a.year); const by = parseYear(b.year);
        if (ay != null && by != null && ay !== by) return ay - by;
        return (a.title || '').localeCompare(b.title || '');
      }
      const ay = parseYear(a.year); const by = parseYear(b.year);
      if (ay == null && by == null) return (a.title || '').localeCompare(b.title || '');
      if (ay == null) return 1;
      if (by == null) return -1;
      if (booksSort === 'year_asc') return ay - by;
      return by - ay;
    };
    // Series-aware sort: uses getSeriesPos so position reflects the current group.
    const makeSeriesGroupSort = (groupKey: string) => (a: Book, b: Book) => {
      if (booksSort === 'series_asc' || booksSort === 'series_desc') {
        const aPos = getSeriesPos(a, groupKey);
        const bPos = getSeriesPos(b, groupKey);
        if (aPos !== bPos) return aPos - bPos;
        const ay = parseYear(a.year); const by = parseYear(b.year);
        if (ay != null && by != null && ay !== by) return ay - by;
        return (a.title || '').localeCompare(b.title || '');
      }
      return withinGroupSort(a, b);
    };

    if (booksSort === 'year_desc' || booksSort === 'year_asc') {
      const yearMap = new Map<string, Book[]>();
      for (const b of books) {
        const year = parseYear(b.year);
        const key = year != null ? String(year) : '__unknown__';
        const list = yearMap.get(key);
        if (list) list.push(b); else yearMap.set(key, [b]);
      }
      const unknown = yearMap.get('__unknown__') ?? [];
      yearMap.delete('__unknown__');
      const years = Array.from(yearMap.keys()).sort((a, b) => booksSort === 'year_asc' ? Number(a) - Number(b) : Number(b) - Number(a));
      const groups = years.map((year) => {
        const yearBooks = [...(yearMap.get(year) ?? [])];
        yearBooks.sort(withinGroupSort);
        return { key: year, title: year, books: yearBooks };
      });
      if (unknown.length > 0) {
        unknown.sort(withinGroupSort);
        groups.push({ key: '__unknown__', title: 'Unknown year', books: unknown });
      }
      return groups;
    }

    // Group by series
    const seriesMap = new Map<string, Book[]>();
    for (const book of books) {
      const primarySeriesKey = (book.series_name || '').trim() || '__standalone__';
      const list = seriesMap.get(primarySeriesKey);
      if (list) list.push(book); else seriesMap.set(primarySeriesKey, [book]);
      if (showMultipleSeries && book.additional_series) {
        for (const alt of book.additional_series) {
          const altKey = (alt.name || '').trim();
          if (!altKey || altKey === primarySeriesKey) continue;
          const altList = seriesMap.get(altKey);
          if (altList) altList.push(book); else seriesMap.set(altKey, [book]);
        }
      }
    }

    const standalone = seriesMap.get('__standalone__') ?? [];
    seriesMap.delete('__standalone__');

    const seriesKeys = Array.from(seriesMap.keys());
    const seriesSorted = (booksSort === 'series_desc')
      ? seriesKeys.sort((a, b) => b.localeCompare(a))
      : seriesKeys.sort((a, b) => a.localeCompare(b));

    const groups = seriesSorted.map((key) => {
      const gb = [...(seriesMap.get(key) ?? [])];
      gb.sort(makeSeriesGroupSort(key));
      return { key, title: key, books: gb };
    });

    if (standalone.length > 0) {
      standalone.sort(withinGroupSort);
      groups.push({ key: '__standalone__', title: 'Standalone', books: standalone });
    }
    return groups;
  }, [books, booksSort, showMultipleSeries]);

  const seriesFilterOptions = useMemo(() => {
    return groupedBooks
      .filter((group) => group.key !== '__standalone__')
      .map((group) => ({ key: group.key, title: group.title, count: group.books.length }));
  }, [groupedBooks]);

  // Prune invalid series filter keys
  useEffect(() => {
    const allowed = new Set(seriesFilterOptions.map((option) => option.key));
    setBooksFilters((prev) => {
      if (prev.seriesKeys.length === 0) return prev;
      const nextSeriesKeys = prev.seriesKeys.filter((key) => allowed.has(key));
      if (nextSeriesKeys.length === prev.seriesKeys.length) return prev;
      return { ...prev, seriesKeys: nextSeriesKeys };
    });
  }, [seriesFilterOptions]);

  const monitoredBookRowByKey = useMemo(() => {
    const map = new Map<string, MonitoredBookRow>();
    for (const row of monitoredBookRows) {
      const provider = (row.provider || '').trim();
      const providerId = (row.provider_book_id || '').trim();
      if (!provider || !providerId) continue;
      map.set(`${provider}:${providerId}`, row);
    }
    return map;
  }, [monitoredBookRows]);

  const getMonitoredAvailabilityForBook = useCallback((book: Book): {
    hasEbook: boolean; hasAudiobook: boolean; ebookFormat?: string; audiobookFormat?: string;
  } => {
    const provider = (book.provider || '').trim();
    const providerId = (book.provider_id || '').trim();
    const key = provider && providerId ? `${provider}:${providerId}` : '';
    const row = key ? monitoredBookRowByKey.get(key) : undefined;
    const source = row || book;
    const ebookFormatRaw = row?.ebook_available_format ?? book.ebook_available_format;
    const audiobookFormatRaw = row?.audiobook_available_format ?? book.audiobook_available_format;
    return {
      hasEbook: monitoredBookHasFormatAvailable(source, 'ebook'),
      hasAudiobook: monitoredBookHasFormatAvailable(source, 'audiobook'),
      ebookFormat: typeof ebookFormatRaw === 'string' && ebookFormatRaw.trim() ? ebookFormatRaw.trim().toLowerCase() : undefined,
      audiobookFormat: typeof audiobookFormatRaw === 'string' && audiobookFormatRaw.trim() ? audiobookFormatRaw.trim().toLowerCase() : undefined,
    };
  }, [monitoredBookRowByKey]);

  const filteredGroupedBooks = useMemo(() => {
    const query = activeBooksQuery.trim().toLowerCase();
    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const currentYear = todayStart.getFullYear();
    const selectedSeries = new Set(booksFilters.seriesKeys);

    const isDormantBookInGroup = (book: Book): boolean => {
      const provider = (book.provider || '').trim();
      const providerId = (book.provider_id || '').trim();
      if (!provider || !providerId) return false;

      const row = monitoredBookRowByKey.get(`${provider}:${providerId}`);
      if (!row) return false;

      if (monitoredBookTracksEbook(row) || monitoredBookTracksAudiobook(row)) {
        return false;
      }

      const availability = getMonitoredAvailabilityForBook(book);
      return !availability.hasEbook && !availability.hasAudiobook;
    };

    const hasNoReleaseDate = (book: Book): boolean => {
      const releaseDateRaw = extractReleaseDateCandidate(book);
      if (!releaseDateRaw) return true;
      const parsed = parseReleaseDateValue(releaseDateRaw);
      return parsed.date === null && parsed.yearOnly === null;
    };

    const passesUpcomingFilter = (book: Book): boolean => {
      if (!booksFilters.showUpcoming) return true;
      const releaseDateRaw = extractReleaseDateCandidate(book);
      if (releaseDateRaw) {
        const parsed = parseReleaseDateValue(releaseDateRaw);
        if (parsed.date) {
          const millis = parsed.date.getTime();
          if (millis <= todayStart.getTime()) return false;
          if (booksFilters.upcomingWindow === 'any') return true;
          if (booksFilters.upcomingWindow === '30d') {
            const end = new Date(todayStart);
            end.setDate(end.getDate() + 30);
            return millis <= end.getTime();
          }
          if (booksFilters.upcomingWindow === '90d') {
            const end = new Date(todayStart);
            end.setDate(end.getDate() + 90);
            return millis <= end.getTime();
          }
          return parsed.date.getFullYear() === currentYear;
        }
        if (parsed.yearOnly != null) {
          if (parsed.yearOnly < currentYear) return false;
          if (booksFilters.upcomingWindow === 'this_year') return parsed.yearOnly === currentYear;
          if (booksFilters.upcomingWindow === '30d' || booksFilters.upcomingWindow === '90d') return false;
          return true;
        }
      }

      const fallbackYear = book.year ? Number.parseInt(book.year, 10) : Number.NaN;
      if (!Number.isFinite(fallbackYear)) return false;
      if (fallbackYear < currentYear) return false;
      if (booksFilters.upcomingWindow === 'this_year') return fallbackYear === currentYear;
      if (booksFilters.upcomingWindow === '30d' || booksFilters.upcomingWindow === '90d') return false;
      return true;
    };

    const passesAvailabilityFilter = (book: Book): boolean => {
      if (booksFilters.availability === 'all') return true;
      const availability = getMonitoredAvailabilityForBook(book);
      const hasEbook = availability.hasEbook;
      const hasAudiobook = availability.hasAudiobook;
      if (booksFilters.availability === 'available_any') return hasEbook || hasAudiobook;
      if (booksFilters.availability === 'ebook_available') return hasEbook;
      if (booksFilters.availability === 'audiobook_available') return hasAudiobook;
      if (booksFilters.availability === 'both_available') return hasEbook && hasAudiobook;
      if (booksFilters.availability === 'missing_any') return !hasEbook || !hasAudiobook;
      if (booksFilters.availability === 'missing_ebook') return !hasEbook;
      if (booksFilters.availability === 'missing_audiobook') return !hasAudiobook;
      return true;
    };

    const passesQueryFilter = (book: Book): boolean => {
      if (!query) return true;
      const title = (book.title || '').toLowerCase();
      const author = (book.author || '').toLowerCase();
      const series = (book.series_name || '').toLowerCase();
      return title.includes(query) || author.includes(query) || series.includes(query);
    };

    const groups = groupedBooks
      .filter((group) => selectedSeries.size === 0 || selectedSeries.has(group.key))
      .map((group) => {
        const nextBooks = group.books.filter((book) => {
          if (!passesQueryFilter(book)) return false;
          if (!passesAvailabilityFilter(book)) return false;
          if (booksFilters.showNoReleaseDate && !hasNoReleaseDate(book)) return false;
          if (!passesUpcomingFilter(book)) return false;
          return true;
        });
        const isDormantGroup = nextBooks.length > 0 && nextBooks.every((book) => isDormantBookInGroup(book));
        return { ...group, books: nextBooks, isDormantGroup };
      })
      .filter((group) => group.books.length > 0);

    return [...groups].sort((a, b) => {
      const ad = Boolean((a as any).isDormantGroup);
      const bd = Boolean((b as any).isDormantGroup);
      if (ad === bd) return 0;
      return ad ? 1 : -1;
    });
  }, [
    activeBooksQuery,
    booksFilters,
    getMonitoredAvailabilityForBook,
    groupedBooks,
    monitoredBookRowByKey,
  ]);

  useEffect(() => {
    if (filteredGroupedBooks.length === 0) return;
    setCollapsedGroups((prev) => {
      let changed = false;
      const next = { ...prev };
      for (const group of filteredGroupedBooks as any[]) {
        if (next[group.key] !== undefined) continue;
        if (group.isDormantGroup) {
          next[group.key] = true;
          changed = true;
        }
      }
      return changed ? next : prev;
    });
  }, [filteredGroupedBooks]);

  const activeFiltersCount = useMemo(() => {
    let count = 0;
    if (booksFilters.availability !== 'all') count += 1;
    if (booksFilters.showUpcoming) count += 1;
    if (booksFilters.showNoReleaseDate) count += 1;
    if (booksFilters.seriesKeys.length > 0) count += 1;
    return count;
  }, [booksFilters]);

  const singleActiveFilterLabel = useMemo(() => {
    const labels: string[] = [];
    if (booksFilters.availability !== 'all') labels.push(AVAILABILITY_FILTER_LABELS[booksFilters.availability]);
    if (booksFilters.showUpcoming) labels.push(booksFilters.upcomingWindow === 'any' ? 'Upcoming' : UPCOMING_WINDOW_LABELS[booksFilters.upcomingWindow]);
    if (booksFilters.showNoReleaseDate) labels.push('No release date');
    if (booksFilters.seriesKeys.length > 0) {
      if (booksFilters.seriesKeys.length === 1) {
        const selected = seriesFilterOptions.find((option) => option.key === booksFilters.seriesKeys[0]);
        labels.push(selected?.title || 'Series');
      } else { labels.push('Series'); }
    }
    return labels.length === 1 ? labels[0] : null;
  }, [booksFilters, seriesFilterOptions]);

  const activeFilterChips = useMemo(() => {
    const chips: Array<{ key: string; label: string }> = [];
    if (booksFilters.availability !== 'all') chips.push({ key: 'availability', label: AVAILABILITY_FILTER_LABELS[booksFilters.availability] });
    if (booksFilters.showUpcoming) chips.push({ key: 'upcoming', label: booksFilters.upcomingWindow === 'any' ? 'Upcoming' : `Upcoming · ${UPCOMING_WINDOW_LABELS[booksFilters.upcomingWindow]}` });
    if (booksFilters.showNoReleaseDate) chips.push({ key: 'no_release_date', label: 'No release date' });
    for (const seriesKey of booksFilters.seriesKeys) {
      const selected = seriesFilterOptions.find((option) => option.key === seriesKey);
      chips.push({ key: `series:${seriesKey}`, label: selected?.title || 'Series' });
    }
    return chips;
  }, [booksFilters, seriesFilterOptions]);

  const visibleBooks = useMemo(() => filteredGroupedBooks.flatMap((group) => group.books), [filteredGroupedBooks]);
  const allVisibleBooksSelected = useMemo(() => visibleBooks.length > 0 && visibleBooks.every((book) => Boolean(selectedBookIds[book.id])), [visibleBooks, selectedBookIds]);
  const selectedBooks = useMemo(() => books.filter((book) => Boolean(selectedBookIds[book.id])), [books, selectedBookIds]);
  const hasActiveBookSelection = selectedBooks.length > 0;
  const allGroupsCollapsed = useMemo(() => {
    if (groupedBooks.length === 0) return false;
    return groupedBooks.every((g) => (collapsedGroups[g.key] ?? false) === true);
  }, [groupedBooks, collapsedGroups]);

  // Fallback photo for author — emitted upward
  const fallbackAuthorPhotoFromPopularBook = useMemo(() => {
    let bestBook: Book | null = null;
    let bestReaders = -1; let bestRatingsCount = -1; let bestRating = -1;
    for (const book of books) {
      const preview = typeof book.preview === 'string' ? book.preview.trim() : '';
      if (!preview) continue;
      const popularity = extractBookPopularity(book);
      const readers = popularity.readersCount ?? -1;
      const ratingsCount = popularity.ratingsCount ?? -1;
      const rating = popularity.rating ?? -1;
      const isBetter = readers > bestReaders
        || (readers === bestReaders && ratingsCount > bestRatingsCount)
        || (readers === bestReaders && ratingsCount === bestRatingsCount && rating > bestRating)
        || (readers === bestReaders && ratingsCount === bestRatingsCount && rating === bestRating && (book.title || '').localeCompare(bestBook?.title || '') < 0);
      if (isBetter) { bestBook = book; bestReaders = readers; bestRatingsCount = ratingsCount; bestRating = rating; }
    }
    return typeof bestBook?.preview === 'string' ? bestBook.preview.trim() : null;
  }, [books]);

  useEffect(() => {
    onFallbackPhotoChange?.(fallbackAuthorPhotoFromPopularBook);
  }, [fallbackAuthorPhotoFromPopularBook, onFallbackPhotoChange]);

  // Scroll to pending series
  useEffect(() => {
    const targetKey = pendingScrollToSeriesRef.current;
    if (!targetKey) return;
    pendingScrollToSeriesRef.current = null;
    setTimeout(() => {
      const el = document.querySelector(`[data-series-key="${CSS.escape(targetKey)}"]`);
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, 80);
  }, [collapsedGroups, booksSort]);

  // --- more callbacks ---

  const toggleSeriesFilter = useCallback((seriesKey: string) => {
    setBooksFilters((prev) => {
      const exists = prev.seriesKeys.includes(seriesKey);
      return { ...prev, seriesKeys: exists ? prev.seriesKeys.filter((key) => key !== seriesKey) : [...prev.seriesKeys, seriesKey] };
    });
  }, []);

  const toggleAllGroups = useCallback(() => {
    setCollapsedGroups((prev) => {
      const next: Record<string, boolean> = { ...prev };
      const shouldCollapse = !allGroupsCollapsed;
      for (const g of groupedBooks) next[g.key] = shouldCollapse;
      return next;
    });
  }, [allGroupsCollapsed, groupedBooks]);

  const toggleGroupCollapsed = useCallback((key: string) => {
    setCollapsedGroups((prev) => ({ ...prev, [key]: !(prev[key] ?? false) }));
  }, []);

  const handleNavigateToSeries = useCallback((seriesName: string) => {
    setActiveBookDetails(null);
    const targetKey = seriesName.trim();
    setBooksSort((current) => current === 'series_asc' || current === 'series_desc' ? current : 'series_asc');
    const allSeriesKeys = new Set<string>();
    for (const b of books) {
      allSeriesKeys.add((b.series_name || '').trim() || '__standalone__');
      if (showMultipleSeries && b.additional_series) {
        for (const alt of b.additional_series) { const altKey = (alt.name || '').trim(); if (altKey) allSeriesKeys.add(altKey); }
      }
    }
    const next: Record<string, boolean> = {};
    for (const key of allSeriesKeys) next[key] = key !== targetKey;
    setCollapsedGroups(next);
    pendingScrollToSeriesRef.current = targetKey;
  }, [books, showMultipleSeries]);

  const toggleBookSelection = useCallback((bookId: string) => {
    setSelectedBookIds((prev) => { const next = { ...prev }; if (next[bookId]) { delete next[bookId]; } else { next[bookId] = true; } return next; });
  }, []);

  const toggleSelectAllVisibleBooks = useCallback(() => {
    if (visibleBooks.length === 0) return;
    setSelectedBookIds((prev) => {
      const allSelected = visibleBooks.every((book) => Boolean(prev[book.id]));
      const next = { ...prev };
      if (allSelected) { for (const book of visibleBooks) delete next[book.id]; }
      else { for (const book of visibleBooks) next[book.id] = true; }
      return next;
    });
  }, [visibleBooks]);

  const runBulkDownloadForSelection = useCallback(async (contentType: ContentType) => {
    if (!onGetReleases || selectedBooks.length === 0 || bulkDownloadRunningByType[contentType]) return;
    const batchId = `${contentType}:${Date.now()}`;
    const batchTotal = selectedBooks.length;
    setBulkDownloadRunningByType((prev) => ({ ...prev, [contentType]: true }));
    try {
      for (let idx = 0; idx < selectedBooks.length; idx += 1) {
        const book = selectedBooks[idx];
        await triggerReleaseSearch(book, contentType, 'auto_search_download', {
          suppressPerBookAutoSearchToasts: true,
          batchAutoDownload: { batchId, index: idx + 1, total: batchTotal, contentType },
        });
      }
    } finally {
      setBulkDownloadRunningByType((prev) => ({ ...prev, [contentType]: false }));
    }
  }, [onGetReleases, selectedBooks, bulkDownloadRunningByType, triggerReleaseSearch]);

  const toggleSelectAllInGroup = useCallback((groupBooks: Book[]) => {
    if (groupBooks.length === 0) return;
    setSelectedBookIds((prev) => {
      const allSelected = groupBooks.every((book) => Boolean(prev[book.id]));
      const next = { ...prev };
      if (allSelected) { for (const book of groupBooks) delete next[book.id]; }
      else { for (const book of groupBooks) next[book.id] = true; }
      return next;
    });
  }, []);

  const getDefaultBookSearchMode = useCallback(() => {
    const defaultContentType: ContentType = defaultReleaseContentType === 'audiobook' ? 'audiobook' : 'ebook';
    const defaultAction = resolvePrimaryActionForContentType(defaultContentType);
    const isAutoDefault = defaultAction === 'auto_search_download';
    const primaryLabel = defaultContentType === 'audiobook'
      ? isAutoDefault ? 'Auto search + download audiobooks' : 'Interactive search audiobooks'
      : isAutoDefault ? 'Auto search + download eBooks' : 'Interactive search eBooks';
    return { defaultContentType, defaultAction, isAutoDefault, primaryLabel };
  }, [defaultReleaseContentType, resolvePrimaryActionForContentType]);

  const getBookMonitorState = useCallback((book: Book): { monitorEbook: boolean; monitorAudiobook: boolean } => {
    const provider = (book.provider || '').trim();
    const providerId = (book.provider_id || '').trim();
    if (!provider || !providerId) return { monitorEbook: true, monitorAudiobook: true };
    const row = monitoredBookRows.find((r) => r.provider === provider && r.provider_book_id === providerId);
    if (!row) return { monitorEbook: true, monitorAudiobook: true };
    return { monitorEbook: monitoredBookTracksEbook(row), monitorAudiobook: monitoredBookTracksAudiobook(row) };
  }, [monitoredBookRows]);

  const isBookDormant = useCallback((book: Book): boolean => {
    const monitorState = getBookMonitorState(book);
    const availability = getMonitoredAvailabilityForBook(book);
    return isMonitoredBookDormantState({
      monitor_ebook: monitorState.monitorEbook, monitor_audiobook: monitorState.monitorAudiobook,
      has_ebook_available: availability.hasEbook, has_audiobook_available: availability.hasAudiobook,
      ebook_available_format: availability.ebookFormat, audiobook_available_format: availability.audiobookFormat,
    });
  }, [getBookMonitorState, getMonitoredAvailabilityForBook]);

  const toggleBookMonitor = useCallback(async (book: Book, type: 'ebook' | 'audiobook' | 'both', newValue?: boolean) => {
    if (!monitoredEntityId) return;
    const provider = (book.provider || '').trim();
    const providerId = (book.provider_id || '').trim();
    if (!provider || !providerId) return;
    const current = getBookMonitorState(book);
    const patch: { provider: string; provider_book_id: string; monitor_ebook?: boolean; monitor_audiobook?: boolean } = { provider, provider_book_id: providerId };
    if (type === 'ebook') { patch.monitor_ebook = newValue !== undefined ? newValue : !current.monitorEbook; }
    else if (type === 'audiobook') { patch.monitor_audiobook = newValue !== undefined ? newValue : !current.monitorAudiobook; }
    else { const targetValue = newValue !== undefined ? newValue : !(current.monitorEbook && current.monitorAudiobook); patch.monitor_ebook = targetValue; patch.monitor_audiobook = targetValue; }
    setMonitoredBookRows((prev) =>
      prev.map((r) =>
        r.provider === provider && r.provider_book_id === providerId
          ? { ...r, monitor_ebook: patch.monitor_ebook !== undefined ? patch.monitor_ebook : r.monitor_ebook, monitor_audiobook: patch.monitor_audiobook !== undefined ? patch.monitor_audiobook : r.monitor_audiobook }
          : r
      )
    );
    try {
      await updateMonitoredBooksMonitorFlags(monitoredEntityId, patch);
    } catch (e) {
      setMonitoredBookRows((prev) =>
        prev.map((r) =>
          r.provider === provider && r.provider_book_id === providerId
            ? { ...r, monitor_ebook: current.monitorEbook, monitor_audiobook: current.monitorAudiobook }
            : r
        )
      );
      console.error('Failed to update monitoring state:', e);
    }
  }, [monitoredEntityId, getBookMonitorState]);

  const renderBookActionMenuContent = useCallback((
    book: Book,
    defaultContentType: ContentType,
    defaultAction: ReleasePrimaryAction,
  ) => ({ close }: { close: () => void }) => {
    const monitorState = getBookMonitorState(book);
    const isFullyMonitored = monitorState.monitorEbook && monitorState.monitorAudiobook;
    return (
      <div className="py-1">
        <button type="button" onClick={() => { close(); setActiveBookDetails(withMonitoredAvailability(book, monitoredBookRows)); }} className="w-full px-3 py-2 text-left text-sm hover-surface whitespace-nowrap">View info</button>
        {onGetReleases ? (
          <>
            <div className="my-1 border-t border-[var(--border-muted)]" />
            {SEARCH_DROPDOWN_OPTIONS.map((option) => {
              const isDefault = option.contentType === defaultContentType && option.action === defaultAction;
              return (
                <button type="button" key={`${option.contentType}:${option.action}`} onClick={() => { close(); void triggerReleaseSearch(book, option.contentType, option.action); }} className={`w-full px-3 py-2 text-left text-sm hover-surface whitespace-nowrap ${isDefault ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''}`}>{option.label}</button>
              );
            })}
          </>
        ) : null}
        {book.source_url ? (
          <>
            <div className="my-1 border-t border-[var(--border-muted)]" />
            <a href={book.source_url} target="_blank" rel="noreferrer" className="block w-full px-3 py-2 text-left text-sm hover-surface whitespace-nowrap" onClick={() => close()}>View source</a>
          </>
        ) : null}
        {monitoredEntityId ? (
          <>
            <div className="my-1 border-t border-[var(--border-muted)]" />
            <div className="px-3 py-1.5 text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">Monitoring</div>
            <button type="button" onClick={() => { void toggleBookMonitor(book, 'both'); }} className="w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between">
              <span>Monitor Both</span>
              {isFullyMonitored ? <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg> : null}
            </button>
            <button type="button" onClick={() => { void toggleBookMonitor(book, 'ebook'); }} className="w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between">
              <span>Monitor eBook</span>
              {monitorState.monitorEbook ? <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg> : null}
            </button>
            <button type="button" onClick={() => { void toggleBookMonitor(book, 'audiobook'); }} className="w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between">
              <span>Monitor Audiobook</span>
              {monitorState.monitorAudiobook ? <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg> : null}
            </button>
          </>
        ) : null}
      </div>
    );
  }, [onGetReleases, triggerReleaseSearch, monitoredEntityId, getBookMonitorState, toggleBookMonitor]);

  const renderBookOverflowMenu = (book: Book) => {
    const { defaultContentType, defaultAction } = getDefaultBookSearchMode();
    return (
      <Dropdown widthClassName="w-auto" align="right" panelClassName="z-[2200] min-w-[250px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
        renderTrigger={({ isOpen, toggle }) => (
          <button type="button" onClick={toggle} className={`inline-flex items-center justify-center rounded-full text-gray-600 dark:text-gray-200 hover-action transition-colors h-6 w-6 ${isOpen ? 'text-gray-900 dark:text-gray-100' : ''}`} aria-label={`More actions for ${book.title || 'this book'}`} title="More actions">
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M12 6.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM12 12.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM12 18.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Z" /></svg>
          </button>
        )}
      >
        {renderBookActionMenuContent(book, defaultContentType, defaultAction)}
      </Dropdown>
    );
  };

  const renderBookTableActions = (book: Book) => {
    const { defaultContentType, defaultAction, isAutoDefault, primaryLabel } = getDefaultBookSearchMode();
    return (
      <div className="inline-flex items-stretch rounded-lg border border-emerald-500/40">
        <button type="button" onClick={() => { if (!onGetReleases) { setActiveBookDetails(withMonitoredAvailability(book, monitoredBookRows)); return; } void triggerReleaseSearch(book, defaultContentType, defaultAction); }} className="inline-flex items-center justify-center h-8 w-8 text-emerald-600 dark:text-emerald-400 hover-action" aria-label={`${primaryLabel} for ${book.title || 'this book'}`} title={primaryLabel}>
          <span className="relative inline-flex items-center justify-center">
            {defaultContentType === 'audiobook' ? (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12a7.5 7.5 0 1 1 15 0v6a1.5 1.5 0 0 1-1.5 1.5h-.75A2.25 2.25 0 0 1 15 17.25v-3A2.25 2.25 0 0 1 17.25 12h2.25m-15 0H6.75A2.25 2.25 0 0 1 9 14.25v3A2.25 2.25 0 0 1 6.75 19.5H6A1.5 1.5 0 0 1 4.5 18v-6Z" /></svg>
            ) : (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 6.75A2.25 2.25 0 0 1 6.75 4.5h4.5A2.25 2.25 0 0 1 13.5 6.75v12A2.25 2.25 0 0 0 11.25 16.5h-4.5A2.25 2.25 0 0 0 4.5 18.75v-12Zm9 0A2.25 2.25 0 0 1 15.75 4.5h1.5A2.25 2.25 0 0 1 19.5 6.75v12a2.25 2.25 0 0 0-2.25-2.25h-1.5A2.25 2.25 0 0 0 13.5 18.75v-12Z" /></svg>
            )}
            {isAutoDefault ? (
              <svg className="w-2.5 h-2.5 absolute -right-1 -bottom-1" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M12 3v10.5m0 0 3-3m-3 3-3-3" /></svg>
            ) : (
              <svg className="w-2.5 h-2.5 absolute -right-1 -bottom-1" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35m1.35-5.15a5 5 0 1 1-10 0 5 5 0 0 1 10 0Z" /></svg>
            )}
          </span>
        </button>
        <Dropdown widthClassName="w-auto" align="right" panelClassName="z-[2200] min-w-[250px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
          renderTrigger={({ isOpen, toggle }) => (
            <button type="button" onClick={toggle} className={`inline-flex items-center justify-center h-8 w-7 border-l border-emerald-500/30 text-emerald-600 dark:text-emerald-400 hover-action ${isOpen ? 'bg-emerald-500/10' : ''}`} aria-label={`More actions for ${book.title || 'this book'}`} title="More actions">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" /></svg>
            </button>
          )}
        >
          {renderBookActionMenuContent(book, defaultContentType, defaultAction)}
        </Dropdown>
      </div>
    );
  };

  // --- JSX ---
  return (
    <>
      <div className="mt-4">
        <div
          ref={booksToolbarRef}
          className={`sticky z-40 bg-[var(--bg)] ${isPageMode ? 'top-[76px]' : 'top-0'} ${isBooksToolbarPinned ? 'rounded-none border-0 border-b border-[var(--border-muted)] -ml-[100vw] -mr-[100vw] px-[100vw]' : 'rounded-t-2xl border border-[var(--border-muted)] border-b-0'}`}
        >
          <div className="flex items-center justify-between gap-3 px-4 py-2">
            <div className="flex items-center gap-2 min-w-0">
              <button type="button" onClick={toggleAllGroups} className="p-1.5 rounded-full text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action transition-all duration-200" aria-label={allGroupsCollapsed ? 'Expand all series groups' : 'Collapse all series groups'} title={allGroupsCollapsed ? 'Expand all' : 'Collapse all'}>
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
              </button>
              <p className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 truncate">
                Books
                {isRefreshing ? <span className="ml-2 text-[10px] text-gray-400 dark:text-gray-500">refreshing…</span> : null}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              {onGetReleases && selectedBooks.length > 0 ? (
                <>
                  <button type="button" onClick={() => void runBulkDownloadForSelection('ebook')} disabled={selectedBooks.length === 0 || bulkDownloadRunningByType.ebook} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border border-[var(--border-muted)] bg-white/70 dark:bg-white/10 hover-action disabled:opacity-40 disabled:cursor-not-allowed" title="Automatically search/download eBooks for selected books">
                    <BookIcon className="w-3.5 h-3.5" />Download selected
                  </button>
                  <button type="button" onClick={() => void runBulkDownloadForSelection('audiobook')} disabled={selectedBooks.length === 0 || bulkDownloadRunningByType.audiobook} className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border border-[var(--border-muted)] bg-white/70 dark:bg-white/10 hover-action disabled:opacity-40 disabled:cursor-not-allowed" title="Automatically search/download audiobooks for selected books">
                    <AudiobookIcon className="w-3.5 h-3.5" />Download selected
                  </button>
                </>
              ) : null}
              {!isPageMode ? (
                <div className="hidden sm:flex items-center gap-2 rounded-full px-2.5 py-1.5 border border-[var(--border-muted)]" style={{ background: 'var(--bg-soft)' }}>
                  <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35m1.35-5.15a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z" /></svg>
                  <input value={activeBooksQuery} onChange={(e) => updateBooksQuery(e.target.value)} placeholder="Search books or series" className="bg-transparent outline-none text-xs text-gray-700 dark:text-gray-200 placeholder:text-gray-500 w-44" aria-label="Search books" />
                  {activeBooksQuery ? (
                    <button type="button" onClick={() => updateBooksQuery('')} className="p-0.5 rounded-full text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action" aria-label="Clear search" title="Clear">
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
                    </button>
                  ) : null}
                </div>
              ) : null}
              <Dropdown align="right" widthClassName="w-auto flex-shrink-0" panelClassName="w-64" noScrollLimit={true}
                renderTrigger={({ isOpen, toggle }) => (
                  <div className="inline-flex items-center gap-1.5">
                    <button type="button" onClick={toggle} className={`relative p-1.5 rounded-full transition-all duration-200 ${isOpen || activeFiltersCount > 0 ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action'}`} aria-haspopup="menu" aria-expanded={isOpen} aria-label={singleActiveFilterLabel ? `Filter books (${singleActiveFilterLabel})` : 'Filter books'} title={singleActiveFilterLabel ? `Filter books: ${singleActiveFilterLabel}` : 'Filter books'}>
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M3 4.5h18l-7 8.25v5.25l-4 2.25v-7.5L3 4.5Z" /></svg>
                      {activeFiltersCount > 1 ? <span className="pointer-events-none absolute -top-1 -right-1 inline-flex items-center justify-center min-w-4 h-4 px-1 rounded-full text-[10px] font-semibold bg-emerald-600 text-white">{activeFiltersCount}</span> : null}
                    </button>
                    {singleActiveFilterLabel ? <span className="max-w-28 truncate px-2 py-0.5 rounded-full text-[10px] font-medium border border-[var(--border-muted)] text-emerald-700 dark:text-emerald-300 bg-emerald-50/70 dark:bg-emerald-500/10">{singleActiveFilterLabel}</span> : null}
                  </div>
                )}
              >
                {({ close }) => (
                  <div className="py-1" role="menu" aria-label="Book filters">
                    <div className="px-3 pb-1 text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">Availability</div>
                    <div className="relative">
                      <button type="button" className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${AVAILABLE_FILTER_MODES.includes(booksFilters.availability) ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`} onClick={() => { setIsAvailabilityFilterMenuOpen((prev) => { const next = !prev; if (next) setIsMissingFilterMenuOpen(false); return next; }); }}>
                        <span>Available</span>
                        <svg className={`w-3.5 h-3.5 transition-transform ${isAvailabilityFilterMenuOpen ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
                      </button>
                      {isAvailabilityFilterMenuOpen ? (
                        <div className="pl-3 pr-2 pb-1">
                          {AVAILABLE_FILTER_MODES.map((mode) => (
                            <button key={mode} type="button" className={`w-full px-3 py-1.5 text-left text-xs rounded-md hover-surface flex items-center justify-between ${booksFilters.availability === mode ? 'text-emerald-600 dark:text-emerald-400 font-medium' : 'text-gray-600 dark:text-gray-300'}`} onClick={() => { setBooksFilters((prev) => ({ ...prev, availability: mode })); }}>
                              <span>{AVAILABILITY_FILTER_LABELS[mode]}</span>
                              {booksFilters.availability === mode ? <span>✓</span> : null}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                    <div className="relative">
                      <button type="button" className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${MISSING_FILTER_MODES.includes(booksFilters.availability) ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`} onClick={() => { setIsMissingFilterMenuOpen((prev) => { const next = !prev; if (next) setIsAvailabilityFilterMenuOpen(false); return next; }); }}>
                        <span>Missing</span>
                        <svg className={`w-3.5 h-3.5 transition-transform ${isMissingFilterMenuOpen ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
                      </button>
                      {isMissingFilterMenuOpen ? (
                        <div className="pl-3 pr-2 pb-1">
                          {MISSING_FILTER_MODES.map((mode) => (
                            <button key={mode} type="button" className={`w-full px-3 py-1.5 text-left text-xs rounded-md hover-surface flex items-center justify-between ${booksFilters.availability === mode ? 'text-emerald-600 dark:text-emerald-400 font-medium' : 'text-gray-600 dark:text-gray-300'}`} onClick={() => { setBooksFilters((prev) => ({ ...prev, availability: mode })); }}>
                              <span>{AVAILABILITY_FILTER_LABELS[mode]}</span>
                              {booksFilters.availability === mode ? <span>✓</span> : null}
                            </button>
                          ))}
                        </div>
                      ) : null}
                    </div>
                    <div className="my-1 border-t border-[var(--border-muted)]" />
                    <button type="button" className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${booksFilters.showNoReleaseDate ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`} onClick={() => { setBooksFilters((prev) => ({ ...prev, showNoReleaseDate: !prev.showNoReleaseDate })); }}>
                      <span>No release date</span>{booksFilters.showNoReleaseDate ? <span>✓</span> : null}
                    </button>
                    <button type="button" className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${booksFilters.showUpcoming ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`} onClick={() => { setBooksFilters((prev) => ({ ...prev, showUpcoming: !prev.showUpcoming })); }}>
                      <span>Upcoming</span>{booksFilters.showUpcoming ? <span>✓</span> : null}
                    </button>
                    {booksFilters.showUpcoming ? (
                      <div className="pl-3 pr-2 pb-1">
                        {(['any', '30d', '90d', 'this_year'] as UpcomingWindowMode[]).map((windowMode) => (
                          <button key={windowMode} type="button" className={`w-full px-3 py-1.5 text-left text-xs rounded-md hover-surface flex items-center justify-between ${booksFilters.upcomingWindow === windowMode ? 'text-emerald-600 dark:text-emerald-400 font-medium' : 'text-gray-600 dark:text-gray-300'}`} onClick={() => { setBooksFilters((prev) => ({ ...prev, upcomingWindow: windowMode })); }}>
                            <span>{UPCOMING_WINDOW_LABELS[windowMode]}</span>
                            {booksFilters.upcomingWindow === windowMode ? <span>✓</span> : null}
                          </button>
                        ))}
                      </div>
                    ) : null}
                    <div className="my-1 border-t border-[var(--border-muted)]" />
                    <div className="relative">
                      <button type="button" className="w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between" onClick={() => setIsSeriesFilterMenuOpen((prev) => !prev)}>
                        <span className="inline-flex items-center gap-2"><span>Series</span>{booksFilters.seriesKeys.length > 0 ? <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-600 text-white">{booksFilters.seriesKeys.length}</span> : null}</span>
                        <svg className={`w-3.5 h-3.5 transition-transform ${isSeriesFilterMenuOpen ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
                      </button>
                      {isSeriesFilterMenuOpen ? (
                        <div className="absolute left-full top-0 ml-2 w-56 rounded-lg border border-[var(--border-muted)] bg-[var(--bg)] shadow-xl z-10 max-h-56 overflow-auto py-1">
                          {seriesFilterOptions.length === 0 ? <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">No series found.</div> : (
                            seriesFilterOptions.map((option) => {
                              const selected = booksFilters.seriesKeys.includes(option.key);
                              return (
                                <button key={option.key} type="button" className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${selected ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`} onClick={() => { toggleSeriesFilter(option.key); if (closeSeriesFilterOnSelect) setIsSeriesFilterMenuOpen(false); }}>
                                  <span className="truncate pr-2">{option.title}</span>
                                  <span className="inline-flex items-center gap-2 flex-shrink-0"><span className="text-[10px] text-gray-500 dark:text-gray-400">{option.count}</span>{selected ? <span>✓</span> : null}</span>
                                </button>
                              );
                            })
                          )}
                          <div className="border-t border-[var(--border-muted)] mt-1 pt-1">
                            <button type="button" className={`w-full px-3 py-2 text-left text-xs hover-surface flex items-center justify-between ${closeSeriesFilterOnSelect ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-500 dark:text-gray-400'}`} onClick={() => setCloseSeriesFilterOnSelect((prev) => !prev)}>
                              <span>Close on select</span>{closeSeriesFilterOnSelect ? <span>✓</span> : null}
                            </button>
                          </div>
                        </div>
                      ) : null}
                    </div>
                    <div className="mt-1 border-t border-[var(--border-muted)]" />
                    <div className="px-2 pt-2 pb-1 flex items-center justify-between gap-2">
                      <button type="button" className="px-2.5 py-1 text-xs rounded-md hover-surface" onClick={() => { setBooksFilters(createDefaultAuthorBooksFilters()); setIsSeriesFilterMenuOpen(false); setIsAvailabilityFilterMenuOpen(false); setIsMissingFilterMenuOpen(false); }}>Clear</button>
                      <button type="button" className="px-2.5 py-1 text-xs rounded-md hover-surface" onClick={() => { setIsSeriesFilterMenuOpen(false); setIsAvailabilityFilterMenuOpen(false); setIsMissingFilterMenuOpen(false); close(); }}>Done</button>
                    </div>
                  </div>
                )}
              </Dropdown>
              <Dropdown align="right" widthClassName="w-auto flex-shrink-0" panelClassName="w-48"
                renderTrigger={({ isOpen, toggle }) => (
                  <button type="button" onClick={toggle} className={`p-1.5 rounded-full transition-all duration-200 ${isOpen ? 'text-gray-900 dark:text-gray-100' : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action'}`} aria-haspopup="listbox" aria-expanded={isOpen} title="Sort books">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M3 6h18M6 12h12M10 18h4" /></svg>
                  </button>
                )}
              >
                {({ close }) => (
                  <div role="listbox" aria-label="Sort books">
                    {([['series_asc','Series (A–Z)'],['series_desc','Series (Z–A)'],['popular','Most popular'],['rating','Highest rated'],['year_desc','Year (newest)'],['year_asc','Year (oldest)'],['title_asc','Title (A–Z)']] as [AuthorBooksSort, string][]).map(([value, label]) => (
                      <button key={value} type="button" className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === value ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`} onClick={() => { setBooksSort(value); close(); }} role="option" aria-selected={booksSort === value}>{label}</button>
                    ))}
                  </div>
                )}
              </Dropdown>
              <Dropdown align="right" widthClassName="w-auto flex-shrink-0" panelClassName="w-56"
                renderTrigger={({ isOpen, toggle }) => (
                  <button type="button" onClick={toggle} className={`p-1.5 rounded-full transition-all duration-200 ${isOpen ? 'text-gray-900 dark:text-gray-100' : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action'}`} aria-label="Compact tile size" title="Compact tile size">
                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M4 19.5h16M7.5 4.5v12m4.5-9v9m4.5-6v6" /></svg>
                  </button>
                )}
              >
                {() => (
                  <div className="py-1">
                    <button type="button" onClick={toggleSelectAllVisibleBooks} className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${allVisibleBooksSelected ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}>
                      <span>{allVisibleBooksSelected ? 'Unselect all books' : 'Select all books'}</span>{allVisibleBooksSelected ? <span>✓</span> : null}
                    </button>
                    <button type="button" onClick={() => setShowMultipleSeries((prev) => !prev)} className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${showMultipleSeries ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`} title="Show all series a book belongs to, not just the primary one">
                      <span>Show multiple series</span>{showMultipleSeries ? <span>✓</span> : null}
                    </button>
                    <div className="border-t border-[var(--border-muted)] my-1" />
                    <div className="px-3 py-2">
                      <div className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">Compact tile size</div>
                      <input type="range" min={AUTHOR_BOOKS_COMPACT_MIN_WIDTH_MIN} max={AUTHOR_BOOKS_COMPACT_MIN_WIDTH_MAX} step={4} value={booksCompactMinWidth} onChange={(e) => setBooksCompactMinWidth(Number(e.target.value))} className="w-full accent-emerald-600" aria-label="Books compact tile size" title="Books compact tile size" disabled={booksViewMode !== 'compact'} />
                      <div className="mt-1 text-[11px] text-gray-500 dark:text-gray-400 tabular-nums text-right">{booksCompactMinWidth}px</div>
                      {booksViewMode !== 'compact' ? <div className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">Switch to compact view to adjust tile size.</div> : null}
                    </div>
                  </div>
                )}
              </Dropdown>
              <ViewModeToggle className="hidden sm:inline-flex" value={booksViewMode} onChange={(next) => setBooksViewMode(next as AuthorBooksViewMode)} options={[
                { value: 'table', label: 'Table view', icon: <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 6.75h15m-15 5.25h15m-15 5.25h15" /></svg> },
                { value: 'compact', label: 'Compact view', icon: <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 4.5h6.75v6.75H4.5V4.5Zm8.25 0h6.75v6.75h-6.75V4.5ZM4.5 12.75h6.75v6.75H4.5v-6.75Zm8.25 0h6.75v6.75h-6.75v-6.75Z" /></svg> },
              ]} />
              <button type="button" onClick={() => void handleRefreshAndScan()} disabled={isLoadingBooks || isRefreshing} className="p-1.5 rounded-full text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action transition-all duration-200 disabled:opacity-30 disabled:cursor-not-allowed flex-shrink-0" aria-label={monitoredEntityId ? 'Refresh & scan files' : 'Refresh books from provider'} title={monitoredEntityId ? 'Refresh & scan files' : 'Refresh books from provider'}>
                <svg className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182M20.015 4.356v4.992" /></svg>
              </button>
              {monitoredEntityId ? (
                <>
                  <button type="button" onClick={() => void handleRunMonitoredSearch('ebook')} disabled={monitorSearchBusyByType.ebook || monitorSearchBusyByType.audiobook} className="px-2.5 py-1 rounded-full text-[11px] font-medium border border-emerald-500/40 text-emerald-600 dark:text-emerald-400 hover-action disabled:opacity-40" title="Search monitored ebook candidates">
                    {monitorSearchBusyByType.ebook ? 'Searching eBooks…' : 'Search eBooks'}
                  </button>
                  <button type="button" onClick={() => void handleRunMonitoredSearch('audiobook')} disabled={monitorSearchBusyByType.ebook || monitorSearchBusyByType.audiobook} className="px-2.5 py-1 rounded-full text-[11px] font-medium border border-emerald-500/40 text-emerald-600 dark:text-emerald-400 hover-action disabled:opacity-40" title="Search monitored audiobook candidates">
                    {monitorSearchBusyByType.audiobook ? 'Searching audiobooks…' : 'Search audiobooks'}
                  </button>
                </>
              ) : null}
            </div>
          </div>
        </div>

        <div className={`rounded-b-2xl border border-[var(--border-muted)] border-t-0 bg-[var(--bg-soft)] sm:bg-[var(--bg)] ${booksViewMode === 'compact' ? 'overflow-visible' : 'overflow-hidden'}`}>
          {activeFilterChips.length > 0 ? (
            <div className="px-4 pt-3 pb-1 flex flex-wrap items-center gap-2">
              {activeFilterChips.map((chip) => (
                <button key={chip.key} type="button" className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] border border-[var(--border-muted)] bg-[var(--bg-soft)] hover-surface" onClick={() => {
                  if (chip.key === 'availability') { setBooksFilters((prev) => ({ ...prev, availability: 'all' })); return; }
                  if (chip.key === 'upcoming') { setBooksFilters((prev) => ({ ...prev, showUpcoming: false, upcomingWindow: 'any' })); return; }
                  if (chip.key === 'no_release_date') { setBooksFilters((prev) => ({ ...prev, showNoReleaseDate: false })); return; }
                  if (chip.key.startsWith('series:')) { const seriesKey = chip.key.slice('series:'.length); setBooksFilters((prev) => ({ ...prev, seriesKeys: prev.seriesKeys.filter((key) => key !== seriesKey) })); }
                }} title={`Remove filter: ${chip.label}`}>
                  <span className="truncate max-w-44">{chip.label}</span>
                  <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
                </button>
              ))}
            </div>
          ) : null}

          {monitoredEntityId ? (
            <div className="px-4 pb-3">
              {monitorSearchSummary ? <div className="text-sm text-emerald-600 dark:text-emerald-400">{monitorSearchSummary}</div> : null}
            </div>
          ) : null}

          <div className="px-4 py-3">
            {booksError && <div className="text-sm text-red-500">{booksError}</div>}
            {syncStatus === 'syncing' ? (
              <div className="flex items-center gap-2 mb-2 text-sm text-gray-500 dark:text-gray-400">
                <svg className="w-4 h-4 animate-spin flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182M20.015 4.356v4.992" /></svg>
                <span>{syncPhase === 'fetching_books' ? 'Fetching books…' : syncPhase === 'scanning_files' ? 'Scanning filesystem…' : syncPhase === 'fetching_covers' ? 'Fetching covers…' : 'Syncing…'}</span>
              </div>
            ) : syncStatus === 'error' ? (
              <div className="text-sm text-red-500 mb-2">Sync error — try refreshing manually.</div>
            ) : null}
            {books.length === 0 && isLoadingBooks ? (
              <div className="text-sm text-gray-600 dark:text-gray-300">Loading…</div>
            ) : books.length === 0 && !isLoadingBooks && syncStatus !== 'syncing' ? (
              <div className="text-sm text-gray-600 dark:text-gray-300">No books found.</div>
            ) : filteredGroupedBooks.length === 0 ? (
              <div className="text-sm text-gray-600 dark:text-gray-300">No books match the current filters.</div>
            ) : (
              <>
                <div className={`w-full rounded-xl ${booksViewMode === 'compact' ? 'overflow-visible' : 'overflow-hidden'}`} style={{ background: 'var(--bg-soft)' }}>
                  {filteredGroupedBooks.map((group, groupIndex) => {
                    const isCollapsed = collapsedGroups[group.key] ?? false;
                    const isDormantGroup = Boolean((group as any).isDormantGroup);
                    const allSelectedInGroup = group.books.length > 0 && group.books.every((book) => Boolean(selectedBookIds[book.id]));
                    const booksInSeries = group.books.length;
                    const booksOnDisk = group.books.reduce((count, book) => {
                      const availability = getMonitoredAvailabilityForBook(book);
                      return count + (availability.hasEbook || availability.hasAudiobook ? 1 : 0);
                    }, 0);
                    return (
                      <div key={group.key} data-series-key={group.key} className={groupIndex === 0 ? '' : 'mt-3'}>
                        <div className={`w-full px-3 sm:px-4 py-2 border-t border-b border-gray-200/60 dark:border-gray-800/60 bg-black/5 dark:bg-white/5 flex items-center gap-3 ${isDormantGroup ? 'opacity-60' : ''}`}>
                          <button type="button" onClick={() => toggleSelectAllInGroup(group.books)} className="flex-shrink-0 text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 transition-colors" aria-label={allSelectedInGroup ? `Unselect all books in ${group.title}` : `Select all books in ${group.title}`} title={allSelectedInGroup ? 'Unselect all in series' : 'Select all in series'}>
                            {allSelectedInGroup ? (
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><rect x="4" y="4" width="16" height="16" rx="3" /><path strokeLinecap="round" strokeLinejoin="round" d="m8 12 2.5 2.5L16 9" /></svg>
                            ) : (
                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><rect x="4" y="4" width="16" height="16" rx="3" /></svg>
                            )}
                          </button>
                          <button type="button" onClick={() => toggleGroupCollapsed(group.key)} className="flex-1 flex items-center gap-2 min-w-0 hover-action" aria-expanded={!isCollapsed}>
                            <svg className={`w-3.5 h-3.5 flex-shrink-0 transition-transform duration-200 ${isCollapsed ? '' : 'rotate-90'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
                            <div className="min-w-0 flex items-center gap-2">
                              <p className="text-s font-semibold text-gray-700 dark:text-gray-200 truncate">{group.title}</p>
                              <span className={`text-[11px] tabular-nums ${booksOnDisk > 0 ? 'text-emerald-500 dark:text-emerald-400' : 'text-gray-500 dark:text-gray-400'}`}>({booksOnDisk}/{booksInSeries})</span>
                            </div>
                          </button>
                        </div>
                        {!isCollapsed ? (
                          booksViewMode === 'compact' ? (
                            <div className="px-3 py-3 grid gap-3 justify-start" style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${booksCompactMinWidth}px, ${booksCompactMinWidth}px))` }}>
                              {group.books.map((book) => {
                                const isSelected = Boolean(selectedBookIds[book.id]);
                                const _bookProvider = (book.provider || '').trim();
                                const _bookProviderId = (book.provider_id || '').trim();
                                const _bookRow = (_bookProvider && _bookProviderId) ? monitoredBookRowByKey.get(`${_bookProvider}:${_bookProviderId}`) : undefined;
                                const ebookStatus = _bookRow ? getFormatStatus(_bookRow, 'ebook') : null;
                                const audiobookStatus = _bookRow ? getFormatStatus(_bookRow, 'audiobook') : null;
                                const isPrimaryGroup = group.key === '__standalone__' || group.key === (book.series_name || '').trim();
                                let groupSeriesPos = book.series_position;
                                let groupSeriesCount = book.series_count;
                                if (!isPrimaryGroup && book.additional_series) {
                                  const match = book.additional_series.find((a) => (a.name || '').trim() === group.key);
                                  groupSeriesPos = match ? (match.position ?? undefined) : undefined;
                                  groupSeriesCount = match ? (match.count ?? undefined) : undefined;
                                }
                                const seriesName = (book.series_name || (group.key !== '__standalone__' ? group.title : '') || '').trim();
                                const seriesLabel = seriesName && groupSeriesPos != null ? `${seriesName} #${groupSeriesPos}` : seriesName;
                                const isSeriesSort = booksSort === 'series_asc' || booksSort === 'series_desc';
                                const isYearSort = booksSort === 'year_asc' || booksSort === 'year_desc';
                                const showSeriesName = Boolean(seriesLabel) && !isSeriesSort;
                                const showExtendedMeta = booksCompactMinWidth >= 178;
                                const popularity = extractBookPopularity(book);
                                const showPopularity = booksCompactMinWidth >= 194 && (popularity.rating !== null || popularity.readersCount !== null);
                                const yearPart = !isYearSort ? (book.year || 'TBA') : '';
                                const metaLine = yearPart ? `${yearPart}${book.author ? ` • ${book.author}` : ''}` : (book.author || '');
                                const popularityLine = [popularity.rating !== null ? `★ ${popularity.rating.toFixed(1)}` : null, popularity.readersCount !== null ? `${popularity.readersCount.toLocaleString()} readers` : null].filter(Boolean).join(' • ');
                                const isDormant = isBookDormant(book);
                                return (
                                  <MonitoredBookCompactTile key={book.id} title={book.title || 'Untitled'} onOpenDetails={() => setActiveBookDetails(withMonitoredAvailability(book, monitoredBookRows))} onToggleSelect={() => toggleBookSelection(book.id)} isSelected={isSelected} hasActiveSelection={hasActiveBookSelection} seriesPosition={groupSeriesPos} seriesCount={groupSeriesCount} ebookStatus={ebookStatus} audiobookStatus={audiobookStatus} seriesLabel={seriesLabel} showSeriesName={showSeriesName} metaLine={metaLine} showMetaLine={showExtendedMeta} popularityLine={popularityLine} showPopularityLine={showPopularity} thumbnail={<RowThumbnail url={book.preview} alt={book.title || undefined} className="w-full aspect-[2/3]" />} overflowMenu={renderBookOverflowMenu(book)} isDimmed={isDormant} />
                                );
                              })}
                            </div>
                          ) : (
                            <div className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
                              <div className="hidden sm:grid items-center px-1.5 sm:px-2 pt-1 pb-2 sm:gap-y-1 sm:gap-x-2 grid-cols-[auto_auto_minmax(0,2fr)_minmax(190px,190px)_minmax(90px,90px)]">
                                <div /><div /><div />
                                <div className="flex w-full justify-center"><span className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">Status</span></div>
                                <div />
                              </div>
                              {group.books.map((book) => (
                                (() => {
                                  const popularity = extractBookPopularity(book);
                                  const hasPopularity = popularity.rating !== null || popularity.readersCount !== null;
                                  const seriesLabel = (book.series_name || (group.key !== '__standalone__' ? group.title : '') || '').trim();
                                  const showSeriesInfo = Boolean(seriesLabel) && group.key !== '__standalone__';
                                  const isPrimaryGroup = group.key === '__standalone__' || group.key === (book.series_name || '').trim();
                                  let groupSeriesPos = book.series_position;
                                  let groupSeriesCount = book.series_count;
                                  if (!isPrimaryGroup && book.additional_series) {
                                    const match = book.additional_series.find((a) => (a.name || '').trim() === group.key);
                                    groupSeriesPos = match ? (match.position ?? undefined) : undefined;
                                    groupSeriesCount = match ? (match.count ?? undefined) : undefined;
                                  }
                                  const hasSeriesPosition = groupSeriesPos != null;
                                  const isDormant = isBookDormant(book);
                                  const _tProvider = (book.provider || '').trim();
                                  const _tProviderId = (book.provider_id || '').trim();
                                  const _tRow = (_tProvider && _tProviderId) ? monitoredBookRowByKey.get(`${_tProvider}:${_tProviderId}`) : undefined;
                                  const tEbookStatus = _tRow ? getFormatStatus(_tRow, 'ebook') : null;
                                  const tAudiobookStatus = _tRow ? getFormatStatus(_tRow, 'audiobook') : null;
                                  return (
                                    <MonitoredBookTableRow key={book.id} isDimmed={isDormant}
                                      leadingControl={(() => {
                                        const isSelected = Boolean(selectedBookIds[book.id]);
                                        return (
                                          <button type="button" onClick={() => toggleBookSelection(book.id)} className={`transition-opacity ${isSelected || hasActiveBookSelection ? 'opacity-100' : 'opacity-0 group-hover:opacity-50'} ${isSelected ? 'text-emerald-500 dark:text-emerald-400' : 'text-gray-500 dark:text-gray-400'}`} role="checkbox" aria-checked={isSelected} aria-label={`Select ${book.title || 'book'}`} title={isSelected ? 'Unselect book' : 'Select book'}>
                                            {isSelected ? (
                                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><rect x="4" y="4" width="16" height="16" rx="3" /><path strokeLinecap="round" strokeLinejoin="round" d="m8 12 2.5 2.5L16 9" /></svg>
                                            ) : (
                                              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><rect x="4" y="4" width="16" height="16" rx="3" /></svg>
                                            )}
                                          </button>
                                        );
                                      })()}
                                      thumbnail={<RowThumbnail url={book.preview} alt={book.title || undefined} />}
                                      onOpen={() => setActiveBookDetails(withMonitoredAvailability(book, monitoredBookRows))}
                                      titleRow={(
                                        <div className="flex items-center gap-2 min-w-0">
                                          <h3 className="font-semibold text-xs min-[400px]:text-sm sm:text-base leading-tight truncate" title={book.title || 'Untitled'}>{book.title || 'Untitled'}</h3>
                                          {showSeriesInfo ? <span className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-500 dark:text-gray-400 truncate">• {seriesLabel}</span> : null}
                                          {hasSeriesPosition ? (
                                            <span className="inline-flex px-1 py-0 text-[9px] sm:text-[10px] font-bold text-white bg-emerald-600 rounded flex-shrink-0" style={{ boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)', textShadow: '0 1px 2px rgba(0, 0, 0, 0.3)' }} title={seriesLabel ? `${seriesLabel}${groupSeriesCount ? ` (${groupSeriesPos}/${groupSeriesCount})` : ` (#${groupSeriesPos})`}` : undefined}>
                                              #{groupSeriesPos}{groupSeriesCount != null ? `/${groupSeriesCount}` : ''}
                                            </span>
                                          ) : null}
                                        </div>
                                      )}
                                      subtitleRow={(
                                        <p className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-600 dark:text-gray-300 truncate">
                                          {book.author || author?.name || 'Unknown author'}
                                          {book.year ? <span> • {book.year}</span> : null}
                                        </p>
                                      )}
                                      metaRow={hasPopularity ? (
                                        <div className="text-[10px] min-[400px]:text-xs text-gray-500 dark:text-gray-400 flex items-center gap-2">
                                          {popularity.rating !== null ? (
                                            <span className="inline-flex items-center gap-1 whitespace-nowrap">
                                              <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true"><path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.286 3.96a1 1 0 00.95.69h4.162c.969 0 1.371 1.24.588 1.81l-3.367 2.446a1 1 0 00-.364 1.118l1.286 3.96c.3.921-.755 1.688-1.538 1.118l-3.367-2.446a1 1 0 00-1.176 0l-3.367 2.446c-.783.57-1.838-.197-1.539-1.118l1.287-3.96a1 1 0 00-.364-1.118L2.063 9.387c-.783-.57-.38-1.81.588-1.81h4.162a1 1 0 00.95-.69l1.286-3.96Z" /></svg>
                                              <span>{popularity.rating.toFixed(1)}{popularity.ratingsCount !== null ? ` (${popularity.ratingsCount.toLocaleString()})` : ''}</span>
                                            </span>
                                          ) : null}
                                          {popularity.readersCount !== null ? (
                                            <span className="inline-flex items-center gap-1 whitespace-nowrap">
                                              <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8} aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0-3-.479c-1.07 0-2.098.18-3 .512m6 0a7.5 7.5 0 1 0-6 0m6 0a9.372 9.372 0 0 1 3 .512M9 10.5a3 3 0 1 1 6 0 3 3 0 0 1-6 0Z" /></svg>
                                              <span>{popularity.readersCount.toLocaleString()}</span>
                                            </span>
                                          ) : null}
                                        </div>
                                      ) : undefined}
                                      availabilitySlot={(
                                        <div className="flex items-center justify-center gap-1">
                                          {tEbookStatus ? <FormatStatusBadge format="ebook" status={tEbookStatus} /> : null}
                                          {tAudiobookStatus ? <FormatStatusBadge format="audiobook" status={tAudiobookStatus} /> : null}
                                        </div>
                                      )}
                                      trailingSlot={renderBookTableActions(book)}
                                    />
                                  );
                                })()
                              ))}
                            </div>
                          )
                        ) : null}
                      </div>
                    );
                  })}
                </div>
                {isLoadingBooks && (
                  <div className="flex items-center gap-2 px-3 py-3 text-sm text-gray-500 dark:text-gray-400">
                    <div className="w-3.5 h-3.5 border-2 border-current border-t-transparent rounded-full animate-spin" />
                    Loading more…
                  </div>
                )}
              </>
            )}
          </div>
        </div>
      </div>

      <BookDetailsModal
        entityId={monitoredEntityId ?? null}
        provider={activeBookDetails?.provider ?? null}
        providerBookId={activeBookDetails?.provider_id ?? null}
        onClose={() => setActiveBookDetails(null)}
        onToggleMonitor={activeBookDetails ? (type) => void toggleBookMonitor(activeBookDetails, type) : undefined}
        onNavigateToSeries={handleNavigateToSeries}
        renderEmbeddedSearch={(book, contentType) => {
          if (renderEmbeddedSearch) {
            return renderEmbeddedSearch(book, contentType);
          }
          return (
            <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] p-4">
              <div className="text-sm text-gray-600 dark:text-gray-300">
                Embedded search is unavailable.
              </div>
            </div>
          );
        }}
      />
    </>
  );
};
