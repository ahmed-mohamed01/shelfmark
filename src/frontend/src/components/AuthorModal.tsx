import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Book, ContentType, OpenReleasesOptions, ReleasePrimaryAction, StatusData } from '../types';
import { getMetadataAuthorInfo, getMetadataBookInfo, listMonitoredBooks, MonitoredBookRow, MonitoredBooksResponse, syncMonitoredEntity, updateMonitoredBooksSeries, MetadataAuthor, MetadataAuthorDetailsResult, searchMetadata, listMonitoredBookFiles, MonitoredBookFileRow, scanMonitoredEntityFiles, updateMonitoredBooksMonitorFlags } from '../services/api';
import { withBasePath } from '../utils/basePath';
import { getFormatColor } from '../utils/colorMaps';
import { Dropdown } from './Dropdown';
import { EditAuthorModal } from './EditAuthorModal';
import { BookDetailsModal } from './BookDetailsModal';
import { MonitoredBookCompactTile } from './MonitoredBookCompactTile';
import { MonitoredBookTableRow } from './MonitoredBookTableRow';
import { ViewModeToggle } from './ViewModeToggle';

const BooksListThumbnail = ({
  preview,
  title,
  className,
}: {
  preview?: string;
  title?: string;
  className?: string;
}) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);
  const sizeClass = className || 'w-7 h-10 sm:w-10 sm:h-14';

  if (!preview || imageError) {
    return (
      <div
        className={`${sizeClass} rounded bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-[8px] sm:text-[9px] font-medium text-gray-500 dark:text-gray-300`}
        aria-label="No cover available"
      >
        No Cover
      </div>
    );
  }

  return (
    <div className={`relative ${sizeClass} rounded overflow-hidden bg-gray-100 dark:bg-gray-800 border border-white/40 dark:border-gray-700/70`}>
      {!imageLoaded && (
        <div className="absolute inset-0 bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
      )}
      <img
        src={preview}
        alt={title || 'Book cover'}
        className="w-full h-full object-cover object-top"
        loading="lazy"
        onLoad={() => setImageLoaded(true)}
        onError={() => setImageError(true)}
        style={{ opacity: imageLoaded ? 1 : 0, transition: 'opacity 0.2s ease-in-out' }}
      />
    </div>
  );
};

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

export interface AuthorModalAuthor {
  name: string;
  provider?: string | null;
  provider_id?: string | null;
  source_url?: string | null;
  photo_url?: string | null;
}

interface AuthorModalProps {
  author: AuthorModalAuthor | null;
  onClose: () => void;
  displayMode?: 'modal' | 'page';
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
  initialBooksQuery?: string;
  initialBookProvider?: string | null;
  initialBookProviderId?: string | null;
  monitoredEntityId?: number | null;
  status?: StatusData;
  booksSearchQuery?: string;
  onBooksSearchQueryChange?: (value: string) => void;
  openEditOnMount?: boolean;
}


const EBOOK_MATCH_FORMATS = ['epub', 'pdf', 'mobi', 'azw', 'azw3'];
const AUDIOBOOK_MATCH_FORMATS = ['m4b', 'm4a', 'mp3', 'flac'];

const SEARCH_DROPDOWN_OPTIONS: Array<{
  contentType: ContentType;
  action: ReleasePrimaryAction;
  label: string;
}> = [
  { contentType: 'ebook', action: 'interactive_search', label: 'eBook — Interactive Search' },
  { contentType: 'ebook', action: 'auto_search_download', label: 'eBook — Auto Search + Download' },
  { contentType: 'audiobook', action: 'interactive_search', label: 'Audiobook — Interactive Search' },
  { contentType: 'audiobook', action: 'auto_search_download', label: 'Audiobook — Auto Search + Download' },
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
  january: 0,
  jan: 0,
  february: 1,
  feb: 1,
  march: 2,
  mar: 2,
  april: 3,
  apr: 3,
  may: 4,
  june: 5,
  jun: 5,
  july: 6,
  jul: 6,
  august: 7,
  aug: 7,
  september: 8,
  sep: 8,
  sept: 8,
  october: 9,
  oct: 9,
  november: 10,
  nov: 10,
  december: 11,
  dec: 11,
};

const parseReleaseDateValue = (value: string): { date: Date | null; yearOnly: number | null } => {
  const input = value.trim();
  if (!input) {
    return { date: null, yearOnly: null };
  }

  const yearOnlyMatch = input.match(/^(\d{4})$/);
  if (yearOnlyMatch) {
    const year = Number.parseInt(yearOnlyMatch[1], 10);
    return { date: null, yearOnly: Number.isFinite(year) ? year : null };
  }

  const isoYmd = input.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (isoYmd) {
    const year = Number.parseInt(isoYmd[1], 10);
    const month = Number.parseInt(isoYmd[2], 10) - 1;
    const day = Number.parseInt(isoYmd[3], 10);
    const date = new Date(year, month, day);
    if (!Number.isNaN(date.getTime())) {
      return { date, yearOnly: null };
    }
  }

  const isoYm = input.match(/^(\d{4})-(\d{1,2})$/);
  if (isoYm) {
    const year = Number.parseInt(isoYm[1], 10);
    const month = Number.parseInt(isoYm[2], 10) - 1;
    const date = new Date(year, month, 1);
    if (!Number.isNaN(date.getTime())) {
      return { date, yearOnly: null };
    }
  }

  const monthYear = input.match(/^([A-Za-z]{3,9})\s+(\d{4})$/);
  if (monthYear) {
    const month = MONTHS_BY_NAME[monthYear[1].toLowerCase()];
    const year = Number.parseInt(monthYear[2], 10);
    if (month != null && Number.isFinite(year)) {
      return { date: new Date(year, month, 1), yearOnly: null };
    }
  }

  const yearMonth = input.match(/^(\d{4})\s+([A-Za-z]{3,9})$/);
  if (yearMonth) {
    const month = MONTHS_BY_NAME[yearMonth[2].toLowerCase()];
    const year = Number.parseInt(yearMonth[1], 10);
    if (month != null && Number.isFinite(year)) {
      return { date: new Date(year, month, 1), yearOnly: null };
    }
  }

  const fallback = new Date(input);
  if (!Number.isNaN(fallback.getTime())) {
    return { date: fallback, yearOnly: null };
  }

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
      label.includes('released') ||
      label.includes('release date') ||
      label.includes('publish date') ||
      label.includes('publication date') ||
      label === 'release' ||
      label === 'published' ||
      label === 'publication';
    if (isReleaseLabel) {
      return value;
    }
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
      if (maybeRating !== null && maybeRating <= 10) {
        rating = maybeRating;
      }

      const parenCount = value.match(/\(([^)]+)\)/);
      if (parenCount) {
        const parsedCount = parseIntFromText(parenCount[1]);
        if (parsedCount !== null) {
          ratingsCount = parsedCount;
        }
      }
      continue;
    }

    if (ratingsCount === null && /ratings?/.test(label)) {
      const parsedCount = parseIntFromText(value);
      if (parsedCount !== null) {
        ratingsCount = parsedCount;
      }
      continue;
    }

    if (readersCount === null && (icon === 'users' || /readers?|users?|followers?|people/.test(label))) {
      const parsedReaders = parseIntFromText(value);
      if (parsedReaders !== null) {
        readersCount = parsedReaders;
      }
    }
  }

  return { rating, ratingsCount, readersCount };
};

export const AuthorModal = ({
  author,
  onClose,
  displayMode = 'modal',
  onGetReleases,
  defaultReleaseContentType = 'ebook',
  defaultReleaseActionEbook = 'interactive_search',
  defaultReleaseActionAudiobook = 'interactive_search',
  initialBooksQuery,
  initialBookProvider,
  initialBookProviderId,
  monitoredEntityId,
  status,
  booksSearchQuery,
  onBooksSearchQueryChange,
  openEditOnMount = false,
}: AuthorModalProps) => {
  const [isClosing, setIsClosing] = useState(false);
  const [details, setDetails] = useState<MetadataAuthor | null>(null);
  const [supportsDetails, setSupportsDetails] = useState<boolean | null>(null);
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [showMoreDetails, setShowMoreDetails] = useState(false);
  const [booksSort, setBooksSort] = useState<AuthorBooksSort>(() => {
    const saved = localStorage.getItem('authorBooksSort');
    return saved === 'year_desc'
      || saved === 'year_asc'
      || saved === 'title_asc'
      || saved === 'series_asc'
      || saved === 'series_desc'
      || saved === 'popular'
      || saved === 'rating'
      ? saved
      : 'series_asc';
  });
  const [booksViewMode, setBooksViewMode] = useState<AuthorBooksViewMode>(() => {
    const saved = localStorage.getItem('authorBooksViewMode');
    if (saved === 'compact' || saved === 'card') return 'compact';
    return 'table';
  });
  const [booksCompactMinWidth, setBooksCompactMinWidth] = useState<number>(() => {
    const raw = localStorage.getItem('authorBooksCompactMinWidth');
    const parsed = raw ? Number(raw) : Number.NaN;
    if (!Number.isFinite(parsed)) {
      return AUTHOR_BOOKS_COMPACT_MIN_WIDTH_DEFAULT;
    }
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
  const [booksQuery, setBooksQuery] = useState('');
  const [selectedBookIds, setSelectedBookIds] = useState<Record<string, boolean>>({});
  const [bulkDownloadRunningByType, setBulkDownloadRunningByType] = useState<Record<ContentType, boolean>>({
    ebook: false,
    audiobook: false,
  });

  const [isEditModalOpen, setIsEditModalOpen] = useState(openEditOnMount);

  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState<string | null>(null);
  const [monitorSearchBusyByType, setMonitorSearchBusyByType] = useState<Record<ContentType, boolean>>({
    ebook: false,
    audiobook: false,
  });
  const [monitorSearchSummary, setMonitorSearchSummary] = useState<string | null>(null);
  const [files, setFiles] = useState<MonitoredBookFileRow[]>([]);
  const [monitoredBookRows, setMonitoredBookRows] = useState<MonitoredBookRow[]>([]);
  const [autoRefreshBusy, setAutoRefreshBusy] = useState(false);
  const [activeBookDetails, setActiveBookDetails] = useState<Book | null>(null);
  const [hasAppliedInitialBookSelection, setHasAppliedInitialBookSelection] = useState(false);
  const [isBooksToolbarPinned, setIsBooksToolbarPinned] = useState(false);
  const booksToolbarRef = useRef<HTMLDivElement | null>(null);
  const isPageMode = displayMode === 'page';
  const activeBooksQuery = booksSearchQuery ?? booksQuery;
  const updateBooksQuery = (value: string) => {
    if (onBooksSearchQueryChange) {
      onBooksSearchQueryChange(value);
      return;
    }
    setBooksQuery(value);
  };

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
    if (prov && bid) {
      keys.push(`p:${prov}:${bid}`);
    }
    if (prov && providerBookId) {
      keys.push(`p:${prov}:${providerBookId}`);
    }

    if (b.id !== null && b.id !== undefined) {
      const sid = String(b.id);
      keys.push(`id:${sid}`);
      keys.push(`rk:${normalizeStatusKeyPart(sid)}`);
    }

    if (bid) {
      keys.push(`rk:${normalizeStatusKeyPart(bid)}`);
    }
    if (providerBookId) {
      keys.push(`rk:${normalizeStatusKeyPart(providerBookId)}`);
    }

    const t = normalizeStatusKeyPart(b.title);
    const a = normalizeStatusKeyPart(b.author);
    const st = normalizeStatusKeyPart(b.search_title);
    const sa = normalizeStatusKeyPart(b.search_author);
    const firstAuthor = normalizeStatusKeyPart(Array.isArray(b.authors) ? b.authors[0] : '');
    if (t && a) {
      keys.push(`ta:${t}|${a}`);
    }
    if (st && sa) {
      keys.push(`ta:${st}|${sa}`);
    }
    if (t && firstAuthor) {
      keys.push(`ta:${t}|${firstAuthor}`);
    }
    if (t) {
      keys.push(`t:${t}`);
    }
    if (st) {
      keys.push(`t:${st}`);
    }

    return keys;
  };

  const resolvePrimaryActionForContentType = useCallback((contentType: ContentType): ReleasePrimaryAction => {
    return contentType === 'audiobook' ? defaultReleaseActionAudiobook : defaultReleaseActionEbook;
  }, [defaultReleaseActionAudiobook, defaultReleaseActionEbook]);


  const triggerReleaseSearch = useCallback(
    async (
      book: Book,
      contentType: ContentType,
      actionOverride?: ReleasePrimaryAction,
      options?: OpenReleasesOptions,
    ) => {
      if (!onGetReleases) return;
      await onGetReleases(book, contentType, monitoredEntityId, actionOverride, options);
    },
    [onGetReleases, monitoredEntityId]
  );

  const lastAutoRefreshSignatureRef = useMemo(() => ({ value: '' }), []);

  const handleClose = useCallback(() => {
    if (isPageMode) {
      onClose();
      return;
    }
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [isPageMode, onClose]);

  useEffect(() => {
    if (isPageMode) {
      return;
    }
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [handleClose, isPageMode]);

  useEffect(() => {
    if (author && !isPageMode) {
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = previousOverflow;
      };
    }
  }, [author, isPageMode]);

  useEffect(() => {
    if (!isPageMode) {
      setIsBooksToolbarPinned(false);
      return;
    }

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

  useEffect(() => {
    if (!author || !monitoredEntityId) {
      setIsEditModalOpen(false);
      setFilesLoading(false);
      setFilesError(null);
      setFiles([]);
      return;
    }
  }, [author, monitoredEntityId]);

  useEffect(() => {
    setHasAppliedInitialBookSelection(false);
    const nextQuery = (initialBooksQuery || '').trim();
    if (onBooksSearchQueryChange) {
      onBooksSearchQueryChange(nextQuery);
      return;
    }
    setBooksQuery(nextQuery);
  }, [author?.name, initialBooksQuery, initialBookProvider, initialBookProviderId, onBooksSearchQueryChange]);

  useEffect(() => {
    if (!author || !monitoredEntityId) return;
    let alive = true;
    const load = async () => {
      setFilesLoading(true);
      setFilesError(null);
      try {
        const resp = await listMonitoredBookFiles(monitoredEntityId);
        if (!alive) return;
        setFiles(resp.files || []);
      } catch (e) {
        if (!alive) return;
        const message = e instanceof Error ? e.message : 'Failed to load matched files';
        console.warn('AuthorModal: Failed to load matched files', message);
        if (!String(message).toLowerCase().includes('directory not found')) {
          setFilesError(message);
        }
        setFiles([]);
      } finally {
        if (alive) setFilesLoading(false);
      }
    };
    void load();
    return () => {
      alive = false;
    };
  }, [author, monitoredEntityId]);

  useEffect(() => {
    if (hasAppliedInitialBookSelection) {
      return;
    }

    const provider = (initialBookProvider || '').trim();
    const providerId = (initialBookProviderId || '').trim();
    if (!provider || !providerId) {
      setHasAppliedInitialBookSelection(true);
      return;
    }

    if (isLoadingBooks) {
      return;
    }

    const match = books.find((book) => (book.provider || '') === provider && (book.provider_id || '') === providerId);
    if (match) {
      setActiveBookDetails(match);
    }
    setHasAppliedInitialBookSelection(true);
  }, [books, hasAppliedInitialBookSelection, initialBookProvider, initialBookProviderId, isLoadingBooks]);

  const handleRefreshAndScan = useCallback(async () => {
    if (!monitoredEntityId) {
      setRefreshKey((k) => k + 1);
      return;
    }

    setIsRefreshing(true);
    setFilesError(null);
    try {
      setRefreshKey((k) => k + 1);
      await syncMonitoredEntity(monitoredEntityId);
      await scanMonitoredEntityFiles(monitoredEntityId);
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Refresh & scan failed';
      console.warn('AuthorModal: Refresh & scan failed', message);
      if (!String(message).toLowerCase().includes('directory not found')) {
        setFilesError(message);
      }
    } finally {
      try {
        const resp = await listMonitoredBookFiles(monitoredEntityId);
        setFiles(resp.files || []);
      } catch (e) {
        const message = e instanceof Error ? e.message : 'Failed to load matched files';
        console.warn('AuthorModal: failed to reload matched files after refresh', message);
        setFiles([]);
      }
      setIsRefreshing(false);
    }
  }, [monitoredEntityId]);

  const handleRunMonitoredSearch = useCallback(async (contentType: ContentType) => {
    if (!monitoredEntityId || !onGetReleases) {
      return;
    }

    setMonitorSearchBusyByType((prev) => ({ ...prev, [contentType]: true }));
    setMonitorSearchSummary(null);
    setFilesError(null);

    try {
      // Scan files first to get current availability
      await scanMonitoredEntityFiles(monitoredEntityId);
      const filesResp = await listMonitoredBookFiles(monitoredEntityId);
      setFiles(filesResp.files || []);

      // Build set of books that already have files for this content type
      const matchFormats = contentType === 'ebook' ? EBOOK_MATCH_FORMATS : AUDIOBOOK_MATCH_FORMATS;
      const availableBookKeys = new Set<string>();
      for (const f of filesResp.files || []) {
        const prov = typeof f.provider === 'string' ? f.provider : '';
        const bid = typeof f.provider_book_id === 'string' ? f.provider_book_id : '';
        const ft = typeof f.file_type === 'string' ? f.file_type.toLowerCase() : '';
        if (prov && bid && matchFormats.includes(ft)) {
          availableBookKeys.add(`${prov}:${bid}`);
        }
      }

      // Filter candidates: monitored for this content type AND missing files
      const monitorFlag = contentType === 'ebook' ? 'monitor_ebook' : 'monitor_audiobook';
      const candidates = monitoredBookRows.filter((row) => {
        const prov = row.provider || '';
        const bid = row.provider_book_id || '';
        if (!prov || !bid) return false;
        // Check if monitored for this content type
        const isMonitored = Boolean(row[monitorFlag]);
        if (!isMonitored) return false;
        // Check if already has files
        const key = `${prov}:${bid}`;
        if (availableBookKeys.has(key)) return false;
        return true;
      });

      if (candidates.length === 0) {
        setMonitorSearchSummary(
          `${contentType === 'ebook' ? 'eBook' : 'Audiobook'} search: No candidates to search (all have files or none monitored).`
        );
        return;
      }

      // Convert to Book format for triggerReleaseSearch
      const candidateBooks: Book[] = candidates.map((row) => ({
        id: `${row.provider || 'unknown'}:${row.provider_book_id || row.id}`,
        title: row.title,
        author: row.authors || '',
        year: row.publish_year != null ? String(row.publish_year) : undefined,
        release_date: row.release_date || undefined,
        preview: row.cover_url || undefined,
        isbn_13: row.isbn_13 || undefined,
        provider: row.provider || undefined,
        provider_id: row.provider_book_id || undefined,
        series_name: row.series_name || undefined,
        series_position: row.series_position != null ? row.series_position : undefined,
        series_count: row.series_count != null ? row.series_count : undefined,
      }));

      // Process using batch auto-download (hooks into activity sidebar)
      const batchId = `monitored:${monitoredEntityId}:${contentType}:${Date.now()}`;
      const batchTotal = candidateBooks.length;

      for (let idx = 0; idx < candidateBooks.length; idx += 1) {
        const book = candidateBooks[idx];
        await triggerReleaseSearch(book, contentType, 'auto_search_download', {
          suppressPerBookAutoSearchToasts: true,
          batchAutoDownload: {
            batchId,
            index: idx + 1,
            total: batchTotal,
            contentType,
          },
        });
      }

      // Refresh files after batch completes
      const updatedFilesResp = await listMonitoredBookFiles(monitoredEntityId);
      setFiles(updatedFilesResp.files || []);
      setRefreshKey((k) => k + 1);
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Monitored search failed';
      setFilesError(message);
    } finally {
      setMonitorSearchBusyByType((prev) => ({ ...prev, [contentType]: false }));
    }
  }, [monitoredEntityId, onGetReleases, monitoredBookRows, triggerReleaseSearch]);

  useEffect(() => {
    if (!monitoredEntityId || !status || autoRefreshBusy) {
      return;
    }

    const authorBookKeys = new Set(books.flatMap((b) => buildBookStatusKeys(b)));

    if (authorBookKeys.size === 0) {
      return;
    }

    const completedEntries = status.complete ? Object.entries(status.complete) : [];
    const relevantCompleted = completedEntries.filter(([, b]) => {
      const keys = buildBookStatusKeys(b);
      return keys.some((k) => authorBookKeys.has(k));
    });

    if (relevantCompleted.length === 0) {
      return;
    }

    const completionSignature = relevantCompleted
      .map(([recordKey, b]) => {
        const keyPart = buildBookStatusKeys(b).sort().join(',');
        const ts = typeof b.added_time === 'number' ? b.added_time : 0;
        return `${recordKey}:${ts}:${keyPart}`;
      })
      .sort()
      .join('|');

    if (!completionSignature || completionSignature === lastAutoRefreshSignatureRef.value) {
      return;
    }

    lastAutoRefreshSignatureRef.value = completionSignature;

    setAutoRefreshBusy(true);
    void (async () => {
      try {
        await scanMonitoredEntityFiles(monitoredEntityId);
        const resp = await listMonitoredBookFiles(monitoredEntityId);
        setFiles(resp.files || []);
      } catch (e) {
        const message = e instanceof Error ? e.message : 'Auto refresh failed';
        console.warn('AuthorModal: auto refresh after download complete failed', message);
      } finally {
        setAutoRefreshBusy(false);
      }
    })();
  }, [monitoredEntityId, status, autoRefreshBusy, books, lastAutoRefreshSignatureRef]);

  useEffect(() => {
    try {
      localStorage.setItem('authorBooksSort', booksSort);
    } catch {
      // ignore
    }
  }, [booksSort]);

  useEffect(() => {
    try {
      localStorage.setItem('authorBooksViewMode', booksViewMode);
    } catch {
      // ignore
    }
  }, [booksViewMode]);

  useEffect(() => {
    try {
      localStorage.setItem('authorBooksCompactMinWidth', String(booksCompactMinWidth));
    } catch {
      // ignore
    }
  }, [booksCompactMinWidth]);

  useEffect(() => {
    if (!author) {
      setDetails(null);
      setSupportsDetails(null);
      setDetailsError(null);
      setIsLoadingDetails(false);
      setShowMoreDetails(false);
      setBooks([]);
      setBooksError(null);
      setIsLoadingBooks(false);
      setIsEditModalOpen(false);
      return;
    }

    let isCancelled = false;

    const load = async () => {
      setDetails(null);
      setSupportsDetails(null);
      setDetailsError(null);
      setIsLoadingDetails(true);

      try {
        if (author.provider && author.provider_id) {
          const res: MetadataAuthorDetailsResult = await getMetadataAuthorInfo(author.provider, author.provider_id);
          if (isCancelled) return;
          setSupportsDetails(res.supportsAuthors);
          setDetails(res.author);
        } else {
          setSupportsDetails(false);
          setDetails(null);
        }
      } catch (e) {
        if (isCancelled) return;
        const message = e instanceof Error ? e.message : 'Failed to load author details';
        setDetailsError(message);
        setSupportsDetails(false);
      } finally {
        if (!isCancelled) {
          setIsLoadingDetails(false);
        }
      }
    };

    void load();

    return () => {
      isCancelled = true;
    };
  }, [author]);

  useEffect(() => {
    if (!author) {
      return;
    }

    let isCancelled = false;

    const monitoredBookToBook = (row: MonitoredBookRow): Book => ({
      id: `${row.provider || 'unknown'}:${row.provider_book_id || row.id}`,
      title: row.title,
      author: row.authors || '',
      year: row.publish_year != null ? String(row.publish_year) : undefined,
      release_date: row.release_date || undefined,
      preview: row.cover_url || undefined,
      isbn_13: row.isbn_13 || undefined,
      provider: row.provider || undefined,
      provider_id: row.provider_book_id || undefined,
      series_name: row.series_name || undefined,
      series_position: row.series_position != null ? row.series_position : undefined,
      series_count: row.series_count != null ? row.series_count : undefined,
      display_fields: [
        ...(typeof row.release_date === 'string' && row.release_date.trim()
          ? [{
              label: 'Release Date',
              value: row.release_date.trim(),
            }]
          : []),
        ...(typeof row.rating === 'number'
          ? [{
              label: 'Rating',
              value: `${row.rating.toFixed(1)}${typeof row.ratings_count === 'number' ? ` (${row.ratings_count.toLocaleString()})` : ''}`,
              icon: 'star',
            }]
          : []),
        ...(typeof row.readers_count === 'number'
          ? [{
              label: 'Readers',
              value: row.readers_count.toLocaleString(),
              icon: 'users',
            }]
          : []),
      ],
    });

    const enrichSeriesInfo = async (allBooks: Book[]): Promise<Array<{ provider: string; provider_book_id: string; series_name: string; series_position?: number; series_count?: number }>> => {
      const candidates = allBooks
        .filter((book) => Boolean(book.provider && book.provider_id))
        .filter((book) => !(book.series_name && book.series_position != null));

      const maxEnrich = 40;
      const batchSize = 5;
      const toEnrich = candidates.slice(0, maxEnrich);

      if (toEnrich.length === 0) return [];

      const enriched: Array<Book | null> = [];

      for (let i = 0; i < toEnrich.length; i += batchSize) {
        const batch = toEnrich.slice(i, i + batchSize);
        const batchResults = await Promise.all(
          batch.map(async (book) => {
            try {
              if (!book.provider || !book.provider_id) return null;
              return await getMetadataBookInfo(book.provider, book.provider_id);
            } catch {
              return null;
            }
          })
        );
        enriched.push(...batchResults);
        if (isCancelled) return [];
      }

      const byId = new Map(enriched.filter((b): b is Book => Boolean(b)).map((b) => [b.id, b]));

      const seriesUpdates: Array<{ provider: string; provider_book_id: string; series_name: string; series_position?: number; series_count?: number }> = [];

      if (byId.size > 0) {
        setBooks((current) =>
          current.map((book) => {
            const update = byId.get(book.id);
            if (!update) return book;
            if (book.series_name && book.series_position != null) return book;
            if (update.series_name && update.provider && update.provider_id) {
              seriesUpdates.push({
                provider: update.provider,
                provider_book_id: update.provider_id,
                series_name: update.series_name,
                series_position: update.series_position,
                series_count: update.series_count,
              });
            }
            return {
              ...book,
              series_name: update.series_name,
              series_position: update.series_position,
              series_count: update.series_count,
            };
          })
        );
      }

      return seriesUpdates;
    };

    const forceRefresh = refreshKey > 0;

    const fetchFromProvider = async (authorName: string, cachedBooks: Book[]): Promise<void> => {
      const hasCachedDisplay = cachedBooks.length > 0;
      if (hasCachedDisplay) setIsRefreshing(true);

      const limit = 40;
      const maxPages = 12;
      const maxBooks = 500;

      let page = 1;
      let hasMore = true;
      const allFreshBooks: Book[] = [];

      while (hasMore && page <= maxPages && allFreshBooks.length < maxBooks) {
        const result = await searchMetadata('', limit, 'relevance', { author: authorName }, page, 'ebook');
        if (isCancelled) return;

        allFreshBooks.push(...result.books);

        if (!hasCachedDisplay) {
          setBooks([...allFreshBooks]);
        }

        hasMore = result.hasMore;
        page += 1;
      }

      setIsLoadingBooks(false);

      const seriesUpdates = await enrichSeriesInfo(allFreshBooks);
      if (isCancelled) return;

      const providerSeriesUpdates = allFreshBooks
        .filter((book) => Boolean(book.provider && book.provider_id && (book.series_name || '').trim()))
        .map((book) => ({
          provider: String(book.provider),
          provider_book_id: String(book.provider_id),
          series_name: String(book.series_name).trim(),
          series_position: book.series_position,
          series_count: book.series_count,
        }));

      const mergedSeriesUpdatesByKey = new Map<string, {
        provider: string;
        provider_book_id: string;
        series_name: string;
        series_position?: number;
        series_count?: number;
      }>();

      for (const update of providerSeriesUpdates) {
        mergedSeriesUpdatesByKey.set(`${update.provider}:${update.provider_book_id}`, update);
      }
      for (const update of seriesUpdates) {
        mergedSeriesUpdatesByKey.set(`${update.provider}:${update.provider_book_id}`, update);
      }

      const mergedSeriesUpdates = Array.from(mergedSeriesUpdatesByKey.values());

      const freshById = new Map(allFreshBooks.map((book) => [book.id, book]));

      if (!hasCachedDisplay) {
        setBooks((current) => current.map((book) => {
          const fresh = freshById.get(book.id);
          if (!fresh) return book;
          return {
            ...book,
            ...fresh,
            series_name: book.series_name || fresh.series_name,
            series_position: book.series_position ?? fresh.series_position,
            series_count: book.series_count ?? fresh.series_count,
          };
        }));
      }

      // Single merge after all provider data + series enrichment is done.
      if (hasCachedDisplay) {
        setBooks((current) => {
          const cachedById = new Map(cachedBooks.map((b) => [b.id, b]));
          const currentById = new Map(current.map((b) => [b.id, b]));
          const merged: Book[] = [];
          const seen = new Set<string>();

          for (const book of current) {
            seen.add(book.id);
            const cached = cachedById.get(book.id);
            const fresh = freshById.get(book.id);
            merged.push({
              ...book,
              ...fresh,
              series_name: book.series_name || fresh?.series_name || cached?.series_name,
              series_position: book.series_position ?? fresh?.series_position ?? cached?.series_position,
              series_count: book.series_count ?? fresh?.series_count ?? cached?.series_count,
            });
          }

          for (const book of cachedBooks) {
            if (!seen.has(book.id)) {
              const cur = currentById.get(book.id);
              const fresh = freshById.get(book.id);
              merged.push(cur || fresh || book);
            }
          }

          for (const fresh of allFreshBooks) {
            if (!seen.has(fresh.id)) {
              seen.add(fresh.id);
              merged.push(fresh);
            }
          }

          return merged;
        });
        setIsRefreshing(false);
      }

      if (monitoredEntityId) {
        try {
          await syncMonitoredEntity(monitoredEntityId);
          if (mergedSeriesUpdates.length > 0) {
            const result = await updateMonitoredBooksSeries(monitoredEntityId, mergedSeriesUpdates);
            if ((result.updated || 0) < mergedSeriesUpdates.length) {
              console.warn(
                'AuthorModal: partial series metadata persistence',
                {
                  monitoredEntityId,
                  attempted: mergedSeriesUpdates.length,
                  updated: result.updated,
                }
              );
            }
          }
        } catch (error) {
          const message = error instanceof Error ? error.message : String(error);
          console.warn('AuthorModal: failed to persist monitored sync/series metadata', {
            monitoredEntityId,
            attemptedSeriesUpdates: mergedSeriesUpdates.length,
            error: message,
          });
        }
      }
    };

    const loadBooks = async () => {
      setBooks([]);
      setBooksError(null);
      setIsLoadingBooks(true);
      setIsRefreshing(false);

      try {
        let cachedBooks: Book[] = [];
        let skipProviderRefresh = false;

        if (monitoredEntityId) {
          try {
            const resp: MonitoredBooksResponse = await listMonitoredBooks(monitoredEntityId);
            if (isCancelled) return;
            if (resp.books.length > 0) {
              setMonitoredBookRows(resp.books);
              cachedBooks = resp.books.map(monitoredBookToBook);
              setBooks(cachedBooks);
              setIsLoadingBooks(false);

              const cachedHasSeriesGrouping = cachedBooks.some((book) => {
                const seriesName = (book.series_name || '').trim();
                return seriesName.length > 0;
              });

              // Scheduled backend refresh keeps monitored author data fresh.
              // On open, prefer cached DB rows when grouping metadata is already present.
              // If cache lacks series data, fetch provider to avoid showing everything as standalone.
              if (!forceRefresh && cachedHasSeriesGrouping) {
                skipProviderRefresh = true;
              }
            }
          } catch {
            // Cache miss is fine, continue to provider fetch
          }
        }

        if (!skipProviderRefresh) {
          await fetchFromProvider(author.name, cachedBooks);
        }
      } catch (e) {
        if (isCancelled) return;
        const message = e instanceof Error ? e.message : 'Failed to load books';
        setBooksError(message);
      } finally {
        if (!isCancelled) {
          setIsLoadingBooks(false);
          setIsRefreshing(false);
        }
      }
    };

    void loadBooks();

    return () => {
      isCancelled = true;
    };
  }, [author, monitoredEntityId, refreshKey]);

  const titleId = useMemo(() => {
    if (!author) return '';
    const key = author.provider && author.provider_id ? `${author.provider}-${author.provider_id}` : author.name;
    return `author-details-title-${key.replace(/[^a-zA-Z0-9_-]/g, '')}`;
  }, [author]);

  const groupedBooks = useMemo(() => {
    const parseYear = (value?: string) => {
      const n = value ? Number.parseInt(value, 10) : Number.NaN;
      return Number.isFinite(n) ? n : null;
    };

    const withinGroupSort = (a: Book, b: Book) => {
      if (booksSort === 'title_asc') {
        return (a.title || '').localeCompare(b.title || '');
      }

      if (booksSort === 'popular' || booksSort === 'rating') {
        const aPopularity = extractBookPopularity(a);
        const bPopularity = extractBookPopularity(b);

        if (booksSort === 'popular') {
          const aReaders = aPopularity.readersCount ?? -1;
          const bReaders = bPopularity.readersCount ?? -1;
          if (bReaders !== aReaders) return bReaders - aReaders;

          const aRatingsCount = aPopularity.ratingsCount ?? -1;
          const bRatingsCount = bPopularity.ratingsCount ?? -1;
          if (bRatingsCount !== aRatingsCount) return bRatingsCount - aRatingsCount;

          const aRating = aPopularity.rating ?? -1;
          const bRating = bPopularity.rating ?? -1;
          if (bRating !== aRating) return bRating - aRating;

          return (a.title || '').localeCompare(b.title || '');
        }

        const aRating = aPopularity.rating ?? -1;
        const bRating = bPopularity.rating ?? -1;
        if (bRating !== aRating) return bRating - aRating;

        const aRatingsCount = aPopularity.ratingsCount ?? -1;
        const bRatingsCount = bPopularity.ratingsCount ?? -1;
        if (bRatingsCount !== aRatingsCount) return bRatingsCount - aRatingsCount;

        const aReaders = aPopularity.readersCount ?? -1;
        const bReaders = bPopularity.readersCount ?? -1;
        if (bReaders !== aReaders) return bReaders - aReaders;

        return (a.title || '').localeCompare(b.title || '');
      }

      if (booksSort === 'series_asc' || booksSort === 'series_desc') {
        const aPos = a.series_position ?? Number.POSITIVE_INFINITY;
        const bPos = b.series_position ?? Number.POSITIVE_INFINITY;
        if (aPos !== bPos) return aPos - bPos;
        const ay = parseYear(a.year);
        const by = parseYear(b.year);
        if (ay != null && by != null && ay !== by) return ay - by;
        return (a.title || '').localeCompare(b.title || '');
      }

      const ay = parseYear(a.year);
      const by = parseYear(b.year);
      if (ay == null && by == null) return (a.title || '').localeCompare(b.title || '');
      if (ay == null) return 1;
      if (by == null) return -1;
      if (booksSort === 'year_asc') return ay - by;
      return by - ay;
    };

    // Group by year when sorting by year
    if (booksSort === 'year_desc' || booksSort === 'year_asc') {
      const yearMap = new Map<string, Book[]>();
      for (const b of books) {
        const year = parseYear(b.year);
        const key = year != null ? String(year) : '__unknown__';
        const list = yearMap.get(key);
        if (list) list.push(b);
        else yearMap.set(key, [b]);
      }

      const unknown = yearMap.get('__unknown__') ?? [];
      yearMap.delete('__unknown__');

      const years = Array.from(yearMap.keys()).sort((a, b) => 
        booksSort === 'year_asc' ? Number(a) - Number(b) : Number(b) - Number(a)
      );

      const groups = years.map((year) => {
        const yearBooks = [...(yearMap.get(year) ?? [])];
        yearBooks.sort(withinGroupSort);
        return { key: year, title: year, books: yearBooks };
      });

      if (unknown.length > 0) {
        unknown.sort(withinGroupSort);
        groups.push({ key: '__unknown__', title: 'Unknown Year', books: unknown });
      }

      return groups;
    }

    // Group by series when sorting by series
    if (booksSort === 'series_asc' || booksSort === 'series_desc') {
      const groupMap = new Map<string, Book[]>();
      for (const b of books) {
        const key = b.series_name || '__standalone__';
        const list = groupMap.get(key);
        if (list) list.push(b);
        else groupMap.set(key, [b]);
      }

      const standalone = groupMap.get('__standalone__') ?? [];
      groupMap.delete('__standalone__');

      const seriesNames = Array.from(groupMap.keys());
      seriesNames.sort((a, b) => a.localeCompare(b));
      if (booksSort === 'series_desc') {
        seriesNames.reverse();
      }

      const groups = seriesNames.map((name) => {
        const seriesBooks = [...(groupMap.get(name) ?? [])];
        seriesBooks.sort(withinGroupSort);
        return { key: name, title: name, books: seriesBooks };
      });

      const standaloneBooks = [...standalone];
      standaloneBooks.sort(withinGroupSort);

      if (standaloneBooks.length > 0) {
        groups.push({ key: '__standalone__', title: 'Standalone', books: standaloneBooks });
      }

      return groups;
    }

    // No grouping for other sort modes (title, popular, rating)
    const sorted = [...books].sort(withinGroupSort);
    return [{ key: '__all__', title: 'All Books', books: sorted }];
  }, [books, booksSort]);

  const seriesFilterOptions = useMemo(() => {
    return groupedBooks
      .filter((group) => group.key !== '__standalone__')
      .map((group) => ({ key: group.key, title: group.title, count: group.books.length }));
  }, [groupedBooks]);

  useEffect(() => {
    const allowed = new Set(seriesFilterOptions.map((option) => option.key));
    setBooksFilters((prev) => {
      if (prev.seriesKeys.length === 0) return prev;
      const nextSeriesKeys = prev.seriesKeys.filter((key) => allowed.has(key));
      if (nextSeriesKeys.length === prev.seriesKeys.length) {
        return prev;
      }
      return { ...prev, seriesKeys: nextSeriesKeys };
    });
  }, [seriesFilterOptions]);

  const matchedFileTypesByBookKey = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const f of files) {
      const prov = typeof f.provider === 'string' ? f.provider : '';
      const bid = typeof f.provider_book_id === 'string' ? f.provider_book_id : '';
      if (!prov || !bid) continue;
      const key = `${prov}:${bid}`;
      const t = typeof f.file_type === 'string' ? f.file_type.trim().toLowerCase() : '';
      if (!t) continue;
      let set = map.get(key);
      if (!set) {
        set = new Set<string>();
        map.set(key, set);
      }
      set.add(t);
    }
    return map;
  }, [files]);

  const activeBookFiles = useMemo(() => {
    if (!activeBookDetails) return [];
    const prov = activeBookDetails.provider || '';
    const bid = activeBookDetails.provider_id || '';
    if (!prov || !bid) return [];
    return files.filter((f) => f.provider === prov && f.provider_book_id === bid);
  }, [activeBookDetails, files]);

  const filteredGroupedBooks = useMemo(() => {
    const q = activeBooksQuery.trim().toLowerCase();

    const now = new Date();
    const todayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const currentYear = todayStart.getFullYear();
    const selectedSeries = new Set(booksFilters.seriesKeys);

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
          if (booksFilters.upcomingWindow === 'this_year') {
            return parsed.yearOnly === currentYear;
          }
          if (booksFilters.upcomingWindow === '30d' || booksFilters.upcomingWindow === '90d') {
            return false;
          }
          return true;
        }
      }

      const fallbackYear = book.year ? Number.parseInt(book.year, 10) : Number.NaN;
      if (!Number.isFinite(fallbackYear)) return false;
      if (booksFilters.upcomingWindow === 'this_year') return fallbackYear === currentYear;
      if (booksFilters.upcomingWindow === '30d' || booksFilters.upcomingWindow === '90d') return false;
      return fallbackYear >= currentYear;
    };

    const passesNoReleaseDateFilter = (book: Book): boolean => {
      if (!booksFilters.showNoReleaseDate) return true;
      const releaseDateRaw = extractReleaseDateCandidate(book);
      return !releaseDateRaw;
    };

    const passesAvailabilityFilter = (book: Book): boolean => {
      if (booksFilters.availability === 'all') return true;
      const provider = book.provider || '';
      const providerId = book.provider_id || '';
      const key = provider && providerId ? `${provider}:${providerId}` : '';
      const types = key ? matchedFileTypesByBookKey.get(key) : undefined;
      const hasAny = Boolean(types && types.size > 0);
      const hasEbook = Boolean(types && EBOOK_MATCH_FORMATS.some((format) => types.has(format)));
      const hasAudiobook = Boolean(types && AUDIOBOOK_MATCH_FORMATS.some((format) => types.has(format)));

      switch (booksFilters.availability) {
        case 'missing_any':
          return !hasAny;
        case 'missing_ebook':
          return !hasEbook;
        case 'missing_audiobook':
          return !hasAudiobook;
        case 'available_any':
          return hasAny;
        case 'ebook_available':
          return hasEbook;
        case 'audiobook_available':
          return hasAudiobook;
        case 'both_available':
          return hasEbook && hasAudiobook;
        default:
          return true;
      }
    };

    return groupedBooks
      .map((g) => {
        if (selectedSeries.size > 0 && !selectedSeries.has(g.key)) {
          return null;
        }

        const booksPassingFilters = g.books.filter((book) => (
          passesAvailabilityFilter(book) && passesUpcomingFilter(book) && passesNoReleaseDateFilter(book)
        ));
        if (booksPassingFilters.length === 0) return null;

        if (!q) {
          return { ...g, books: booksPassingFilters };
        }

        const titleMatch = (g.title || '').toLowerCase().includes(q);
        if (titleMatch) return { ...g, books: booksPassingFilters };
        const matching = booksPassingFilters.filter((b) => (b.title || '').toLowerCase().includes(q));
        if (matching.length === 0) return null;
        return { ...g, books: matching };
      })
      .filter((g): g is { key: string; title: string; books: Book[] } => g != null);
  }, [groupedBooks, activeBooksQuery, booksFilters, matchedFileTypesByBookKey]);

  const activeFiltersCount = useMemo(() => {
    let count = booksFilters.availability !== 'all' ? 1 : 0;
    if (booksFilters.showUpcoming) count += 1;
    if (booksFilters.showNoReleaseDate) count += 1;
    if (booksFilters.seriesKeys.length > 0) count += 1;
    return count;
  }, [booksFilters]);

  const singleActiveFilterLabel = useMemo(() => {
    const labels: string[] = [];

    if (booksFilters.availability !== 'all') {
      labels.push(AVAILABILITY_FILTER_LABELS[booksFilters.availability]);
    }

    if (booksFilters.showUpcoming) {
      labels.push(booksFilters.upcomingWindow === 'any' ? 'Upcoming' : UPCOMING_WINDOW_LABELS[booksFilters.upcomingWindow]);
    }

    if (booksFilters.showNoReleaseDate) {
      labels.push('No release date');
    }

    if (booksFilters.seriesKeys.length > 0) {
      if (booksFilters.seriesKeys.length === 1) {
        const selected = seriesFilterOptions.find((option) => option.key === booksFilters.seriesKeys[0]);
        labels.push(selected?.title || 'Series');
      } else {
        labels.push('Series');
      }
    }

    return labels.length === 1 ? labels[0] : null;
  }, [booksFilters, seriesFilterOptions]);

  const activeFilterChips = useMemo(() => {
    const chips: Array<{ key: string; label: string }> = [];

    if (booksFilters.availability !== 'all') {
      chips.push({ key: 'availability', label: AVAILABILITY_FILTER_LABELS[booksFilters.availability] });
    }
    if (booksFilters.showUpcoming) {
      chips.push({
        key: 'upcoming',
        label: booksFilters.upcomingWindow === 'any'
          ? 'Upcoming'
          : `Upcoming · ${UPCOMING_WINDOW_LABELS[booksFilters.upcomingWindow]}`,
      });
    }
    if (booksFilters.showNoReleaseDate) {
      chips.push({ key: 'no_release_date', label: 'No release date' });
    }
    for (const seriesKey of booksFilters.seriesKeys) {
      const selected = seriesFilterOptions.find((option) => option.key === seriesKey);
      chips.push({ key: `series:${seriesKey}`, label: selected?.title || 'Series' });
    }

    return chips;
  }, [booksFilters, seriesFilterOptions]);

  const visibleBooks = useMemo(() => {
    return filteredGroupedBooks.flatMap((group) => group.books);
  }, [filteredGroupedBooks]);

  const allVisibleBooksSelected = useMemo(() => {
    return visibleBooks.length > 0 && visibleBooks.every((book) => Boolean(selectedBookIds[book.id]));
  }, [visibleBooks, selectedBookIds]);

  const selectedBooks = useMemo(() => {
    return books.filter((book) => Boolean(selectedBookIds[book.id]));
  }, [books, selectedBookIds]);

  const hasActiveBookSelection = selectedBooks.length > 0;

  const toggleSeriesFilter = useCallback((seriesKey: string) => {
    setBooksFilters((prev) => {
      const exists = prev.seriesKeys.includes(seriesKey);
      return {
        ...prev,
        seriesKeys: exists
          ? prev.seriesKeys.filter((key) => key !== seriesKey)
          : [...prev.seriesKeys, seriesKey],
      };
    });
  }, []);

  const allGroupsCollapsed = useMemo(() => {
    if (groupedBooks.length === 0) return false;
    return groupedBooks.every((g) => (collapsedGroups[g.key] ?? false) === true);
  }, [groupedBooks, collapsedGroups]);

  const toggleAllGroups = useCallback(() => {
    setCollapsedGroups((prev) => {
      const next: Record<string, boolean> = { ...prev };
      const shouldCollapse = !allGroupsCollapsed;
      for (const g of groupedBooks) {
        next[g.key] = shouldCollapse;
      }
      return next;
    });
  }, [allGroupsCollapsed, groupedBooks]);

  const toggleGroupCollapsed = useCallback((key: string) => {
    setCollapsedGroups((prev) => ({
      ...prev,
      [key]: !(prev[key] ?? false),
    }));
  }, []);

  useEffect(() => {
    setSelectedBookIds((prev) => {
      const next: Record<string, boolean> = {};
      let changed = false;

      for (const book of books) {
        if (prev[book.id]) {
          next[book.id] = true;
        }
      }

      const prevKeys = Object.keys(prev);
      const nextKeys = Object.keys(next);
      if (prevKeys.length !== nextKeys.length) {
        changed = true;
      } else {
        for (const key of prevKeys) {
          if (!next[key]) {
            changed = true;
            break;
          }
        }
      }

      return changed ? next : prev;
    });
  }, [books]);

  const toggleBookSelection = useCallback((bookId: string) => {
    setSelectedBookIds((prev) => {
      const next = { ...prev };
      if (next[bookId]) {
        delete next[bookId];
      } else {
        next[bookId] = true;
      }
      return next;
    });
  }, []);

  const toggleSelectAllVisibleBooks = useCallback(() => {
    if (visibleBooks.length === 0) return;
    setSelectedBookIds((prev) => {
      const allSelected = visibleBooks.every((book) => Boolean(prev[book.id]));
      const next = { ...prev };
      if (allSelected) {
        for (const book of visibleBooks) {
          delete next[book.id];
        }
      } else {
        for (const book of visibleBooks) {
          next[book.id] = true;
        }
      }
      return next;
    });
  }, [visibleBooks]);

  const runBulkDownloadForSelection = useCallback(async (contentType: ContentType) => {
    if (!onGetReleases || selectedBooks.length === 0 || bulkDownloadRunningByType[contentType]) {
      return;
    }

    const batchId = `${contentType}:${Date.now()}`;
    const batchTotal = selectedBooks.length;

    setBulkDownloadRunningByType((prev) => ({
      ...prev,
      [contentType]: true,
    }));

    try {
      for (let idx = 0; idx < selectedBooks.length; idx += 1) {
        const book = selectedBooks[idx];
        await triggerReleaseSearch(book, contentType, 'auto_search_download', {
          suppressPerBookAutoSearchToasts: true,
          batchAutoDownload: {
            batchId,
            index: idx + 1,
            total: batchTotal,
            contentType,
          },
        });
      }
    } finally {
      setBulkDownloadRunningByType((prev) => ({
        ...prev,
        [contentType]: false,
      }));
    }
  }, [onGetReleases, selectedBooks, bulkDownloadRunningByType, triggerReleaseSearch]);

  const toggleSelectAllInGroup = useCallback((groupBooks: Book[]) => {
    if (groupBooks.length === 0) return;
    setSelectedBookIds((prev) => {
      const allSelected = groupBooks.every((book) => Boolean(prev[book.id]));
      const next = { ...prev };
      if (allSelected) {
        for (const book of groupBooks) {
          delete next[book.id];
        }
      } else {
        for (const book of groupBooks) {
          next[book.id] = true;
        }
      }
      return next;
    });
  }, []);

  const getDefaultBookSearchMode = useCallback(() => {
    const defaultContentType: ContentType = defaultReleaseContentType === 'audiobook' ? 'audiobook' : 'ebook';
    const defaultAction = resolvePrimaryActionForContentType(defaultContentType);
    const isAutoDefault = defaultAction === 'auto_search_download';
    const primaryLabel = defaultContentType === 'audiobook'
      ? isAutoDefault
        ? 'Auto search + download audiobooks'
        : 'Interactive search audiobooks'
      : isAutoDefault
        ? 'Auto search + download eBooks'
        : 'Interactive search eBooks';

    return {
      defaultContentType,
      defaultAction,
      isAutoDefault,
      primaryLabel,
    };
  }, [defaultReleaseContentType, resolvePrimaryActionForContentType]);

  const getBookMonitorState = useCallback((book: Book): { monitorEbook: boolean; monitorAudiobook: boolean } => {
    const provider = (book.provider || '').trim();
    const providerId = (book.provider_id || '').trim();
    if (!provider || !providerId) {
      return { monitorEbook: true, monitorAudiobook: true };
    }
    const row = monitoredBookRows.find(
      (r) => r.provider === provider && r.provider_book_id === providerId
    );
    if (!row) {
      return { monitorEbook: true, monitorAudiobook: true };
    }
    return {
      monitorEbook: row.monitor_ebook === true || row.monitor_ebook === 1,
      monitorAudiobook: row.monitor_audiobook === true || row.monitor_audiobook === 1,
    };
  }, [monitoredBookRows]);

  const toggleBookMonitor = useCallback(async (
    book: Book,
    type: 'ebook' | 'audiobook' | 'both',
    newValue?: boolean
  ) => {
    if (!monitoredEntityId) return;
    const provider = (book.provider || '').trim();
    const providerId = (book.provider_id || '').trim();
    if (!provider || !providerId) return;

    const current = getBookMonitorState(book);
    const patch: { provider: string; provider_book_id: string; monitor_ebook?: boolean; monitor_audiobook?: boolean } = {
      provider,
      provider_book_id: providerId,
    };

    if (type === 'ebook') {
      patch.monitor_ebook = newValue !== undefined ? newValue : !current.monitorEbook;
    } else if (type === 'audiobook') {
      patch.monitor_audiobook = newValue !== undefined ? newValue : !current.monitorAudiobook;
    } else {
      const targetValue = newValue !== undefined ? newValue : !(current.monitorEbook && current.monitorAudiobook);
      patch.monitor_ebook = targetValue;
      patch.monitor_audiobook = targetValue;
    }

    // Optimistic update
    setMonitoredBookRows((prev) =>
      prev.map((r) =>
        r.provider === provider && r.provider_book_id === providerId
          ? {
              ...r,
              monitor_ebook: patch.monitor_ebook !== undefined ? patch.monitor_ebook : r.monitor_ebook,
              monitor_audiobook: patch.monitor_audiobook !== undefined ? patch.monitor_audiobook : r.monitor_audiobook,
            }
          : r
      )
    );

    try {
      await updateMonitoredBooksMonitorFlags(monitoredEntityId, patch);
    } catch (e) {
      // Revert on error
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
        <button
          type="button"
          onClick={() => {
            close();
            setActiveBookDetails(book);
          }}
          className="w-full px-3 py-2 text-left text-sm hover-surface"
        >
          View info
        </button>
        {onGetReleases ? (
          <>
            <div className="my-1 border-t border-[var(--border-muted)]" />
            {SEARCH_DROPDOWN_OPTIONS.map((option) => {
              const isDefault = option.contentType === defaultContentType && option.action === defaultAction;
              return (
                <button
                  type="button"
                  key={`${option.contentType}:${option.action}`}
                  onClick={() => {
                    close();
                    void triggerReleaseSearch(book, option.contentType, option.action);
                  }}
                  className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${isDefault ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''}`}
                >
                  <span>{option.label}</span>
                  {isDefault ? <span className="text-[10px] uppercase tracking-wide opacity-80">Default</span> : null}
                </button>
              );
            })}
          </>
        ) : null}
        {book.source_url ? (
          <>
            <div className="my-1 border-t border-[var(--border-muted)]" />
            <a
              href={book.source_url}
              target="_blank"
              rel="noreferrer"
              className="block w-full px-3 py-2 text-left text-sm hover-surface"
              onClick={() => close()}
            >
              View source
            </a>
          </>
        ) : null}
        {monitoredEntityId ? (
          <>
            <div className="my-1 border-t border-[var(--border-muted)]" />
            <div className="px-3 py-1.5 text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
              Monitoring
            </div>
            <button
              type="button"
              onClick={() => {
                void toggleBookMonitor(book, 'both');
              }}
              className="w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between"
            >
              <span>Monitor Both</span>
              {isFullyMonitored ? (
                <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              ) : null}
            </button>
            <button
              type="button"
              onClick={() => {
                void toggleBookMonitor(book, 'ebook');
              }}
              className="w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between"
            >
              <span>Monitor eBook</span>
              {monitorState.monitorEbook ? (
                <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              ) : null}
            </button>
            <button
              type="button"
              onClick={() => {
                void toggleBookMonitor(book, 'audiobook');
              }}
              className="w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between"
            >
              <span>Monitor Audiobook</span>
              {monitorState.monitorAudiobook ? (
                <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              ) : null}
            </button>
          </>
        ) : null}
      </div>
    );
  }, [onGetReleases, triggerReleaseSearch, monitoredEntityId, getBookMonitorState, toggleBookMonitor]);

  const renderBookOverflowMenu = (book: Book) => {
    const { defaultContentType, defaultAction } = getDefaultBookSearchMode();

    return (
      <Dropdown
        widthClassName="w-auto"
        align="right"
        panelClassName="z-[2200] min-w-[250px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
        renderTrigger={({ isOpen, toggle }) => (
          <button
            type="button"
            onClick={toggle}
            className={`inline-flex items-center justify-center rounded-full text-gray-600 dark:text-gray-200 hover-action transition-colors h-6 w-6 ${isOpen ? 'text-gray-900 dark:text-gray-100' : ''}`}
            aria-label={`More actions for ${book.title || 'this book'}`}
            title="More actions"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM12 12.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM12 18.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Z" />
            </svg>
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
        <button
          type="button"
          onClick={() => {
            if (!onGetReleases) {
              setActiveBookDetails(book);
              return;
            }
            void triggerReleaseSearch(book, defaultContentType, defaultAction);
          }}
          className="inline-flex items-center justify-center h-8 w-8 text-emerald-600 dark:text-emerald-400 hover-action"
          aria-label={`${primaryLabel} for ${book.title || 'this book'}`}
          title={primaryLabel}
        >
          <span className="relative inline-flex items-center justify-center">
            {defaultContentType === 'audiobook' ? (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12a7.5 7.5 0 1 1 15 0v6a1.5 1.5 0 0 1-1.5 1.5h-.75A2.25 2.25 0 0 1 15 17.25v-3A2.25 2.25 0 0 1 17.25 12h2.25m-15 0H6.75A2.25 2.25 0 0 1 9 14.25v3A2.25 2.25 0 0 1 6.75 19.5H6A1.5 1.5 0 0 1 4.5 18v-6Z" />
              </svg>
            ) : (
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 6.75A2.25 2.25 0 0 1 6.75 4.5h4.5A2.25 2.25 0 0 1 13.5 6.75v12A2.25 2.25 0 0 0 11.25 16.5h-4.5A2.25 2.25 0 0 0 4.5 18.75v-12Zm9 0A2.25 2.25 0 0 1 15.75 4.5h1.5A2.25 2.25 0 0 1 19.5 6.75v12a2.25 2.25 0 0 0-2.25-2.25h-1.5A2.25 2.25 0 0 0 13.5 18.75v-12Z" />
              </svg>
            )}
            {isAutoDefault ? (
              <svg className="w-2.5 h-2.5 absolute -right-1 -bottom-1" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 3v10.5m0 0 3-3m-3 3-3-3" />
              </svg>
            ) : (
              <svg className="w-2.5 h-2.5 absolute -right-1 -bottom-1" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35m1.35-5.15a5 5 0 1 1-10 0 5 5 0 0 1 10 0Z" />
              </svg>
            )}
          </span>
        </button>

        <Dropdown
          widthClassName="w-auto"
          align="right"
          panelClassName="z-[2200] min-w-[250px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
          renderTrigger={({ isOpen, toggle }) => (
            <button
              type="button"
              onClick={toggle}
              className={`inline-flex items-center justify-center h-8 w-7 border-l border-emerald-500/30 text-emerald-600 dark:text-emerald-400 hover-action ${isOpen ? 'bg-emerald-500/10' : ''}`}
              aria-label={`More actions for ${book.title || 'this book'}`}
              title="More actions"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" />
              </svg>
            </button>
          )}
        >
          {renderBookActionMenuContent(book, defaultContentType, defaultAction)}
        </Dropdown>
      </div>
    );
  };

  const fallbackAuthorPhotoFromPopularBook = useMemo(() => {
    let bestBook: Book | null = null;
    let bestReaders = -1;
    let bestRatingsCount = -1;
    let bestRating = -1;

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
        || (
          readers === bestReaders
          && ratingsCount === bestRatingsCount
          && rating === bestRating
          && (book.title || '').localeCompare(bestBook?.title || '') < 0
        );

      if (isBetter) {
        bestBook = book;
        bestReaders = readers;
        bestRatingsCount = ratingsCount;
        bestRating = rating;
      }
    }

    return typeof bestBook?.preview === 'string' ? bestBook.preview.trim() : null;
  }, [books]);

  if (!author && !isClosing) return null;
  if (!author) return null;

  const resolvedName = details?.name || author.name;
  const resolvedPhoto = details?.photo_url || author.photo_url || fallbackAuthorPhotoFromPopularBook || null;
  const resolvedBio = details?.bio || null;
  const resolvedUrl = details?.source_url || author.source_url || null;
  const providerLabel = details?.provider || author.provider || null;
  const booksCount = details?.stats?.books_count ?? null;

  return (
    <>
      <div
        className={isPageMode ? 'w-full' : 'modal-overlay active sm:px-6 sm:py-6'}
        style={!isPageMode && isEditModalOpen ? { pointerEvents: 'none' } : undefined}
        onClick={e => {
          if (!isPageMode && e.target === e.currentTarget) handleClose();
        }}
      >
        <div
          className={isPageMode ? 'w-full' : `details-container w-full max-w-5xl h-full sm:h-auto ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
          role={isPageMode ? 'region' : 'dialog'}
          aria-modal={isPageMode ? undefined : true}
          aria-labelledby={titleId}
        >
          <div className={isPageMode
            ? 'flex flex-col overflow-visible rounded-2xl border border-black/10 dark:border-white/10 bg-white/80 dark:bg-white/5 text-[var(--text)] shadow-xl'
            : 'flex h-full sm:h-[90vh] sm:max-h-[90vh] flex-col overflow-hidden rounded-none sm:rounded-2xl border-0 sm:border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-none sm:shadow-2xl'}>
            <header className={`flex items-start gap-4 px-5 py-4 ${isPageMode ? 'border-b border-black/10 dark:border-white/10 bg-transparent' : 'border-b border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)]'}`}>
              <div className="flex-1 min-w-0">
                {isPageMode ? (
                  <div className="flex items-center gap-2 min-w-0">
                    <button
                      type="button"
                      onClick={handleClose}
                      className="rounded-full p-1.5 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
                      aria-label="Back to monitored authors"
                    >
                      <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8} aria-hidden="true">
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 19.5 8.25 12l7.5-7.5" />
                      </svg>
                    </button>
                    <p className="text-base font-semibold tracking-tight text-gray-900 dark:text-gray-100 truncate">
                      <button
                        type="button"
                        onClick={handleClose}
                        className="hover:text-emerald-600 dark:hover:text-emerald-400 transition-colors"
                      >
                        Monitored Authors
                      </button>
                      <span className="mx-2 text-gray-500 dark:text-gray-400">/</span>
                      <span id={titleId}>{resolvedName || 'Unknown author'}</span>
                    </p>
                  </div>
                ) : (
                  <>
                    <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Author</p>
                    <h3 id={titleId} className="text-lg font-semibold leading-snug truncate">
                      {resolvedName || 'Unknown author'}
                    </h3>
                  </>
                )}
              </div>

              <div className="flex items-center gap-2 flex-shrink-0">
                {isPageMode ? (
                  <>
                    <div className="hidden sm:flex items-center gap-2 rounded-full border border-[var(--border-muted)] px-3 py-1.5 bg-white/70 dark:bg-white/10">
                      <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35m1.35-5.15a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z" />
                      </svg>
                      <input
                        value={activeBooksQuery}
                        onChange={(e) => updateBooksQuery(e.target.value)}
                        placeholder="Search monitored books"
                        className="w-44 bg-transparent outline-none text-xs text-gray-700 dark:text-gray-200 placeholder:text-gray-500"
                        aria-label="Search monitored books"
                      />
                      {activeBooksQuery ? (
                        <button
                          type="button"
                          onClick={() => updateBooksQuery('')}
                          className="p-0.5 rounded-full text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action"
                          aria-label="Clear search"
                          title="Clear"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                          </svg>
                        </button>
                      ) : null}
                    </div>
                    {monitoredEntityId ? (
                      <button
                        type="button"
                        onClick={() => setIsEditModalOpen(true)}
                        className="px-3 py-1 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                      >
                        Edit
                      </button>
                    ) : null}
                  </>
                ) : (
                  <button
                    type="button"
                    onClick={handleClose}
                    className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
                    aria-label="Close author details"
                  >
                    <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                    </svg>
                  </button>
                )}
              </div>
            </header>

            <div className="px-3 pb-3 sm:px-4 sm:pb-4">
              <div className="mt-4 rounded-2xl border border-[var(--border-muted)] bg-[var(--bg-soft)] sm:bg-[var(--bg)] p-4">
                <div className="flex items-start gap-4">
                  <div className="flex-shrink-0">
                    {resolvedPhoto ? (
                      <img
                        src={resolvedPhoto}
                        alt={resolvedName}
                        className="w-20 h-20 rounded-2xl object-cover object-top"
                        loading="lazy"
                      />
                    ) : (
                      <div className="w-20 h-20 rounded-2xl bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-[10px] font-medium text-gray-500 dark:text-gray-300">
                        No Photo
                      </div>
                    )}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="text-base font-semibold text-gray-900 dark:text-gray-100 truncate">{resolvedName || 'Unknown author'}</div>
                        <div className="mt-1 text-xs text-gray-500 dark:text-gray-400">
                          {booksCount != null ? `${booksCount} books` : 'Unknown'}
                          {providerLabel ? ` • ${providerLabel}` : ''}
                        </div>
                      </div>

                      <div className="flex items-center gap-2 flex-shrink-0">
                        {monitoredEntityId && !isPageMode ? (
                          <button
                            type="button"
                            onClick={() => setIsEditModalOpen(true)}
                            className="px-3 py-1 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                          >
                            Edit
                          </button>
                        ) : null}
                        {!isPageMode ? (
                          <>
                            <button
                              type="button"
                              onClick={() => window.location.assign(withBasePath(`/?q=${encodeURIComponent(author.name)}`))}
                              className="px-3 py-1 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                            >
                              Open in main search
                            </button>
                            <button
                              type="button"
                              onClick={() => setShowMoreDetails((v) => !v)}
                              className="px-3 py-1 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                            >
                              {showMoreDetails ? 'Hide details' : 'Show details'}
                            </button>
                          </>
                        ) : null}
                      </div>
                    </div>

                    {!showMoreDetails ? (
                      <div className="mt-2 min-h-[1.25rem]">
                        {resolvedBio ? (
                          <p className="text-xs text-gray-600 dark:text-gray-300 line-clamp-2">
                            {resolvedBio.length > 100 ? `${resolvedBio.slice(0, 100)}…` : resolvedBio}
                          </p>
                        ) : isLoadingDetails ? (
                          <p className="text-xs text-gray-500 dark:text-gray-500">Loading bio…</p>
                        ) : null}
                      </div>
                    ) : null}

                    {showMoreDetails ? (
                      <div className="mt-3 rounded-xl border border-[var(--border-muted)] bg-[var(--bg)]/40 px-3 py-2">
                        <div className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">Bio</div>
                        <div className="mt-1 text-sm">
                          {isLoadingDetails ? (
                            <p className="text-gray-600 dark:text-gray-300">Loading…</p>
                          ) : detailsError ? (
                            <p className="text-red-500">{detailsError}</p>
                          ) : resolvedBio ? (
                            <p className="text-gray-900 dark:text-gray-100 whitespace-pre-line">{resolvedBio}</p>
                          ) : supportsDetails ? (
                            <p className="text-gray-600 dark:text-gray-300">No bio available.</p>
                          ) : (
                            <p className="text-gray-600 dark:text-gray-300">Details not supported by this provider.</p>
                          )}
                        </div>
                        {isPageMode && resolvedUrl ? (
                          <div className="mt-2">
                            <a
                              href={resolvedUrl}
                              target="_blank"
                              rel="noreferrer"
                              className="inline-block text-xs text-gray-500 dark:text-gray-400 hover:underline"
                            >
                              View on provider ↗
                            </a>
                          </div>
                        ) : null}
                      </div>
                    ) : null}

                    <div className="mt-2 h-4">
                      {isPageMode ? (
                        <button
                          type="button"
                          onClick={() => setShowMoreDetails((v) => !v)}
                          className="inline-block text-xs text-gray-500 dark:text-gray-400 hover:underline"
                        >
                          {showMoreDetails ? 'Hide details ↗' : 'Show details ↘'}
                        </button>
                      ) : resolvedUrl ? (
                        <a
                          href={resolvedUrl}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-block text-xs text-gray-500 dark:text-gray-400 hover:underline"
                        >
                          View on provider ↗
                        </a>
                      ) : null}
                    </div>
                  </div>
                </div>
              </div>

              <div className="mt-4">
                <div
                  ref={booksToolbarRef}
                  className={`sticky z-40 bg-[var(--bg)] ${isPageMode ? 'top-[76px]' : 'top-0'} ${isBooksToolbarPinned ? 'rounded-none border-0 border-b border-[var(--border-muted)] -ml-[100vw] -mr-[100vw] px-[100vw]' : 'rounded-t-2xl border border-[var(--border-muted)] border-b-0'}`}
                >
                  <div className="flex items-center justify-between gap-3 px-4 py-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <button
                      type="button"
                      onClick={toggleAllGroups}
                      className="p-1.5 rounded-full text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action transition-all duration-200"
                      aria-label={allGroupsCollapsed ? 'Expand all series groups' : 'Collapse all series groups'}
                      title={allGroupsCollapsed ? 'Expand all' : 'Collapse all'}
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                    <p className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 truncate">
                      Books
                      {isRefreshing ? (
                        <span className="ml-2 text-[10px] text-gray-400 dark:text-gray-500">refreshing…</span>
                      ) : null}
                    </p>
                  </div>
                  <div className="flex items-center gap-2 flex-shrink-0">
                    {onGetReleases && selectedBooks.length > 0 ? (
                      <>
                        <button
                          type="button"
                          onClick={() => void runBulkDownloadForSelection('ebook')}
                          disabled={selectedBooks.length === 0 || bulkDownloadRunningByType.ebook}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border border-[var(--border-muted)] bg-white/70 dark:bg-white/10 hover-action disabled:opacity-40 disabled:cursor-not-allowed"
                          title="Automatically search/download eBooks for selected books"
                        >
                          <BookIcon className="w-3.5 h-3.5" />
                          Download selected
                        </button>
                        <button
                          type="button"
                          onClick={() => void runBulkDownloadForSelection('audiobook')}
                          disabled={selectedBooks.length === 0 || bulkDownloadRunningByType.audiobook}
                          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-medium border border-[var(--border-muted)] bg-white/70 dark:bg-white/10 hover-action disabled:opacity-40 disabled:cursor-not-allowed"
                          title="Automatically search/download audiobooks for selected books"
                        >
                          <AudiobookIcon className="w-3.5 h-3.5" />
                          Download selected
                        </button>
                      </>
                    ) : null}
                    {!isPageMode ? (
                      <div className="hidden sm:flex items-center gap-2 rounded-full px-2.5 py-1.5 border border-[var(--border-muted)]" style={{ background: 'var(--bg-soft)' }}>
                        <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35m1.35-5.15a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z" />
                        </svg>
                        <input
                          value={activeBooksQuery}
                          onChange={(e) => updateBooksQuery(e.target.value)}
                          placeholder="Search books or series"
                          className="bg-transparent outline-none text-xs text-gray-700 dark:text-gray-200 placeholder:text-gray-500 w-44"
                          aria-label="Search books"
                        />
                        {activeBooksQuery ? (
                          <button
                            type="button"
                            onClick={() => updateBooksQuery('')}
                            className="p-0.5 rounded-full text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action"
                            aria-label="Clear search"
                            title="Clear"
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                            </svg>
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                    <Dropdown
                      align="right"
                      widthClassName="w-auto flex-shrink-0"
                      panelClassName="w-64"
                      noScrollLimit={true}
                      renderTrigger={({ isOpen, toggle }) => (
                        <div className="inline-flex items-center gap-1.5">
                          <button
                            type="button"
                            onClick={toggle}
                            className={`relative p-1.5 rounded-full transition-all duration-200 ${
                              isOpen || activeFiltersCount > 0
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action'
                            }`}
                            aria-haspopup="menu"
                            aria-expanded={isOpen}
                            aria-label={singleActiveFilterLabel ? `Filter books (${singleActiveFilterLabel})` : 'Filter books'}
                            title={singleActiveFilterLabel ? `Filter books: ${singleActiveFilterLabel}` : 'Filter books'}
                          >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M3 4.5h18l-7 8.25v5.25l-4 2.25v-7.5L3 4.5Z" />
                            </svg>
                            {activeFiltersCount > 1 ? (
                              <span className="pointer-events-none absolute -top-1 -right-1 inline-flex items-center justify-center min-w-4 h-4 px-1 rounded-full text-[10px] font-semibold bg-emerald-600 text-white">
                                {activeFiltersCount}
                              </span>
                            ) : null}
                          </button>
                          {singleActiveFilterLabel ? (
                            <span className="max-w-28 truncate px-2 py-0.5 rounded-full text-[10px] font-medium border border-[var(--border-muted)] text-emerald-700 dark:text-emerald-300 bg-emerald-50/70 dark:bg-emerald-500/10">
                              {singleActiveFilterLabel}
                            </span>
                          ) : null}
                        </div>
                      )}
                    >
                      {({ close }) => (
                        <div className="py-1" role="menu" aria-label="Book filters">
                          <div className="px-3 pb-1 text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">Availability</div>
                          <div className="relative">
                            <button
                              type="button"
                              className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${AVAILABLE_FILTER_MODES.includes(booksFilters.availability) ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                              onClick={() => {
                                setIsAvailabilityFilterMenuOpen((prev) => {
                                  const next = !prev;
                                  if (next) setIsMissingFilterMenuOpen(false);
                                  return next;
                                });
                              }}
                            >
                              <span>Available</span>
                              <svg className={`w-3.5 h-3.5 transition-transform ${isAvailabilityFilterMenuOpen ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                              </svg>
                            </button>

                            {isAvailabilityFilterMenuOpen ? (
                              <div className="pl-3 pr-2 pb-1">
                                {AVAILABLE_FILTER_MODES.map((mode) => (
                                  <button
                                    key={mode}
                                    type="button"
                                    className={`w-full px-3 py-1.5 text-left text-xs rounded-md hover-surface flex items-center justify-between ${booksFilters.availability === mode ? 'text-emerald-600 dark:text-emerald-400 font-medium' : 'text-gray-600 dark:text-gray-300'}`}
                                    onClick={() => {
                                      setBooksFilters((prev) => ({ ...prev, availability: mode }));
                                    }}
                                  >
                                    <span>{AVAILABILITY_FILTER_LABELS[mode]}</span>
                                    {booksFilters.availability === mode ? <span>✓</span> : null}
                                  </button>
                                ))}
                              </div>
                            ) : null}
                          </div>

                          <div className="relative">
                            <button
                              type="button"
                              className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${MISSING_FILTER_MODES.includes(booksFilters.availability) ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                              onClick={() => {
                                setIsMissingFilterMenuOpen((prev) => {
                                  const next = !prev;
                                  if (next) setIsAvailabilityFilterMenuOpen(false);
                                  return next;
                                });
                              }}
                            >
                              <span>Missing</span>
                              <svg className={`w-3.5 h-3.5 transition-transform ${isMissingFilterMenuOpen ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                              </svg>
                            </button>

                            {isMissingFilterMenuOpen ? (
                              <div className="pl-3 pr-2 pb-1">
                                {MISSING_FILTER_MODES.map((mode) => (
                                  <button
                                    key={mode}
                                    type="button"
                                    className={`w-full px-3 py-1.5 text-left text-xs rounded-md hover-surface flex items-center justify-between ${booksFilters.availability === mode ? 'text-emerald-600 dark:text-emerald-400 font-medium' : 'text-gray-600 dark:text-gray-300'}`}
                                    onClick={() => {
                                      setBooksFilters((prev) => ({ ...prev, availability: mode }));
                                    }}
                                  >
                                    <span>{AVAILABILITY_FILTER_LABELS[mode]}</span>
                                    {booksFilters.availability === mode ? <span>✓</span> : null}
                                  </button>
                                ))}
                              </div>
                            ) : null}
                          </div>

                          <div className="my-1 border-t border-[var(--border-muted)]" />

                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${booksFilters.showNoReleaseDate ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => {
                              setBooksFilters((prev) => ({ ...prev, showNoReleaseDate: !prev.showNoReleaseDate }));
                            }}
                          >
                            <span>No release date</span>
                            {booksFilters.showNoReleaseDate ? <span>✓</span> : null}
                          </button>

                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${booksFilters.showUpcoming ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => {
                              setBooksFilters((prev) => ({ ...prev, showUpcoming: !prev.showUpcoming }));
                            }}
                          >
                            <span>Upcoming</span>
                            {booksFilters.showUpcoming ? <span>✓</span> : null}
                          </button>

                          {booksFilters.showUpcoming ? (
                            <div className="pl-3 pr-2 pb-1">
                              {(['any', '30d', '90d', 'this_year'] as UpcomingWindowMode[]).map((windowMode) => (
                                <button
                                  key={windowMode}
                                  type="button"
                                  className={`w-full px-3 py-1.5 text-left text-xs rounded-md hover-surface flex items-center justify-between ${booksFilters.upcomingWindow === windowMode ? 'text-emerald-600 dark:text-emerald-400 font-medium' : 'text-gray-600 dark:text-gray-300'}`}
                                  onClick={() => {
                                    setBooksFilters((prev) => ({ ...prev, upcomingWindow: windowMode }));
                                  }}
                                >
                                  <span>{UPCOMING_WINDOW_LABELS[windowMode]}</span>
                                  {booksFilters.upcomingWindow === windowMode ? <span>✓</span> : null}
                                </button>
                              ))}
                            </div>
                          ) : null}

                          <div className="my-1 border-t border-[var(--border-muted)]" />

                          <div className="relative">
                            <button
                              type="button"
                              className="w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between"
                              onClick={() => setIsSeriesFilterMenuOpen((prev) => !prev)}
                            >
                              <span className="inline-flex items-center gap-2">
                                <span>Series</span>
                                {booksFilters.seriesKeys.length > 0 ? (
                                  <span className="text-[10px] px-1.5 py-0.5 rounded-full bg-emerald-600 text-white">{booksFilters.seriesKeys.length}</span>
                                ) : null}
                              </span>
                              <svg className={`w-3.5 h-3.5 transition-transform ${isSeriesFilterMenuOpen ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                                <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                              </svg>
                            </button>

                            {isSeriesFilterMenuOpen ? (
                              <div className="absolute left-full top-0 ml-2 w-56 rounded-lg border border-[var(--border-muted)] bg-[var(--bg)] shadow-xl z-10 max-h-56 overflow-auto py-1">
                                {seriesFilterOptions.length === 0 ? (
                                  <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">No series found.</div>
                                ) : (
                                  seriesFilterOptions.map((option) => {
                                    const selected = booksFilters.seriesKeys.includes(option.key);
                                    return (
                                      <button
                                        key={option.key}
                                        type="button"
                                        className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${selected ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                        onClick={() => {
                                          toggleSeriesFilter(option.key);
                                          if (closeSeriesFilterOnSelect) {
                                            setIsSeriesFilterMenuOpen(false);
                                          }
                                        }}
                                      >
                                        <span className="truncate pr-2">{option.title}</span>
                                        <span className="inline-flex items-center gap-2 flex-shrink-0">
                                          <span className="text-[10px] text-gray-500 dark:text-gray-400">{option.count}</span>
                                          {selected ? <span>✓</span> : null}
                                        </span>
                                      </button>
                                    );
                                  })
                                )}
                                <div className="border-t border-[var(--border-muted)] mt-1 pt-1">
                                  <button
                                    type="button"
                                    className={`w-full px-3 py-2 text-left text-xs hover-surface flex items-center justify-between ${closeSeriesFilterOnSelect ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-500 dark:text-gray-400'}`}
                                    onClick={() => setCloseSeriesFilterOnSelect((prev) => !prev)}
                                  >
                                    <span>Close on select</span>
                                    {closeSeriesFilterOnSelect ? <span>✓</span> : null}
                                  </button>
                                </div>
                              </div>
                            ) : null}
                          </div>

                          <div className="mt-1 border-t border-[var(--border-muted)]" />
                          <div className="px-2 pt-2 pb-1 flex items-center justify-between gap-2">
                            <button
                              type="button"
                              className="px-2.5 py-1 text-xs rounded-md hover-surface"
                              onClick={() => {
                                setBooksFilters(createDefaultAuthorBooksFilters());
                                setIsSeriesFilterMenuOpen(false);
                                setIsAvailabilityFilterMenuOpen(false);
                                setIsMissingFilterMenuOpen(false);
                              }}
                            >
                              Clear
                            </button>
                            <button
                              type="button"
                              className="px-2.5 py-1 text-xs rounded-md hover-surface"
                              onClick={() => {
                                setIsSeriesFilterMenuOpen(false);
                                setIsAvailabilityFilterMenuOpen(false);
                                setIsMissingFilterMenuOpen(false);
                                close();
                              }}
                            >
                              Done
                            </button>
                          </div>
                        </div>
                      )}
                    </Dropdown>
                    <Dropdown
                      align="right"
                      widthClassName="w-auto flex-shrink-0"
                      panelClassName="w-48"
                      renderTrigger={({ isOpen, toggle }) => (
                        <button
                          type="button"
                          onClick={toggle}
                          className={`p-1.5 rounded-full transition-all duration-200 ${
                            isOpen
                              ? 'text-gray-900 dark:text-gray-100'
                              : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action'
                          }`}
                          aria-haspopup="listbox"
                          aria-expanded={isOpen}
                          title="Sort books"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M3 6h18M6 12h12M10 18h4" />
                          </svg>
                        </button>
                      )}
                    >
                      {({ close }) => (
                        <div role="listbox" aria-label="Sort books">
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'series_asc' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => { setBooksSort('series_asc'); close(); }}
                            role="option"
                            aria-selected={booksSort === 'series_asc'}
                          >
                            Series (A–Z)
                          </button>
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'series_desc' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => { setBooksSort('series_desc'); close(); }}
                            role="option"
                            aria-selected={booksSort === 'series_desc'}
                          >
                            Series (Z–A)
                          </button>
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'popular' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => { setBooksSort('popular'); close(); }}
                            role="option"
                            aria-selected={booksSort === 'popular'}
                          >
                            Most popular
                          </button>
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'rating' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => { setBooksSort('rating'); close(); }}
                            role="option"
                            aria-selected={booksSort === 'rating'}
                          >
                            Highest rated
                          </button>
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'year_desc' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => { setBooksSort('year_desc'); close(); }}
                            role="option"
                            aria-selected={booksSort === 'year_desc'}
                          >
                            Year (newest)
                          </button>
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'year_asc' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => { setBooksSort('year_asc'); close(); }}
                            role="option"
                            aria-selected={booksSort === 'year_asc'}
                          >
                            Year (oldest)
                          </button>
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'title_asc' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => { setBooksSort('title_asc'); close(); }}
                            role="option"
                            aria-selected={booksSort === 'title_asc'}
                          >
                            Title (A–Z)
                          </button>
                        </div>
                      )}
                    </Dropdown>
                    <Dropdown
                      align="right"
                      widthClassName="w-auto flex-shrink-0"
                      panelClassName="w-56"
                      renderTrigger={({ isOpen, toggle }) => (
                        <button
                          type="button"
                          onClick={toggle}
                          className={`p-1.5 rounded-full transition-all duration-200 ${
                            isOpen
                              ? 'text-gray-900 dark:text-gray-100'
                              : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action'
                          }`}
                          aria-label="Compact tile size"
                          title="Compact tile size"
                        >
                          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M4 19.5h16M7.5 4.5v12m4.5-9v9m4.5-6v6" />
                          </svg>
                        </button>
                      )}
                    >
                      {() => (
                        <div className="py-1">
                          <button
                            type="button"
                            onClick={toggleSelectAllVisibleBooks}
                            className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${allVisibleBooksSelected ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                          >
                            <span>{allVisibleBooksSelected ? 'Unselect all books' : 'Select all books'}</span>
                            {allVisibleBooksSelected ? <span>✓</span> : null}
                          </button>
                          <div className="border-t border-[var(--border-muted)] my-1" />
                          <div className="px-3 py-2">
                            <div className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">Compact tile size</div>
                            <input
                              type="range"
                              min={AUTHOR_BOOKS_COMPACT_MIN_WIDTH_MIN}
                              max={AUTHOR_BOOKS_COMPACT_MIN_WIDTH_MAX}
                              step={4}
                              value={booksCompactMinWidth}
                              onChange={(e) => setBooksCompactMinWidth(Number(e.target.value))}
                              className="w-full accent-emerald-600"
                              aria-label="Books compact tile size"
                              title="Books compact tile size"
                              disabled={booksViewMode !== 'compact'}
                            />
                            <div className="mt-1 text-[11px] text-gray-500 dark:text-gray-400 tabular-nums text-right">{booksCompactMinWidth}px</div>
                            {booksViewMode !== 'compact' ? (
                              <div className="mt-2 text-[11px] text-gray-500 dark:text-gray-400">Switch to compact view to adjust tile size.</div>
                            ) : null}
                          </div>
                        </div>
                      )}
                    </Dropdown>
                    <ViewModeToggle
                      className="hidden sm:inline-flex"
                      value={booksViewMode}
                      onChange={(next) => setBooksViewMode(next as AuthorBooksViewMode)}
                      options={[
                        {
                          value: 'table',
                          label: 'Table view',
                          icon: (
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 6.75h15m-15 5.25h15m-15 5.25h15" />
                            </svg>
                          ),
                        },
                        {
                          value: 'compact',
                          label: 'Compact view',
                          icon: (
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 4.5h6.75v6.75H4.5V4.5Zm8.25 0h6.75v6.75h-6.75V4.5ZM4.5 12.75h6.75v6.75H4.5v-6.75Zm8.25 0h6.75v6.75h-6.75v-6.75Z" />
                            </svg>
                          ),
                        },
                      ]}
                    />
                    <button
                      type="button"
                      onClick={() => void handleRefreshAndScan()}
                      disabled={isLoadingBooks || isRefreshing}
                      className="p-1.5 rounded-full text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action transition-all duration-200 disabled:opacity-30 disabled:cursor-not-allowed flex-shrink-0"
                      aria-label={monitoredEntityId ? 'Refresh & scan files' : 'Refresh books from provider'}
                      title={monitoredEntityId ? 'Refresh & scan files' : 'Refresh books from provider'}
                    >
                      <svg className={`w-4 h-4 ${isRefreshing ? 'animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182M20.015 4.356v4.992" />
                      </svg>
                    </button>
                    {monitoredEntityId ? (
                      <>
                        <button
                          type="button"
                          onClick={() => void handleRunMonitoredSearch('ebook')}
                          disabled={monitorSearchBusyByType.ebook || monitorSearchBusyByType.audiobook}
                          className="px-2.5 py-1 rounded-full text-[11px] font-medium border border-emerald-500/40 text-emerald-600 dark:text-emerald-400 hover-action disabled:opacity-40"
                          title="Search monitored ebook candidates"
                        >
                          {monitorSearchBusyByType.ebook ? 'Searching eBooks…' : 'Search eBooks'}
                        </button>
                        <button
                          type="button"
                          onClick={() => void handleRunMonitoredSearch('audiobook')}
                          disabled={monitorSearchBusyByType.ebook || monitorSearchBusyByType.audiobook}
                          className="px-2.5 py-1 rounded-full text-[11px] font-medium border border-emerald-500/40 text-emerald-600 dark:text-emerald-400 hover-action disabled:opacity-40"
                          title="Search monitored audiobook candidates"
                        >
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
                      <button
                        key={chip.key}
                        type="button"
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] border border-[var(--border-muted)] bg-[var(--bg-soft)] hover-surface"
                        onClick={() => {
                          if (chip.key === 'availability') {
                            setBooksFilters((prev) => ({ ...prev, availability: 'all' }));
                            return;
                          }
                          if (chip.key === 'upcoming') {
                            setBooksFilters((prev) => ({ ...prev, showUpcoming: false, upcomingWindow: 'any' }));
                            return;
                          }
                          if (chip.key === 'no_release_date') {
                            setBooksFilters((prev) => ({ ...prev, showNoReleaseDate: false }));
                            return;
                          }
                          if (chip.key.startsWith('series:')) {
                            const seriesKey = chip.key.slice('series:'.length);
                            setBooksFilters((prev) => ({ ...prev, seriesKeys: prev.seriesKeys.filter((key) => key !== seriesKey) }));
                          }
                        }}
                        title={`Remove filter: ${chip.label}`}
                      >
                        <span className="truncate max-w-44">{chip.label}</span>
                        <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                        </svg>
                      </button>
                    ))}
                  </div>
                ) : null}

                {monitoredEntityId ? (
                  <div className="px-4 pb-3">
                    {filesError ? <div className="text-sm text-red-500">{filesError}</div> : null}
                    {monitorSearchSummary ? <div className="text-sm text-emerald-600 dark:text-emerald-400">{monitorSearchSummary}</div> : null}
                    {filesLoading ? <div className="text-sm text-gray-600 dark:text-gray-300">Loading files…</div> : null}
                  </div>
                ) : null}

                <div className="px-4 py-3">
                  {booksError && <div className="text-sm text-red-500">{booksError}</div>}

                  {books.length === 0 && isLoadingBooks ? (
                    <div className="text-sm text-gray-600 dark:text-gray-300">Loading…</div>
                  ) : books.length === 0 && !isLoadingBooks ? (
                    <div className="text-sm text-gray-600 dark:text-gray-300">No books found.</div>
                  ) : filteredGroupedBooks.length === 0 ? (
                    <div className="text-sm text-gray-600 dark:text-gray-300">No books match the current filters.</div>
                  ) : (
                    <>
                      <div className="w-full rounded-xl overflow-hidden" style={{ background: 'var(--bg-soft)' }}>
                        {filteredGroupedBooks.map((group, groupIndex) => {
                          const isCollapsed = collapsedGroups[group.key] ?? false;
                          const allSelectedInGroup = group.books.length > 0 && group.books.every((book) => Boolean(selectedBookIds[book.id]));
                          const booksInSeries = group.books.length;
                          const booksOnDisk = group.books.reduce((count, book) => {
                            const prov = book.provider || '';
                            const bid = book.provider_id || '';
                            if (!prov || !bid) return count;
                            const key = `${prov}:${bid}`;
                            return count + (matchedFileTypesByBookKey.has(key) ? 1 : 0);
                          }, 0);
                          return (
                            <div key={group.key} className={groupIndex === 0 ? '' : 'mt-3'}>
                              <div className="w-full px-3 sm:px-4 py-2 border-t border-b border-gray-200/60 dark:border-gray-800/60 bg-black/5 dark:bg-white/5 flex items-center gap-3">
                                <button
                                  type="button"
                                  onClick={() => toggleSelectAllInGroup(group.books)}
                                  className="flex-shrink-0 text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
                                  aria-label={allSelectedInGroup ? `Unselect all books in ${group.title}` : `Select all books in ${group.title}`}
                                  title={allSelectedInGroup ? 'Unselect all in series' : 'Select all in series'}
                                >
                                  {allSelectedInGroup ? (
                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                                      <rect x="4" y="4" width="16" height="16" rx="3" />
                                      <path strokeLinecap="round" strokeLinejoin="round" d="m8 12 2.5 2.5L16 9" />
                                    </svg>
                                  ) : (
                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                                      <rect x="4" y="4" width="16" height="16" rx="3" />
                                    </svg>
                                  )}
                                </button>
                                <button
                                  type="button"
                                  onClick={() => toggleGroupCollapsed(group.key)}
                                  className="flex-1 flex items-center gap-2 min-w-0 hover-action"
                                  aria-expanded={!isCollapsed}
                                >
                                  <svg
                                    className={`w-3.5 h-3.5 flex-shrink-0 transition-transform duration-200 ${isCollapsed ? '' : 'rotate-90'}`}
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                    strokeWidth={2}
                                  >
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                                  </svg>
                                  <div className="min-w-0 flex items-center gap-2">
                                    <p className="text-s font-semibold text-gray-700 dark:text-gray-200 truncate">{group.title}</p>
                                    <span className={`text-[11px] tabular-nums ${booksOnDisk > 0 ? 'text-emerald-500 dark:text-emerald-400' : 'text-gray-500 dark:text-gray-400'}`}>
                                      ({booksOnDisk}/{booksInSeries})
                                    </span>
                                  </div>
                                </button>
                              </div>

                              {!isCollapsed ? (
                                booksViewMode === 'compact' ? (
                                  <div
                                    className="px-3 py-3 grid gap-3 justify-start"
                                    style={{ gridTemplateColumns: `repeat(auto-fit, minmax(${booksCompactMinWidth}px, ${booksCompactMinWidth}px))` }}
                                  >
                                    {group.books.map((book) => {
                                      const isSelected = Boolean(selectedBookIds[book.id]);
                                      const prov = book.provider || '';
                                      const bid = book.provider_id || '';
                                      const key = prov && bid ? `${prov}:${bid}` : '';
                                      const types = key ? matchedFileTypesByBookKey.get(key) : undefined;
                                      const sortedTypes = types ? Array.from(types).sort((a, b) => a.localeCompare(b)) : [];
                                      const seriesName = (book.series_name || (group.key !== '__standalone__' ? group.title : '') || '').trim();
                                      const seriesLabel = seriesName && book.series_position != null 
                                        ? `${seriesName} #${book.series_position}` 
                                        : seriesName;
                                      const isSeriesSort = booksSort === 'series_asc' || booksSort === 'series_desc';
                                      const isYearSort = booksSort === 'year_asc' || booksSort === 'year_desc';
                                      // Show series name when NOT grouping by series (i.e., not series sort)
                                      const showSeriesName = Boolean(seriesLabel) && !isSeriesSort;
                                      const showExtendedMeta = booksCompactMinWidth >= 178;
                                      const popularity = extractBookPopularity(book);
                                      const showPopularity = booksCompactMinWidth >= 194 && (popularity.rating !== null || popularity.readersCount !== null);
                                      // Show year when NOT grouping by year (i.e., not year sort)
                                      const yearPart = !isYearSort ? (book.year || '—') : '';
                                      const metaLine = yearPart ? `${yearPart}${book.author ? ` • ${book.author}` : ''}` : (book.author || '');
                                      const popularityLine = [
                                        popularity.rating !== null ? `★ ${popularity.rating.toFixed(1)}` : null,
                                        popularity.readersCount !== null ? `${popularity.readersCount.toLocaleString()} readers` : null,
                                      ].filter(Boolean).join(' • ');
                                      const bookMonitorState = getBookMonitorState(book);
                                      const isUnmonitored = !bookMonitorState.monitorEbook && !bookMonitorState.monitorAudiobook;
                                      return (
                                        <MonitoredBookCompactTile
                                          key={book.id}
                                          title={book.title || 'Untitled'}
                                          onOpenDetails={() => setActiveBookDetails(book)}
                                          onToggleSelect={() => toggleBookSelection(book.id)}
                                          isSelected={isSelected}
                                          hasActiveSelection={hasActiveBookSelection}
                                          seriesPosition={book.series_position}
                                          seriesCount={book.series_count}
                                          primaryFormat={sortedTypes[0]}
                                          extraFormatsCount={Math.max(0, sortedTypes.length - 1)}
                                          seriesLabel={seriesLabel}
                                          showSeriesName={showSeriesName}
                                          metaLine={metaLine}
                                          showMetaLine={showExtendedMeta}
                                          popularityLine={popularityLine}
                                          showPopularityLine={showPopularity}
                                          thumbnail={<BooksListThumbnail preview={book.preview} title={book.title} className="w-full aspect-[2/3]" />}
                                          overflowMenu={renderBookOverflowMenu(book)}
                                          isDimmed={isUnmonitored}
                                        />
                                      );
                                    })}
                                  </div>
                                ) : (
                                <div className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
                                  <div className="hidden sm:grid items-center px-1.5 sm:px-2 pt-1 pb-2 sm:gap-y-1 sm:gap-x-2 grid-cols-[auto_auto_minmax(0,2fr)_minmax(164px,164px)_minmax(64px,64px)]">
                                    <div />
                                    <div />
                                    <div />
                                    <div className="flex w-full justify-center">
                                      <span className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">Available</span>
                                    </div>
                                    <div />
                                  </div>
                                  {group.books.map((book) => (
                                      (() => {
                                        const popularity = extractBookPopularity(book);
                                        const hasPopularity = popularity.rating !== null || popularity.readersCount !== null;
                                        const seriesLabel = (book.series_name || (group.key !== '__standalone__' ? group.title : '') || '').trim();
                                        const showSeriesInfo = Boolean(seriesLabel) && group.key !== '__standalone__';
                                        const hasSeriesPosition = book.series_position != null;
                                        const bookMonitorState = getBookMonitorState(book);
                                        const isUnmonitored = !bookMonitorState.monitorEbook && !bookMonitorState.monitorAudiobook;

                                        return (
                                          <MonitoredBookTableRow
                                            key={book.id}
                                            isDimmed={isUnmonitored}
                                            leadingControl={(() => {
                                              const isSelected = Boolean(selectedBookIds[book.id]);
                                              return (
                                                <button
                                                  type="button"
                                                  onClick={() => toggleBookSelection(book.id)}
                                                  className={`transition-opacity ${isSelected || hasActiveBookSelection ? 'opacity-100' : 'opacity-0 group-hover:opacity-50'} ${isSelected ? 'text-emerald-500 dark:text-emerald-400' : 'text-gray-500 dark:text-gray-400'}`}
                                                  role="checkbox"
                                                  aria-checked={isSelected}
                                                  aria-label={`Select ${book.title || 'book'}`}
                                                  title={isSelected ? 'Unselect book' : 'Select book'}
                                                >
                                                  {isSelected ? (
                                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                                                      <rect x="4" y="4" width="16" height="16" rx="3" />
                                                      <path strokeLinecap="round" strokeLinejoin="round" d="m8 12 2.5 2.5L16 9" />
                                                    </svg>
                                                  ) : (
                                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                                                      <rect x="4" y="4" width="16" height="16" rx="3" />
                                                    </svg>
                                                  )}
                                                </button>
                                              );
                                            })()}
                                            thumbnail={<BooksListThumbnail preview={book.preview} title={book.title} />}
                                            onOpen={() => setActiveBookDetails(book)}
                                            titleRow={(
                                              <div className="flex items-center gap-2 min-w-0">
                                                <h3 className="font-semibold text-xs min-[400px]:text-sm sm:text-base leading-tight truncate" title={book.title || 'Untitled'}>
                                                  {book.title || 'Untitled'}
                                                </h3>
                                                {showSeriesInfo ? (
                                                  <span className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-500 dark:text-gray-400 truncate">
                                                    • {seriesLabel}
                                                  </span>
                                                ) : null}
                                                {hasSeriesPosition ? (
                                                  <span
                                                    className="inline-flex px-1 py-0 text-[9px] sm:text-[10px] font-bold text-white bg-emerald-600 rounded flex-shrink-0"
                                                    style={{
                                                      boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)',
                                                      textShadow: '0 1px 2px rgba(0, 0, 0, 0.3)',
                                                    }}
                                                    title={seriesLabel ? `${seriesLabel}${book.series_count ? ` (${book.series_position}/${book.series_count})` : ` (#${book.series_position})`}` : undefined}
                                                  >
                                                    #{book.series_position}
                                                    {book.series_count != null ? `/${book.series_count}` : ''}
                                                  </span>
                                                ) : null}
                                              </div>
                                            )}
                                            subtitleRow={(
                                              <p className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-600 dark:text-gray-300 truncate">
                                                {book.author || resolvedName || 'Unknown author'}
                                                {book.year ? <span> • {book.year}</span> : null}
                                              </p>
                                            )}
                                            metaRow={hasPopularity ? (
                                              <div className="text-[10px] min-[400px]:text-xs text-gray-500 dark:text-gray-400 flex items-center gap-2">
                                                {popularity.rating !== null ? (
                                                  <span className="inline-flex items-center gap-1 whitespace-nowrap">
                                                    <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20" aria-hidden="true">
                                                      <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.286 3.96a1 1 0 00.95.69h4.162c.969 0 1.371 1.24.588 1.81l-3.367 2.446a1 1 0 00-.364 1.118l1.286 3.96c.3.921-.755 1.688-1.538 1.118l-3.367-2.446a1 1 0 00-1.176 0l-3.367 2.446c-.783.57-1.838-.197-1.539-1.118l1.287-3.96a1 1 0 00-.364-1.118L2.063 9.387c-.783-.57-.38-1.81.588-1.81h4.162a1 1 0 00.95-.69l1.286-3.96Z" />
                                                    </svg>
                                                    <span>
                                                      {popularity.rating.toFixed(1)}
                                                      {popularity.ratingsCount !== null ? ` (${popularity.ratingsCount.toLocaleString()})` : ''}
                                                    </span>
                                                  </span>
                                                ) : null}
                                                {popularity.readersCount !== null ? (
                                                  <span className="inline-flex items-center gap-1 whitespace-nowrap">
                                                    <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8} aria-hidden="true">
                                                      <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.128a9.38 9.38 0 0 0-3-.479c-1.07 0-2.098.18-3 .512m6 0a7.5 7.5 0 1 0-6 0m6 0a9.372 9.372 0 0 1 3 .512M9 10.5a3 3 0 1 1 6 0 3 3 0 0 1-6 0Z" />
                                                    </svg>
                                                    <span>{popularity.readersCount.toLocaleString()}</span>
                                                  </span>
                                                ) : null}
                                              </div>
                                            ) : undefined}
                                            availabilitySlot={(
                                              <>
                                                {(() => {
                                                  const prov = book.provider || '';
                                                  const bid = book.provider_id || '';
                                                  const key = prov && bid ? `${prov}:${bid}` : '';
                                                  const types = key ? matchedFileTypesByBookKey.get(key) : undefined;
                                                  if (!types || types.size === 0) {
                                                    return null;
                                                  }

                                                  const sorted = Array.from(types).sort((a, b) => a.localeCompare(b));
                                                  return (
                                                    <>
                                                      {sorted.slice(0, 2).map((t) => (
                                                        <span
                                                          key={t}
                                                          className={`${getFormatColor(t).bg} ${getFormatColor(t).text} inline-flex items-center px-2 py-0.5 rounded-lg text-[10px] font-semibold tracking-wide uppercase`}
                                                          title={`Matched file: ${t.toUpperCase()}`}
                                                        >
                                                          {t.toUpperCase()}
                                                        </span>
                                                      ))}
                                                    </>
                                                  );
                                                })()}
                                              </>
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
          </div>
        </div>
      </div>
      </div>
      <BookDetailsModal
        book={activeBookDetails}
        files={activeBookFiles}
        monitoredEntityId={monitoredEntityId}
        onClose={() => setActiveBookDetails(null)}
        onOpenSearch={(contentType) => {
          if (!activeBookDetails || !onGetReleases) return;
          void triggerReleaseSearch(activeBookDetails, contentType);
        }}
        monitorEbook={activeBookDetails ? getBookMonitorState(activeBookDetails).monitorEbook : undefined}
        monitorAudiobook={activeBookDetails ? getBookMonitorState(activeBookDetails).monitorAudiobook : undefined}
        onToggleMonitor={activeBookDetails ? (type) => void toggleBookMonitor(activeBookDetails, type) : undefined}
      />

      <EditAuthorModal
        open={isEditModalOpen}
        entityId={monitoredEntityId ?? null}
        authorName={resolvedName || 'Unknown author'}
        onClose={() => setIsEditModalOpen(false)}
        onDeleted={handleClose}
      />
    </>
  );
};
