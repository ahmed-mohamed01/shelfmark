import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { Header } from '../components/Header';
import { ActivityStatusCounts } from '../utils/activityBadge';
import {
  createMonitoredEntity,
  listMonitoredEntities,
  listMonitoredBooks,
  listMonitoredBookFiles,
  updateMonitoredBooksMonitorFlags,
  getSelfUserEditContext,
  fsListDirectories,
  updateSelfUser,
  MetadataAuthor,
  MonitoredEntity,
  MonitoredBookFileRow,
  MonitoredBookRow,
  MonitoredAuthorBookSearchRow,
  searchMonitoredAuthorBooks,
  searchMetadata,
  searchMetadataAuthors,
} from '../services/api';
import { deleteMonitoredAuthorsByIds } from '../services/monitoredAuthors';
import { FolderBrowserModal } from '../components/FolderBrowserModal';
import { Dropdown } from '../components/Dropdown';
import { MediaCompactTileBase } from '../components/MediaCompactTileBase';
import { AuthorCompactView } from '../components/resultsViews/AuthorCompactView';
import { MonitoredAuthorCompactTile } from '../components/MonitoredAuthorCompactTile';
import { MonitoredAuthorTableRow } from '../components/AuthorTableRow';
import { MonitoredBookTableRow } from '../components/MonitoredBookTableRow';
import { BookDetailsModal } from '../components/BookDetailsModal';
import { AuthorModal, AuthorModalAuthor } from '../components/AuthorModal';
import { ViewModeToggle, type ViewModeToggleOption } from '../components/ViewModeToggle';
import { ResultsSection } from '../components/ResultsSection';
import { Book, ButtonStateInfo, ContentType, OpenReleasesOptions, ReleasePrimaryAction, SortOption, StatusData } from '../types';

interface MonitoredAuthor {
  id: number;
  name: string;
  provider?: string;
  provider_id?: string;
  photo_url?: string;
  books_count?: number;
  created_at?: string;
  cached_bio?: string;
  cached_source_url?: string;
}

interface MonitoredBookListRow extends MonitoredBookRow {
  author_entity_id: number;
  author_name: string;
  author_provider?: string;
  author_provider_id?: string;
  author_photo_url?: string;
  author_source_url?: string;
}

interface MonitoredBooksSourceEntity {
  id: number;
  kind: 'author' | 'book';
  name: string;
  provider?: string;
  provider_id?: string;
  cached_source_url?: string;
  settings?: Record<string, unknown>;
}

interface MonitoredBooksGroup {
  key: string;
  title: string;
  rows: MonitoredBookListRow[];
}

const groupMonitoredBooks = (
  rows: MonitoredBookListRow[],
  groupBy: 'none' | 'author' | 'year',
  allLabel: string,
): MonitoredBooksGroup[] => {
  if (rows.length === 0) {
    return [];
  }

  if (groupBy === 'none') {
    return [{ key: 'all', title: allLabel, rows }];
  }

  const groups = new Map<string, MonitoredBooksGroup>();

  for (const row of rows) {
    const groupKey = groupBy === 'author'
      ? `author:${(row.author_name || 'Unknown author').trim().toLowerCase()}`
      : `year:${typeof row.publish_year === 'number' ? row.publish_year : 'unknown'}`;
    const groupTitle = groupBy === 'author'
      ? (row.author_name || 'Unknown author')
      : (typeof row.publish_year === 'number' ? String(row.publish_year) : 'Unknown year');

    if (!groups.has(groupKey)) {
      groups.set(groupKey, { key: groupKey, title: groupTitle, rows: [] });
    }
    groups.get(groupKey)?.rows.push(row);
  }

  const sortedGroups = [...groups.values()];
  if (groupBy === 'year') {
    sortedGroups.sort((a, b) => {
      const aYear = a.title === 'Unknown year' ? Number.NEGATIVE_INFINITY : Number(a.title);
      const bYear = b.title === 'Unknown year' ? Number.NEGATIVE_INFINITY : Number(b.title);
      return bYear - aYear;
    });
  } else {
    sortedGroups.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }));
  }

  return sortedGroups;
};

const isUpcomingMonitoredBook = (book: MonitoredBookListRow, todayStartMs: number, currentYear: number): boolean => {
  if (typeof book.release_date === 'string' && book.release_date.trim()) {
    const parsed = Date.parse(book.release_date);
    if (Number.isFinite(parsed)) {
      const releaseDay = new Date(parsed);
      releaseDay.setHours(0, 0, 0, 0);
      if (releaseDay.getTime() >= todayStartMs) {
        return true;
      }
    }
  }

  return typeof book.publish_year === 'number' && book.publish_year > currentYear;
};

interface MonitoredPageProps {
  onActivityClick?: () => void;
  isActivityOpen?: boolean;
  onBack?: () => void;
  onMonitoredClick?: () => void;
  logoUrl?: string;

  debug?: boolean;
  onSettingsClick?: () => void;
  statusCounts?: ActivityStatusCounts;
  isAdmin?: boolean;
  canAccessSettings?: boolean;
  authRequired?: boolean;
  isAuthenticated?: boolean;
  username?: string | null;
  displayName?: string | null;
  onLogout?: () => void;
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
  metadataSortOptions?: SortOption[];
  status?: StatusData;
}

const normalizeAuthor = (value: string): string => {
  return value
    .split(/\s+/)
    .join(' ')
    .trim();
};

const extractPrimaryAuthorName = (value: string): string => {
  const first = (value || '').split(',')[0] || '';
  return normalizeAuthor(first);
};

const MONITORED_SEARCH_SCOPE_OPTIONS = [
  { value: 'authors', label: 'Authors' },
  { value: 'books', label: 'Books' },
];

const SEARCH_VIEW_ICON_GRID = (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 4.5h6.75v6.75H4.5V4.5Zm8.25 0h6.75v6.75h-6.75V4.5ZM4.5 12.75h6.75v6.75H4.5v-6.75Zm8.25 0h6.75v6.75h-6.75v-6.75Z" />
  </svg>
);

const SEARCH_VIEW_ICON_LIST = (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
    <path strokeLinecap="round" strokeLinejoin="round" d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0ZM3.75 12h.007v.008H3.75V12Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm-.375 5.25h.007v.008H3.75v-.008Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z" />
  </svg>
);

const SEARCH_VIEW_ICON_COMPACT_LINES = (
  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
    <rect x="3.75" y="4.5" width="6" height="6" rx="1.125" />
    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6h8.25M12 8.25h6" />
    <rect x="3.75" y="13.5" width="6" height="6" rx="1.125" />
    <path strokeLinecap="round" strokeLinejoin="round" d="M12 15h8.25M12 17.25h6" />
  </svg>
);

const BookRowThumbnail = ({ coverUrl, title }: { coverUrl?: string | null; title: string }) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);

  if (!coverUrl || imageError) {
    return (
      <div
        className="w-7 h-10 sm:w-10 sm:h-14 rounded bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-[8px] sm:text-[9px] font-medium text-gray-500 dark:text-gray-300"
        aria-label="No cover available"
      >
        No Cover
      </div>
    );
  }

  return (
    <div className="relative w-7 h-10 sm:w-10 sm:h-14 rounded overflow-hidden bg-gray-100 dark:bg-gray-800 border border-white/40 dark:border-gray-700/70">
      {!imageLoaded && (
        <div className="absolute inset-0 bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
      )}
      <img
        src={coverUrl}
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

const GRID_CLASSES = {
  mobile: 'grid-cols-1 items-start',
  compact: 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 items-start',
} as const;

const MONITORED_COMPACT_MIN_WIDTH_MIN = 120;
const MONITORED_COMPACT_MIN_WIDTH_MAX = 185;
const MONITORED_COMPACT_MIN_WIDTH_DEFAULT = 150;
const MONITORED_COUNTS_CACHE_KEY = 'monitoredCountsSnapshot';
const MONITORED_BOOKS_SEARCH_QUERY_KEY = 'monitoredBooksSearchQuery';
const MONITORED_BOOKS_SEARCH_EXPANDED_KEY = 'monitoredBooksSearchExpanded';

interface MonitoredCountsSnapshot {
  authors: number;
  books: number;
  upcoming: number;
  search: number;
}

const readMonitoredCountsSnapshot = (): MonitoredCountsSnapshot | null => {
  try {
    const raw = sessionStorage.getItem(MONITORED_COUNTS_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<MonitoredCountsSnapshot>;
    if (
      typeof parsed.authors === 'number'
      && typeof parsed.books === 'number'
      && typeof parsed.upcoming === 'number'
      && typeof parsed.search === 'number'
    ) {
      return {
        authors: parsed.authors,
        books: parsed.books,
        upcoming: parsed.upcoming,
        search: parsed.search,
      };
    }
  } catch {
    // ignore
  }
  return null;
};

const selectFallbackPhotoFromMonitoredBooks = (books: MonitoredBookRow[]): string | undefined => {
  let bestCover: string | undefined;
  let bestReaders = -1;
  let bestRatingsCount = -1;
  let bestRating = -1;
  let bestTitle = '';

  for (const book of books) {
    const cover = typeof book.cover_url === 'string' ? book.cover_url.trim() : '';
    if (!cover) continue;

    const readers = typeof book.readers_count === 'number' ? book.readers_count : -1;
    const ratingsCount = typeof book.ratings_count === 'number' ? book.ratings_count : -1;
    const rating = typeof book.rating === 'number' ? book.rating : -1;
    const title = (book.title || '').trim();

    const isBetter = readers > bestReaders
      || (readers === bestReaders && ratingsCount > bestRatingsCount)
      || (readers === bestReaders && ratingsCount === bestRatingsCount && rating > bestRating)
      || (
        readers === bestReaders
        && ratingsCount === bestRatingsCount
        && rating === bestRating
        && title.localeCompare(bestTitle, undefined, { sensitivity: 'base' }) < 0
      );

    if (isBetter) {
      bestCover = cover;
      bestReaders = readers;
      bestRatingsCount = ratingsCount;
      bestRating = rating;
      bestTitle = title;
    }
  }

  return bestCover;
};

const AuthorRowThumbnail = ({ photo_url, name }: { photo_url?: string; name: string }) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);

  if (!photo_url || imageError) {
    return (
      <div
        className="w-7 h-10 sm:w-10 sm:h-14 rounded bg-gray-200 dark:bg-gray-700 flex items-center justify-center text-[8px] sm:text-[9px] font-medium text-gray-500 dark:text-gray-300"
        aria-label="No photo available"
      >
        No Photo
      </div>
    );
  }

  return (
    <div className="relative w-7 h-10 sm:w-10 sm:h-14 rounded overflow-hidden bg-gray-100 dark:bg-gray-800 border border-white/40 dark:border-gray-700/70">
      {!imageLoaded && (
        <div className="absolute inset-0 bg-gradient-to-r from-gray-200 via-gray-100 to-gray-200 dark:from-gray-700 dark:via-gray-600 dark:to-gray-700 animate-pulse" />
      )}
      <img
        src={photo_url}
        alt={name}
        className="w-full h-full object-cover object-top"
        loading="lazy"
        onLoad={() => setImageLoaded(true)}
        onError={() => setImageError(true)}
        style={{ opacity: imageLoaded ? 1 : 0, transition: 'opacity 0.2s ease-in-out' }}
      />
    </div>
  );
};

export const MonitoredPage = ({
  onActivityClick,
  isActivityOpen = false,
  onBack,
  onMonitoredClick,
  logoUrl,
  debug,
  onSettingsClick,
  statusCounts,
  isAdmin,
  canAccessSettings,
  authRequired,
  isAuthenticated,
  username,
  displayName,
  onLogout,
  onGetReleases,
  defaultReleaseContentType = 'ebook',
  defaultReleaseActionEbook = 'interactive_search',
  defaultReleaseActionAudiobook = 'interactive_search',
  metadataSortOptions,
  status,
}: MonitoredPageProps) => {
  const [landingTab, setLandingTab] = useState<'authors' | 'books' | 'upcoming' | 'search'>(() => {
    const saved = localStorage.getItem('monitoredLandingTab');
    return saved === 'books' || saved === 'upcoming' || saved === 'search' ? saved : 'authors';
  });
  const [view, setView] = useState<'landing' | 'search'>('landing');
  const [searchScope, setSearchScope] = useState<'authors' | 'books'>('authors');
  const [authorQuery, setAuthorQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [monitoredError, setMonitoredError] = useState<string | null>(null);
  const [rootsError, setRootsError] = useState<string | null>(null);
  const [authorResults, setAuthorResults] = useState<string[]>([]);
  const [authorCards, setAuthorCards] = useState<MetadataAuthor[]>([]);
  const [bookSearchResults, setBookSearchResults] = useState<Book[]>([]);
  const [bookSearchSortValue, setBookSearchSortValue] = useState('relevance');
  const [bookSearchViewMode, setBookSearchViewMode] = useState<'compact' | 'list'>(() => {
    const saved = localStorage.getItem('bookViewMode');
    return saved === 'list' ? 'list' : 'compact';
  });
  const [authorViewMode, setAuthorViewMode] = useState<'compact' | 'list'>(() => {
    const saved = localStorage.getItem('authorViewMode');
    return saved === 'list' ? 'list' : 'compact';
  });
  const [monitoredViewMode, setMonitoredViewMode] = useState<'compact' | 'table'>(() => {
    const saved = localStorage.getItem('monitoredAuthorViewMode');
    if (saved === 'table' || saved === 'list') return 'table';
    if (saved === 'compact' || saved === 'card') return 'compact';
    return 'compact';
  });
  const [monitoredBooksViewMode, setMonitoredBooksViewMode] = useState<'table' | 'compact'>(() => {
    const saved = localStorage.getItem('monitoredBooksViewMode');
    return saved === 'table' || saved === 'list' ? 'table' : 'compact';
  });
  const [monitoredBooksSortBy, setMonitoredBooksSortBy] = useState<'alphabetical' | 'year'>(() => {
    const saved = localStorage.getItem('monitoredBooksSortBy');
    return saved === 'year' ? 'year' : 'alphabetical';
  });
  const [monitoredBooksGroupBy, setMonitoredBooksGroupBy] = useState<'none' | 'author' | 'year'>(() => {
    const saved = localStorage.getItem('monitoredBooksGroupBy');
    return saved === 'author' || saved === 'year' ? saved : 'none';
  });
  const [monitoredSortBy, setMonitoredSortBy] = useState<'alphabetical' | 'date_added' | 'books_count'>(() => {
    const saved = localStorage.getItem('monitoredAuthorSortBy');
    return saved === 'date_added' || saved === 'books_count' || saved === 'alphabetical'
      ? saved
      : 'alphabetical';
  });
  const [monitoredCompactMinWidth, setMonitoredCompactMinWidth] = useState<number>(() => {
    const raw = localStorage.getItem('monitoredCompactMinWidth');
    const parsed = raw ? Number(raw) : NaN;
    if (!Number.isFinite(parsed)) {
      return MONITORED_COMPACT_MIN_WIDTH_DEFAULT;
    }
    return Math.max(MONITORED_COMPACT_MIN_WIDTH_MIN, Math.min(MONITORED_COMPACT_MIN_WIDTH_MAX, parsed));
  });
  const [monitored, setMonitored] = useState<MonitoredAuthor[]>([]);
  const [monitoredBooksSources, setMonitoredBooksSources] = useState<MonitoredBooksSourceEntity[]>([]);
  const [monitoredLoaded, setMonitoredLoaded] = useState(false);
  const [monitoredBooksRows, setMonitoredBooksRows] = useState<MonitoredBookListRow[]>([]);
  const [monitoredBooksLoading, setMonitoredBooksLoading] = useState(false);
  const [monitoredBooksLoadError, setMonitoredBooksLoadError] = useState<string | null>(null);
  const [activeBookDetails, setActiveBookDetails] = useState<Book | null>(null);
  const [activeBookFiles, setActiveBookFiles] = useState<MonitoredBookFileRow[]>([]);
  const [activeBookEntityId, setActiveBookEntityId] = useState<number | null>(null);
  const [activeBookSourceRow, setActiveBookSourceRow] = useState<MonitoredBookListRow | null>(null);
  const activeBookRequestSeq = useRef(0);
  const navigate = useNavigate();
  const location = useLocation();
  const [monitoredBooksSearchQuery, setMonitoredBooksSearchQuery] = useState(() => {
    try {
      return sessionStorage.getItem(MONITORED_BOOKS_SEARCH_QUERY_KEY) || '';
    } catch {
      return '';
    }
  });
  const [monitoredBooksSearchResults, setMonitoredBooksSearchResults] = useState<MonitoredAuthorBookSearchRow[]>([]);
  const [monitoredBooksSearchLoading, setMonitoredBooksSearchLoading] = useState(false);
  const [monitoredBooksSearchError, setMonitoredBooksSearchError] = useState<string | null>(null);
  const [monitoredBooksSearchOpen, setMonitoredBooksSearchOpen] = useState(false);
  const [monitoredBooksSearchExpanded, setMonitoredBooksSearchExpanded] = useState(() => {
    try {
      return sessionStorage.getItem(MONITORED_BOOKS_SEARCH_EXPANDED_KEY) === '1';
    } catch {
      return false;
    }
  });
  const monitoredBooksSearchRef = useRef<HTMLDivElement | null>(null);
  const monitoredBooksSearchInputRef = useRef<HTMLInputElement | null>(null);
  const [selectedMonitoredBookKeys, setSelectedMonitoredBookKeys] = useState<Record<string, boolean>>({});
  const [selectedMonitoredAuthorKeys, setSelectedMonitoredAuthorKeys] = useState<Record<string, boolean>>({});
  const [bulkUnmonitorRunning, setBulkUnmonitorRunning] = useState(false);
  const [bulkDeleteAuthorsRunning, setBulkDeleteAuthorsRunning] = useState(false);
  const [bulkDeleteAuthorsConfirmOpen, setBulkDeleteAuthorsConfirmOpen] = useState(false);
  const [cachedMonitoredCounts, setCachedMonitoredCounts] = useState<MonitoredCountsSnapshot | null>(() => readMonitoredCountsSnapshot());

  const [monitorModalState, setMonitorModalState] = useState<{
    open: boolean;
    author: { name: string; provider?: string; provider_id?: string; photo_url?: string; books_count?: number } | null;
    ebookAuthorDir: string;
    audiobookAuthorDir: string;
    monitorEbookMode: 'all' | 'missing' | 'upcoming';
    monitorAudiobookMode: 'all' | 'missing' | 'upcoming';
  }>(() => ({
    open: false,
    author: null,
    ebookAuthorDir: '',
    audiobookAuthorDir: '',
    monitorEbookMode: 'missing',
    monitorAudiobookMode: 'missing',
  }));

  const [bookMonitorModalState, setBookMonitorModalState] = useState<{
    open: boolean;
    book: Book | null;
    ebookAuthorDir: string;
    audiobookAuthorDir: string;
    monitorEbook: boolean;
    monitorAudiobook: boolean;
  }>({
    open: false,
    book: null,
    ebookAuthorDir: '',
    audiobookAuthorDir: '',
    monitorEbook: true,
    monitorAudiobook: true,
  });

  const [monitoredEbookRoots, setMonitoredEbookRoots] = useState<string[]>([]);
  const [monitoredAudiobookRoots, setMonitoredAudiobookRoots] = useState<string[]>([]);

  const [folderBrowserState, setFolderBrowserState] = useState<{
    open: boolean;
    kind: 'ebook' | 'audiobook' | null;
    initialPath: string | null;
  }>({ open: false, kind: null, initialPath: null });

  const [pathSuggestState, setPathSuggestState] = useState<{
    kind: 'ebook' | 'audiobook' | null;
    open: boolean;
    loading: boolean;
    parent: string | null;
    entries: { name: string; path: string }[];
    error: string | null;
  }>({
    kind: null,
    open: false,
    loading: false,
    parent: null,
    entries: [],
    error: null,
  });

  const [editingMonitorPathKind, setEditingMonitorPathKind] = useState<'ebook' | 'audiobook' | null>(null);

  const [isDesktop, setIsDesktop] = useState(false);
  useEffect(() => {
    let timeoutId: number;

    const checkDesktop = () => {
      clearTimeout(timeoutId);
      timeoutId = window.setTimeout(() => {
        setIsDesktop(window.innerWidth >= 640);
      }, 100);
    };

    setIsDesktop(window.innerWidth >= 640);
    window.addEventListener('resize', checkDesktop);
    return () => {
      clearTimeout(timeoutId);
      window.removeEventListener('resize', checkDesktop);
    };
  }, []);

  useEffect(() => {
    const targets = monitored.filter((author) => !author.photo_url && Number.isFinite(author.id));
    if (targets.length === 0) return;

    let cancelled = false;

    void (async () => {
      const results = await Promise.allSettled(targets.map(async (author) => {
        const response = await listMonitoredBooks(author.id);
        return {
          id: author.id,
          photo_url: selectFallbackPhotoFromMonitoredBooks(response.books),
        };
      }));

      if (cancelled) return;

      const fallbackById = new Map<number, string>();
      for (const result of results) {
        if (result.status === 'fulfilled' && result.value.photo_url) {
          fallbackById.set(result.value.id, result.value.photo_url);
        }
      }

      if (fallbackById.size === 0) return;

      setMonitored((prev) => prev.map((author) => {
        if (author.photo_url) return author;
        const fallback = fallbackById.get(author.id);
        return fallback ? { ...author, photo_url: fallback } : author;
      }));
    })();

    return () => {
      cancelled = true;
    };
  }, [monitored]);

  useEffect(() => {
    let alive = true;
    setMonitoredLoaded(false);

    const toMonitoredAuthor = (entity: MonitoredEntity): MonitoredAuthor | null => {
      if (entity.kind !== 'author') {
        return null;
      }
      const name = normalizeAuthor(entity.name);
      if (!name) {
        return null;
      }

      const settings = entity.settings && typeof entity.settings === 'object' ? entity.settings : {};
      const photo_url = typeof (settings as Record<string, unknown>).photo_url === 'string'
        ? ((settings as Record<string, unknown>).photo_url as string)
        : undefined;
      const books_count = typeof (settings as Record<string, unknown>).books_count === 'number'
        ? ((settings as Record<string, unknown>).books_count as number)
        : undefined;

      return {
        id: entity.id,
        name,
        provider: entity.provider || undefined,
        provider_id: entity.provider_id || undefined,
        photo_url,
        books_count,
        created_at: entity.created_at || undefined,
        cached_bio: entity.cached_bio || undefined,
        cached_source_url: entity.cached_source_url || undefined,
      };
    };

    const load = async () => {
      setMonitoredError(null);
      try {
        const entities = await listMonitoredEntities();
        const nextSources = entities
          .map((entity): MonitoredBooksSourceEntity | null => {
            if (entity.kind !== 'author' && entity.kind !== 'book') {
              return null;
            }
            const settings = entity.settings && typeof entity.settings === 'object' ? entity.settings as Record<string, unknown> : undefined;
            return {
              id: entity.id,
              kind: entity.kind,
              name: String(entity.name || '').trim(),
              provider: entity.provider || undefined,
              provider_id: entity.provider_id || undefined,
              cached_source_url: entity.cached_source_url || undefined,
              settings,
            };
          })
          .filter((item): item is MonitoredBooksSourceEntity => item !== null);
        const next = entities
          .map(toMonitoredAuthor)
          .filter((item): item is MonitoredAuthor => item !== null);
        if (!alive) {
          return;
        }
        setMonitoredBooksSources(nextSources);
        setMonitored(next);
      } catch (e) {
        if (!alive) {
          return;
        }
        const message = e instanceof Error ? e.message : 'Failed to load monitored authors';
        setMonitoredError(message);
        setMonitoredBooksSources([]);
        setMonitored([]);
      } finally {
        if (alive) {
          setMonitoredLoaded(true);
        }
      }
    };

    void load();
    return () => {
      alive = false;
    };
  }, []);

  useEffect(() => {
    let alive = true;

    const loadRoots = async () => {
      setRootsError(null);
      try {
        const ctx = await getSelfUserEditContext();
        const overrides = ctx?.deliveryPreferences?.userOverrides ?? {};
        const ebook = overrides.MONITORED_EBOOK_ROOTS;
        const audio = overrides.MONITORED_AUDIOBOOK_ROOTS;
        setMonitoredEbookRoots(Array.isArray(ebook) ? ebook.filter((v): v is string => typeof v === 'string' && Boolean(v.trim())) : []);
        setMonitoredAudiobookRoots(Array.isArray(audio) ? audio.filter((v): v is string => typeof v === 'string' && Boolean(v.trim())) : []);
      } catch (e) {
        if (!alive) return;
        const message = e instanceof Error ? e.message : 'Failed to load folder suggestions';
        setRootsError(message);
        setMonitoredEbookRoots([]);
        setMonitoredAudiobookRoots([]);
      }
    };

    void loadRoots();
    return () => {
      alive = false;
    };
  }, []);

  const joinPath = useCallback((root: string, authorName: string): string => {
    const r = (root || '').trim().replace(/\/+$/g, '');
    if (!r) return '';
    return `${r}/${authorName}`;
  }, []);

  const normalizeAbsolutePath = useCallback((value: string): string => {
    const v = (value || '').trim();
    if (!v) return '';
    return v.replace(/\/+$/g, '');
  }, []);

  const stripTrailingAuthorName = useCallback((fullPath: string, authorName: string): string => {
    const normalized = normalizeAbsolutePath(fullPath);
    const a = (authorName || '').trim();
    if (!normalized || !a) return normalized;
    const suffix = `/${a}`;
    if (normalized.endsWith(suffix)) {
      return normalized.slice(0, -suffix.length) || '/';
    }
    return normalized;
  }, [normalizeAbsolutePath]);

  const deriveRootFromAuthorDir = useCallback((authorDir: string): string => {
    const normalized = normalizeAbsolutePath(authorDir);
    if (!normalized || !normalized.startsWith('/')) return '';
    const idx = normalized.lastIndexOf('/');
    if (idx <= 0) return '';
    return normalized.slice(0, idx);
  }, [normalizeAbsolutePath]);

  const persistLearnedRoots = useCallback(async (nextEbookRoot: string, nextAudiobookRoot: string) => {
    const ebookRoot = normalizeAbsolutePath(nextEbookRoot);
    const audioRoot = normalizeAbsolutePath(nextAudiobookRoot);

    if (!ebookRoot && !audioRoot) {
      return;
    }

    const nextSettings: Record<string, unknown> = {};

    if (ebookRoot) {
      const merged = [ebookRoot, ...monitoredEbookRoots].filter(Boolean);
      const unique = Array.from(new Set(merged));
      nextSettings.MONITORED_EBOOK_ROOTS = unique;
      setMonitoredEbookRoots(unique);
    }

    if (audioRoot) {
      const merged = [audioRoot, ...monitoredAudiobookRoots].filter(Boolean);
      const unique = Array.from(new Set(merged));
      nextSettings.MONITORED_AUDIOBOOK_ROOTS = unique;
      setMonitoredAudiobookRoots(unique);
    }

    try {
      await updateSelfUser({ settings: nextSettings });
    } catch {
      // Best-effort persistence; ignore.
    }
  }, [monitoredEbookRoots, monitoredAudiobookRoots, normalizeAbsolutePath]);

  const monitoredAuthorsForCards: MetadataAuthor[] = useMemo(() => {
    const sorted = [...monitored].sort((a, b) => {
      if (monitoredSortBy === 'date_added') {
        const aDate = a.created_at ? Date.parse(a.created_at) : NaN;
        const bDate = b.created_at ? Date.parse(b.created_at) : NaN;
        const aHasDate = Number.isFinite(aDate);
        const bHasDate = Number.isFinite(bDate);
        if (aHasDate && bHasDate && aDate !== bDate) {
          return bDate - aDate;
        }
        if (aHasDate !== bHasDate) {
          return aHasDate ? -1 : 1;
        }
        return b.id - a.id;
      }

      if (monitoredSortBy === 'books_count') {
        const aCount = typeof a.books_count === 'number' ? a.books_count : -1;
        const bCount = typeof b.books_count === 'number' ? b.books_count : -1;
        if (bCount !== aCount) {
          return bCount - aCount;
        }
      }

      return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
    });

    return sorted.map((item) => ({
      provider: item.provider || 'hardcover',
      provider_id: item.provider_id || item.name,
      name: item.name,
      photo_url: item.photo_url,
      source_url: item.cached_source_url || null,
      bio: item.cached_bio || null,
      stats: {
        books_count: typeof item.books_count === 'number' ? item.books_count : null,
      },
    }));
  }, [monitored, monitoredSortBy]);

  const monitoredBooksForTable = useMemo(() => {
    const trackedOnly = monitoredBooksRows.filter((book) => (
      book.monitor_ebook === true
      || book.monitor_ebook === 1
      || book.monitor_audiobook === true
      || book.monitor_audiobook === 1
    ));

    return trackedOnly.sort((a, b) => {
      if (monitoredBooksSortBy === 'year') {
        const aYear = typeof a.publish_year === 'number' ? a.publish_year : Number.POSITIVE_INFINITY;
        const bYear = typeof b.publish_year === 'number' ? b.publish_year : Number.POSITIVE_INFINITY;
        if (aYear !== bYear) {
          return aYear - bYear;
        }
      }

      const titleCompare = (a.title || '').localeCompare(b.title || '', undefined, { sensitivity: 'base' });
      if (titleCompare !== 0) return titleCompare;
      return (a.author_name || '').localeCompare(b.author_name || '', undefined, { sensitivity: 'base' });
    });
  }, [monitoredBooksRows, monitoredBooksSortBy]);

  const todayStartMs = useMemo(() => {
    const now = new Date();
    now.setHours(0, 0, 0, 0);
    return now.getTime();
  }, []);
  const currentYear = useMemo(() => new Date(todayStartMs).getFullYear(), [todayStartMs]);

  const upcomingMonitoredBooksForTable = useMemo(() => {
    return monitoredBooksForTable.filter((book) => isUpcomingMonitoredBook(book, todayStartMs, currentYear));
  }, [monitoredBooksForTable, todayStartMs, currentYear]);

  const regularMonitoredBooksForTable = useMemo(() => {
    return monitoredBooksForTable.filter((book) => !isUpcomingMonitoredBook(book, todayStartMs, currentYear));
  }, [monitoredBooksForTable, todayStartMs, currentYear]);

  const normalizedMonitoredBooksFilterQuery = monitoredBooksSearchQuery.trim().toLowerCase();

  const matchesMonitoredBooksFilter = useCallback((book: MonitoredBookListRow): boolean => {
    if (!normalizedMonitoredBooksFilterQuery) return true;
    const fields = [
      book.title || '',
      book.author_name || '',
      book.series_name || '',
      book.provider || '',
      book.provider_book_id || '',
      typeof book.publish_year === 'number' ? String(book.publish_year) : '',
    ];
    return fields.some((field) => field.toLowerCase().includes(normalizedMonitoredBooksFilterQuery));
  }, [normalizedMonitoredBooksFilterQuery]);

  const filteredRegularMonitoredBooksForTable = useMemo(() => {
    if (!normalizedMonitoredBooksFilterQuery || landingTab === 'authors') {
      return regularMonitoredBooksForTable;
    }
    return regularMonitoredBooksForTable.filter(matchesMonitoredBooksFilter);
  }, [
    normalizedMonitoredBooksFilterQuery,
    landingTab,
    regularMonitoredBooksForTable,
    matchesMonitoredBooksFilter,
  ]);

  const filteredUpcomingMonitoredBooksForTable = useMemo(() => {
    if (!normalizedMonitoredBooksFilterQuery || landingTab === 'authors') {
      return upcomingMonitoredBooksForTable;
    }
    return upcomingMonitoredBooksForTable.filter(matchesMonitoredBooksFilter);
  }, [
    normalizedMonitoredBooksFilterQuery,
    landingTab,
    upcomingMonitoredBooksForTable,
    matchesMonitoredBooksFilter,
  ]);

  const monitoredBookGroups = useMemo<MonitoredBooksGroup[]>(() => {
    return groupMonitoredBooks(filteredRegularMonitoredBooksForTable, monitoredBooksGroupBy, 'All monitored books');
  }, [filteredRegularMonitoredBooksForTable, monitoredBooksGroupBy]);

  const upcomingBookGroups = useMemo<MonitoredBooksGroup[]>(() => {
    return groupMonitoredBooks(filteredUpcomingMonitoredBooksForTable, monitoredBooksGroupBy, 'All upcoming books');
  }, [filteredUpcomingMonitoredBooksForTable, monitoredBooksGroupBy]);

  useEffect(() => {
    try {
      sessionStorage.setItem(MONITORED_BOOKS_SEARCH_QUERY_KEY, monitoredBooksSearchQuery);
    } catch {
      // ignore
    }
  }, [monitoredBooksSearchQuery]);

  useEffect(() => {
    try {
      sessionStorage.setItem(MONITORED_BOOKS_SEARCH_EXPANDED_KEY, monitoredBooksSearchExpanded ? '1' : '0');
    } catch {
      // ignore
    }
  }, [monitoredBooksSearchExpanded]);

  useEffect(() => {
    if (!monitoredLoaded) {
      return;
    }
    const snapshot: MonitoredCountsSnapshot = {
      authors: monitoredAuthorsForCards.length,
      books: filteredRegularMonitoredBooksForTable.length,
      upcoming: filteredUpcomingMonitoredBooksForTable.length,
      search: searchScope === 'books' ? bookSearchResults.length : authorResults.length,
    };
    setCachedMonitoredCounts(snapshot);
    try {
      sessionStorage.setItem(MONITORED_COUNTS_CACHE_KEY, JSON.stringify(snapshot));
    } catch {
      // ignore
    }
  }, [monitoredLoaded, monitored.length, regularMonitoredBooksForTable.length, upcomingMonitoredBooksForTable.length]);

  useEffect(() => {
    try {
      localStorage.setItem('authorViewMode', authorViewMode);
    } catch {
      // ignore
    }
  }, [authorViewMode]);

  useEffect(() => {
    try {
      localStorage.setItem('monitoredAuthorViewMode', monitoredViewMode);
    } catch {
      // ignore
    }
  }, [monitoredViewMode]);

  useEffect(() => {
    try {
      localStorage.setItem('monitoredBooksViewMode', monitoredBooksViewMode);
    } catch {
      // ignore
    }
  }, [monitoredBooksViewMode]);

  useEffect(() => {
    try {
      localStorage.setItem('monitoredBooksSortBy', monitoredBooksSortBy);
    } catch {
      // ignore
    }
  }, [monitoredBooksSortBy]);

  useEffect(() => {
    try {
      localStorage.setItem('monitoredBooksGroupBy', monitoredBooksGroupBy);
    } catch {
      // ignore
    }
  }, [monitoredBooksGroupBy]);

  useEffect(() => {
    try {
      localStorage.setItem('monitoredLandingTab', landingTab);
    } catch {
      // ignore
    }
  }, [landingTab]);

  useEffect(() => {
    try {
      localStorage.setItem('monitoredAuthorSortBy', monitoredSortBy);
    } catch {
      // ignore
    }
  }, [monitoredSortBy]);

  useEffect(() => {
    try {
      localStorage.setItem('monitoredCompactMinWidth', String(monitoredCompactMinWidth));
    } catch {
      // ignore
    }
  }, [monitoredCompactMinWidth]);

  const monitoredNames = useMemo(() => new Set(monitored.map((a) => a.name.toLowerCase())), [monitored]);

  const monitoredSingleBookKeySet = useMemo(() => {
    const keys = new Set<string>();
    for (const entity of monitoredBooksSources) {
      if (entity.kind !== 'book') {
        continue;
      }
      const provider = (entity.provider || '').trim().toLowerCase();
      const providerId = (entity.provider_id || '').trim().toLowerCase();
      if (!provider || !providerId) {
        continue;
      }
      keys.add(`${provider}:${providerId}`);
    }
    return keys;
  }, [monitoredBooksSources]);

  useEffect(() => {
    if (monitoredBooksSources.length === 0) {
      setMonitoredBooksRows([]);
      setMonitoredBooksLoading(false);
      setMonitoredBooksLoadError(null);
      return;
    }

    let alive = true;

    void (async () => {
      setMonitoredBooksLoading(true);
      setMonitoredBooksLoadError(null);

      const responses = await Promise.allSettled(
        monitoredBooksSources.map(async (entity) => {
          const response = await listMonitoredBooks(entity.id);
          return { entity, books: response.books };
        })
      );

      if (!alive) {
        return;
      }

      const rows: MonitoredBookListRow[] = [];
      let failedCount = 0;

      for (const result of responses) {
        if (result.status !== 'fulfilled') {
          failedCount += 1;
          continue;
        }
        const { entity, books } = result.value;
        const settings = entity.settings || {};
        const bookSettingsAuthorName = typeof settings.book_author === 'string' ? settings.book_author.trim() : '';
        const bookSettingsSourceUrl = typeof settings.book_source_url === 'string' ? settings.book_source_url.trim() : '';
        for (const book of books || []) {
          const displayAuthor = entity.kind === 'book'
            ? (extractPrimaryAuthorName(book.authors || '') || bookSettingsAuthorName || entity.name || 'Unknown author')
            : entity.name;
          rows.push({
            ...book,
            author_entity_id: entity.id,
            author_name: displayAuthor,
            author_provider: entity.provider,
            author_provider_id: entity.provider_id,
            author_source_url: entity.cached_source_url || bookSettingsSourceUrl || undefined,
          });
        }
      }

      setMonitoredBooksRows(rows);
      setMonitoredBooksLoadError(failedCount > 0 ? 'Some monitored books could not be loaded.' : null);
      setMonitoredBooksLoading(false);
    })();

    return () => {
      alive = false;
    };
  }, [monitored]);

  const monitoredEntityIdByName = useMemo(() => {
    const map = new Map<string, number>();
    for (const item of monitored) {
      map.set(item.name.toLowerCase(), item.id);
    }
    return map;
  }, [monitored]);

  const monitoredCompactGridStyle = useMemo(() => {
    if (!isDesktop || monitoredViewMode !== 'compact') {
      return undefined;
    }
    return {
      gridTemplateColumns: `repeat(auto-fill, minmax(${monitoredCompactMinWidth}px, 1fr))`,
    };
  }, [isDesktop, monitoredViewMode, monitoredCompactMinWidth]);

  const monitoredBooksGridStyle = useMemo(() => {
    if (!isDesktop || monitoredBooksViewMode !== 'compact') {
      return undefined;
    }
    return {
      gridTemplateColumns: `repeat(auto-fill, minmax(${monitoredCompactMinWidth}px, 1fr))`,
    };
  }, [isDesktop, monitoredBooksViewMode, monitoredCompactMinWidth]);

  const isUpcomingTab = landingTab === 'upcoming';
  const activeBookGroups = isUpcomingTab ? upcomingBookGroups : monitoredBookGroups;
  const activeBooksCount = isUpcomingTab ? filteredUpcomingMonitoredBooksForTable.length : filteredRegularMonitoredBooksForTable.length;
  const monitoredBooksCountsReady = monitoredLoaded && (monitored.length === 0 || !monitoredBooksLoading);
  const displayAuthorsCount = monitoredLoaded ? monitored.length : (cachedMonitoredCounts?.authors ?? '–');
  const displayBooksCount = monitoredBooksCountsReady ? regularMonitoredBooksForTable.length : (cachedMonitoredCounts?.books ?? '–');
  const displayUpcomingCount = monitoredBooksCountsReady ? upcomingMonitoredBooksForTable.length : (cachedMonitoredCounts?.upcoming ?? '–');
  const displaySearchCount = monitoredLoaded
    ? (searchScope === 'books' ? bookSearchResults.length : authorResults.length)
    : (cachedMonitoredCounts?.search ?? '–');
  const monitoredSearchSortOptions = (metadataSortOptions && metadataSortOptions.length > 0)
    ? metadataSortOptions
    : [{ value: 'relevance', label: 'Most relevant' }];
  const hasStartedSearch = isSearching
    || Boolean(searchError)
    || authorResults.length > 0
    || authorCards.length > 0
    || bookSearchResults.length > 0
    || view === 'search';
  const authorSearchViewOptions = useMemo<ViewModeToggleOption[]>(() => ([
    { value: 'compact', label: 'Compact view', icon: SEARCH_VIEW_ICON_GRID },
    { value: 'list', label: 'List view', icon: SEARCH_VIEW_ICON_LIST },
  ]), []);
  const bookSearchViewOptions = useMemo<ViewModeToggleOption[]>(() => ([
    { value: 'compact', label: 'Compact view', icon: SEARCH_VIEW_ICON_COMPACT_LINES },
    { value: 'list', label: 'List view', icon: SEARCH_VIEW_ICON_LIST },
  ]), []);

  const getMonitoredRowSearchKey = useCallback((book: MonitoredBookListRow): string => {
    const provider = (book.provider || '').trim().toLowerCase();
    const providerId = (book.provider_book_id || '').trim().toLowerCase();
    if (provider && providerId) {
      return `${book.author_entity_id}:${provider}:${providerId}`;
    }
    const title = (book.title || '').trim().toLowerCase();
    const author = (book.author_name || '').trim().toLowerCase();
    return `${book.author_entity_id}::${title}|${author}`;
  }, []);

  const getSearchRowKey = useCallback((row: MonitoredAuthorBookSearchRow): string => {
    const provider = (row.book_provider || '').trim().toLowerCase();
    const providerId = (row.book_provider_id || '').trim().toLowerCase();
    if (provider && providerId) {
      return `${row.entity_id}:${provider}:${providerId}`;
    }
    const title = (row.book_title || '').trim().toLowerCase();
    const author = (row.author_name || '').trim().toLowerCase();
    return `${row.entity_id}::${title}|${author}`;
  }, []);

  const monitoredBookSearchKeySet = useMemo(() => {
    return new Set(regularMonitoredBooksForTable.map(getMonitoredRowSearchKey));
  }, [regularMonitoredBooksForTable, getMonitoredRowSearchKey]);

  const upcomingBookSearchKeySet = useMemo(() => {
    return new Set(upcomingMonitoredBooksForTable.map(getMonitoredRowSearchKey));
  }, [upcomingMonitoredBooksForTable, getMonitoredRowSearchKey]);

  const scopedMonitoredBooksSearchResults = useMemo(() => {
    if (landingTab === 'authors') {
      return monitoredBooksSearchResults;
    }
    const allowedKeys = landingTab === 'upcoming' ? upcomingBookSearchKeySet : monitoredBookSearchKeySet;
    return monitoredBooksSearchResults.filter((row) => allowedKeys.has(getSearchRowKey(row)));
  }, [
    landingTab,
    monitoredBooksSearchResults,
    monitoredBookSearchKeySet,
    upcomingBookSearchKeySet,
    getSearchRowKey,
  ]);

  const getMonitoredBookSelectionKey = useCallback((book: MonitoredBookListRow): string => {
    const provider = (book.provider || 'unknown').trim() || 'unknown';
    const providerBookId = (book.provider_book_id || String(book.id)).trim() || String(book.id);
    return `${book.author_entity_id}:${provider}:${providerBookId}`;
  }, []);

  const selectedMonitoredBookCount = useMemo(
    () => Object.values(selectedMonitoredBookKeys).filter(Boolean).length,
    [selectedMonitoredBookKeys],
  );

  const selectedMonitoredAuthorCount = useMemo(
    () => Object.values(selectedMonitoredAuthorKeys).filter(Boolean).length,
    [selectedMonitoredAuthorKeys],
  );

  const selectedMonitoredAuthors = useMemo(
    () => monitored.filter((author) => selectedMonitoredAuthorKeys[String(author.id)]),
    [monitored, selectedMonitoredAuthorKeys],
  );

  const hasActiveMonitoredAuthorSelection = selectedMonitoredAuthorCount > 0;
  const selectedSingleMonitoredAuthorName = selectedMonitoredAuthors.length === 1
    ? selectedMonitoredAuthors[0]?.name || 'this author'
    : null;

  useEffect(() => {
    const validKeys = new Set(monitoredBooksRows.map((book) => getMonitoredBookSelectionKey(book)));
    setSelectedMonitoredBookKeys((prev) => {
      const next: Record<string, boolean> = {};
      for (const [key, selected] of Object.entries(prev)) {
        if (selected && validKeys.has(key)) {
          next[key] = true;
        }
      }
      return next;
    });
  }, [monitoredBooksRows, getMonitoredBookSelectionKey]);

  const toggleMonitoredBookSelection = useCallback((book: MonitoredBookListRow) => {
    const key = getMonitoredBookSelectionKey(book);
    setSelectedMonitoredBookKeys((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  }, [getMonitoredBookSelectionKey]);

  const toggleMonitoredAuthorSelection = useCallback((authorId: number) => {
    const key = String(authorId);
    setSelectedMonitoredAuthorKeys((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  }, []);

  const runBulkUnmonitorSelected = useCallback(async () => {
    if (bulkUnmonitorRunning) return;

    const selectedRows = monitoredBooksRows.filter((book) => selectedMonitoredBookKeys[getMonitoredBookSelectionKey(book)]);
    if (selectedRows.length === 0) return;

    setBulkUnmonitorRunning(true);
    setMonitoredBooksLoadError(null);
    try {
      const updatesByEntity = new Map<number, Array<{ provider: string; provider_book_id: string; monitor_ebook: boolean; monitor_audiobook: boolean }>>();
      for (const book of selectedRows) {
        const provider = (book.provider || '').trim();
        const providerBookId = (book.provider_book_id || '').trim();
        if (!provider || !providerBookId) {
          continue;
        }
        const existing = updatesByEntity.get(book.author_entity_id) || [];
        existing.push({
          provider,
          provider_book_id: providerBookId,
          monitor_ebook: false,
          monitor_audiobook: false,
        });
        updatesByEntity.set(book.author_entity_id, existing);
      }

      const requests = Array.from(updatesByEntity.entries()).map(([entityId, updates]) =>
        updateMonitoredBooksMonitorFlags(entityId, updates),
      );
      const results = await Promise.allSettled(requests);
      const hasFailure = results.some((result) => result.status === 'rejected');

      const selectedKeys = new Set(selectedRows.map((book) => getMonitoredBookSelectionKey(book)));
      setMonitoredBooksRows((prev) => prev.filter((book) => !selectedKeys.has(getMonitoredBookSelectionKey(book))));
      setSelectedMonitoredBookKeys({});

      if (hasFailure) {
        setMonitoredBooksLoadError('Some books could not be unmonitored, but successful updates were applied.');
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to unmonitor selected books';
      setMonitoredBooksLoadError(message);
    } finally {
      setBulkUnmonitorRunning(false);
    }
  }, [bulkUnmonitorRunning, monitoredBooksRows, selectedMonitoredBookKeys, getMonitoredBookSelectionKey]);

  const runBulkDeleteSelectedAuthors = useCallback(async () => {
    if (bulkDeleteAuthorsRunning) return;

    const selectedAuthors = monitored.filter((author) => selectedMonitoredAuthorKeys[String(author.id)]);
    if (selectedAuthors.length === 0) return;

    setBulkDeleteAuthorsRunning(true);
    setMonitoredError(null);
    try {
      const { successfulIds, failedIds } = await deleteMonitoredAuthorsByIds(
        selectedAuthors.map((author) => author.id),
      );
      const successfulIdSet = new Set(successfulIds);

      if (successfulIdSet.size > 0) {
        setMonitored((prev) => prev.filter((author) => !successfulIdSet.has(author.id)));
        setMonitoredBooksSources((prev) => prev.filter((entity) => !successfulIdSet.has(entity.id)));
        setMonitoredBooksRows((prev) => prev.filter((book) => !successfulIdSet.has(book.author_entity_id)));
        setSelectedMonitoredAuthorKeys((prev) => {
          const next: Record<string, boolean> = {};
          for (const [key, selected] of Object.entries(prev)) {
            if (selected && !successfulIdSet.has(Number(key))) {
              next[key] = true;
            }
          }
          return next;
        });
      }

      if (failedIds.length > 0) {
        setMonitoredError('Some authors could not be deleted, but successful deletions were applied.');
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to delete selected authors';
      setMonitoredError(message);
    } finally {
      setBulkDeleteAuthorsRunning(false);
      setBulkDeleteAuthorsConfirmOpen(false);
    }
  }, [bulkDeleteAuthorsRunning, monitored, selectedMonitoredAuthorKeys]);

  useEffect(() => {
    const validAuthorIds = new Set(monitored.map((author) => String(author.id)));
    setSelectedMonitoredAuthorKeys((prev) => {
      const next: Record<string, boolean> = {};
      for (const [key, selected] of Object.entries(prev)) {
        if (selected && validAuthorIds.has(key)) {
          next[key] = true;
        }
      }
      return next;
    });
  }, [monitored]);

  useEffect(() => {
    if (landingTab !== 'authors') {
      setMonitoredBooksSearchResults([]);
      setMonitoredBooksSearchLoading(false);
      setMonitoredBooksSearchError(null);
      return;
    }

    const q = monitoredBooksSearchQuery.trim();
    if (!q) {
      setMonitoredBooksSearchResults([]);
      setMonitoredBooksSearchLoading(false);
      setMonitoredBooksSearchError(null);
      return;
    }

    let alive = true;
    const timeoutId = window.setTimeout(() => {
      void (async () => {
        setMonitoredBooksSearchLoading(true);
        setMonitoredBooksSearchError(null);
        try {
          const response = await searchMonitoredAuthorBooks(q, 20);
          if (!alive) {
            return;
          }
          setMonitoredBooksSearchResults(Array.isArray(response.results) ? response.results : []);
        } catch (e) {
          if (!alive) {
            return;
          }
          const message = e instanceof Error ? e.message : 'Failed to search monitored books';
          setMonitoredBooksSearchError(message);
          setMonitoredBooksSearchResults([]);
        } finally {
          if (alive) {
            setMonitoredBooksSearchLoading(false);
          }
        }
      })();
    }, 160);

    return () => {
      alive = false;
      window.clearTimeout(timeoutId);
    };
  }, [landingTab, monitoredBooksSearchQuery]);

  useEffect(() => {
    if (landingTab === 'authors') {
      return;
    }
    setMonitoredBooksSearchOpen(false);
  }, [landingTab]);

  useEffect(() => {
    if (!monitoredBooksSearchOpen) {
      return;
    }

    const handlePointerDown = (event: MouseEvent) => {
      if (monitoredBooksSearchRef.current && !monitoredBooksSearchRef.current.contains(event.target as Node)) {
        setMonitoredBooksSearchOpen(false);
        if (!monitoredBooksSearchQuery.trim()) {
          setMonitoredBooksSearchExpanded(false);
        }
      }
    };

    document.addEventListener('mousedown', handlePointerDown);
    return () => {
      document.removeEventListener('mousedown', handlePointerDown);
    };
  }, [monitoredBooksSearchOpen, monitoredBooksSearchQuery]);

  useEffect(() => {
    if (!monitoredBooksSearchExpanded) {
      return;
    }
    const id = window.setTimeout(() => {
      monitoredBooksSearchInputRef.current?.focus();
    }, 0);
    return () => window.clearTimeout(id);
  }, [monitoredBooksSearchExpanded]);

  const runAuthorSearch = useCallback(async () => {
    const q = normalizeAuthor(authorQuery);
    setSearchError(null);
    setAuthorResults([]);
    setAuthorCards([]);
    setBookSearchResults([]);

    if (!q) {
      return;
    }

    setIsSearching(true);
    setLandingTab('search');
    setView('search');
    try {
      if (searchScope === 'books') {
        const result = await searchMetadata(q, 40, bookSearchSortValue, {}, 1, defaultReleaseContentType);
        setBookSearchResults(result.books || []);
        return;
      }

      const authorResponse = await searchMetadataAuthors(q, 20, 1, 'ebook');

      if (authorResponse.supportsAuthors && authorResponse.authors.length > 0) {
        setAuthorCards(authorResponse.authors);
        setAuthorResults(authorResponse.authors.map((a) => a.name));
        return;
      }

      const result = await searchMetadata('', 40, 'relevance', { author: q }, 1, 'ebook');
      const unique = new Map<string, string>();

      result.books.forEach((book) => {
        (book.author || '')
          .split(',')
          .map((name) => normalizeAuthor(name))
          .filter(Boolean)
          .forEach((name) => {
            const key = name.toLowerCase();
            if (!unique.has(key)) {
              unique.set(key, name);
            }
          });
      });

      const results = Array.from(unique.values()).sort((a, b) => a.localeCompare(b));
      setAuthorResults(results);
    } catch (e) {
      const message = e instanceof Error
        ? e.message
        : searchScope === 'books'
          ? 'Failed to search books'
          : 'Failed to search authors';
      setSearchError(message);
    } finally {
      setIsSearching(false);
    }
  }, [authorQuery, bookSearchSortValue, defaultReleaseContentType, searchScope]);

  useEffect(() => {
    if (searchScope !== 'books') {
      return;
    }
    if (!normalizeAuthor(authorQuery)) {
      return;
    }
    void runAuthorSearch();
  }, [authorQuery, bookSearchSortValue, runAuthorSearch, searchScope]);

  useEffect(() => {
    if (!monitoredSearchSortOptions.some((option) => option.value === bookSearchSortValue)) {
      setBookSearchSortValue(monitoredSearchSortOptions[0]?.value || 'relevance');
    }
  }, [bookSearchSortValue, monitoredSearchSortOptions]);

  const openMonitoredTab = useCallback((tab: 'authors' | 'books' | 'upcoming' | 'search') => {
    setLandingTab(tab);
    if (tab === 'search') {
      setView('search');
      if (!authorQuery.trim()) {
        window.setTimeout(() => {
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }, 0);
      }
      return;
    }
    setView('landing');
  }, [authorQuery]);

  const closeBookMonitorModal = useCallback(() => {
    setBookMonitorModalState({
      open: false,
      book: null,
      ebookAuthorDir: '',
      audiobookAuthorDir: '',
      monitorEbook: true,
      monitorAudiobook: true,
    });
    setPathSuggestState({ kind: null, open: false, loading: false, parent: null, entries: [], error: null });
    setEditingMonitorPathKind(null);
  }, []);

  const openBookMonitorModal = useCallback((book: Book) => {
    const primaryAuthor = extractPrimaryAuthorName(book.author || '') || normalizeAuthor(book.title || '') || 'Unknown';
    const ebookSuggestion = monitoredEbookRoots.length > 0 ? joinPath(monitoredEbookRoots[0], primaryAuthor) : '';
    const audioSuggestion = monitoredAudiobookRoots.length > 0 ? joinPath(monitoredAudiobookRoots[0], primaryAuthor) : '';
    setBookMonitorModalState({
      open: true,
      book,
      ebookAuthorDir: ebookSuggestion,
      audiobookAuthorDir: audioSuggestion,
      monitorEbook: true,
      monitorAudiobook: true,
    });
    setPathSuggestState({ kind: null, open: false, loading: false, parent: null, entries: [], error: null });
    setEditingMonitorPathKind(null);
  }, [joinPath, monitoredAudiobookRoots, monitoredEbookRoots]);

  const runBookResultInteractiveSearch = useCallback((book: Book, contentType: ContentType) => {
    if (!onGetReleases) {
      return;
    }
    const actionOverride = contentType === 'ebook'
      ? defaultReleaseActionEbook
      : defaultReleaseActionAudiobook;
    void onGetReleases(book, contentType, null, actionOverride);
  }, [defaultReleaseActionAudiobook, defaultReleaseActionEbook, onGetReleases]);

  const isBookSearchResultMonitored = useCallback((book: Book): boolean => {
    const provider = (book.provider || '').trim().toLowerCase();
    const providerId = (book.provider_id || '').trim().toLowerCase();
    return Boolean(provider && providerId && monitoredSingleBookKeySet.has(`${provider}:${providerId}`));
  }, [monitoredSingleBookKeySet]);

  const getMonitorResultButtonState = useCallback((_bookId: string): ButtonStateInfo => ({
    text: 'Monitor',
    state: 'download',
  }), []);

  const handleBookSearchResultDetails = useCallback(async (bookId: string) => {
    const selected = bookSearchResults.find((book) => book.id === bookId);
    if (!selected) {
      return;
    }
    runBookResultInteractiveSearch(selected, defaultReleaseContentType);
  }, [bookSearchResults, defaultReleaseContentType, runBookResultInteractiveSearch]);

  const noopDownload = useCallback(async (_book: Book) => {
    return;
  }, []);

  const handleBookSearchResultGet = useCallback(async (book: Book) => {
    runBookResultInteractiveSearch(book, defaultReleaseContentType);
  }, [defaultReleaseContentType, runBookResultInteractiveSearch]);

  const confirmMonitorBook = useCallback(async () => {
    const book = bookMonitorModalState.book;
    if (!book) return;

    const provider = (book.provider || '').trim();
    const providerId = (book.provider_id || '').trim();
    if (!provider || !providerId) {
      setMonitoredError('Selected book is missing provider metadata and cannot be monitored.');
      return;
    }

    const monitorEbook = Boolean(bookMonitorModalState.monitorEbook);
    const monitorAudiobook = Boolean(bookMonitorModalState.monitorAudiobook);
    if (!monitorEbook && !monitorAudiobook) {
      setMonitoredError('Enable eBook, Audiobook, or both to monitor this book.');
      return;
    }

    const ebookAuthorDir = normalizeAbsolutePath(bookMonitorModalState.ebookAuthorDir);
    const audiobookAuthorDir = normalizeAbsolutePath(bookMonitorModalState.audiobookAuthorDir);

    if (!ebookAuthorDir && !audiobookAuthorDir) {
      setMonitoredError('Please set an Ebook folder or Audiobook folder.');
      return;
    }

    setMonitoredError(null);
    try {
      const created = await createMonitoredEntity({
        kind: 'book',
        name: (book.title || '').trim() || `${provider}:${providerId}`,
        provider,
        provider_id: providerId,
        settings: {
          photo_url: book.preview,
          book_title: book.title,
          book_author: book.author,
          book_source_url: book.source_url,
          ebook_author_dir: ebookAuthorDir || undefined,
          audiobook_author_dir: audiobookAuthorDir || undefined,
          monitor_ebook: monitorEbook,
          monitor_audiobook: monitorAudiobook,
        },
      });

      setMonitoredBooksSources((prev) => {
        if (prev.some((entity) => entity.id === created.id)) {
          return prev;
        }
        return [
          {
            id: created.id,
            kind: created.kind,
            name: created.name,
            provider: created.provider || undefined,
            provider_id: created.provider_id || undefined,
            cached_source_url: created.cached_source_url || undefined,
            settings: created.settings,
          },
          ...prev,
        ];
      });
      closeBookMonitorModal();
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to monitor book';
      setMonitoredError(message);
    }
  }, [bookMonitorModalState, closeBookMonitorModal, normalizeAbsolutePath]);

  const openMonitorModal = useCallback((payload: { name: string; provider?: string; provider_id?: string; photo_url?: string; books_count?: number }) => {
    const normalized = normalizeAuthor(payload.name);
    if (!normalized) return;

    const ebookSuggestion = monitoredEbookRoots.length > 0 ? joinPath(monitoredEbookRoots[0], normalized) : '';
    const audioSuggestion = monitoredAudiobookRoots.length > 0 ? joinPath(monitoredAudiobookRoots[0], normalized) : '';

    setMonitorModalState({
      open: true,
      author: { ...payload, name: normalized },
      ebookAuthorDir: ebookSuggestion,
      audiobookAuthorDir: audioSuggestion,
      monitorEbookMode: 'missing',
      monitorAudiobookMode: 'missing',
    });
    setPathSuggestState({ kind: null, open: false, loading: false, parent: null, entries: [], error: null });
    setEditingMonitorPathKind(null);
  }, [joinPath, monitoredAudiobookRoots, monitoredEbookRoots]);

  const closeMonitorModal = useCallback(() => {
    setMonitorModalState({
      open: false,
      author: null,
      ebookAuthorDir: '',
      audiobookAuthorDir: '',
      monitorEbookMode: 'missing',
      monitorAudiobookMode: 'missing',
    });
    setPathSuggestState({ kind: null, open: false, loading: false, parent: null, entries: [], error: null });
    setEditingMonitorPathKind(null);
  }, []);

  const splitPathForSuggest = useCallback((raw: string): { parent: string | null; prefix: string } => {
    const value = raw || '';
    if (!value.startsWith('/')) {
      return { parent: null, prefix: '' };
    }
    const lastSlash = value.lastIndexOf('/');
    if (lastSlash <= 0) {
      return { parent: '/', prefix: value.slice(1) };
    }
    const parent = value.slice(0, lastSlash) || '/';
    const prefix = value.slice(lastSlash + 1);
    return { parent, prefix };
  }, []);

  const refreshPathSuggestions = useCallback(async (kind: 'ebook' | 'audiobook', rawValue: string) => {
    const { parent, prefix } = splitPathForSuggest(rawValue);
    if (!parent) {
      setPathSuggestState((prev) => ({ ...prev, kind, open: false, loading: false, parent: null, entries: [], error: null }));
      return;
    }

    setPathSuggestState((prev) => ({ ...prev, kind, open: true, loading: true, parent, entries: [], error: null }));
    try {
      const res = await fsListDirectories(parent);
      const entries = (res.directories || [])
        .filter((d) => !prefix || d.name.toLowerCase().startsWith(prefix.toLowerCase()))
        .slice(0, 12);
      setPathSuggestState((prev) => ({ ...prev, kind, open: true, loading: false, parent, entries, error: null }));
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to list folders';
      setPathSuggestState((prev) => ({ ...prev, kind, open: true, loading: false, parent, entries: [], error: message }));
    }
  }, [splitPathForSuggest]);

  const confirmMonitorAuthor = useCallback(async () => {
    const payload = monitorModalState.author;
    if (!payload) return;

    const normalized = normalizeAuthor(payload.name);
    if (!normalized) return;

    const ebookAuthorDir = normalizeAbsolutePath(monitorModalState.ebookAuthorDir);
    const audiobookAuthorDir = normalizeAbsolutePath(monitorModalState.audiobookAuthorDir);

    if (!ebookAuthorDir && !audiobookAuthorDir) {
      setMonitoredError('Please set an Ebook folder or Audiobook folder.');
      return;
    }

    setMonitoredError(null);
    try {
      const created = await createMonitoredEntity({
        kind: 'author',
        name: normalized,
        provider: payload.provider,
        provider_id: payload.provider_id,
        settings: {
          photo_url: payload.photo_url,
          books_count: payload.books_count,
          ebook_author_dir: ebookAuthorDir || undefined,
          audiobook_author_dir: audiobookAuthorDir || undefined,
          monitor_ebook_mode: monitorModalState.monitorEbookMode,
          monitor_audiobook_mode: monitorModalState.monitorAudiobookMode,
        },
      });

      const learnedEbookRoot = ebookAuthorDir ? deriveRootFromAuthorDir(ebookAuthorDir) : '';
      const learnedAudioRoot = audiobookAuthorDir ? deriveRootFromAuthorDir(audiobookAuthorDir) : '';
      void persistLearnedRoots(learnedEbookRoot, learnedAudioRoot);

      setMonitored((prev) => {
        const next = prev.filter((item) => item.id !== created.id);
        next.unshift({
          id: created.id,
          name: normalized,
          provider: created.provider || payload.provider,
          provider_id: created.provider_id || payload.provider_id,
          photo_url: payload.photo_url,
          books_count: payload.books_count,
        });
        return next;
      });
      setMonitoredBooksSources((prev) => {
        const next = prev.filter((entity) => entity.id !== created.id);
        next.unshift({
          id: created.id,
          kind: 'author',
          name: normalized,
          provider: created.provider || payload.provider,
          provider_id: created.provider_id || payload.provider_id,
          cached_source_url: created.cached_source_url || undefined,
          settings: created.settings,
        });
        return next;
      });
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to monitor author';
      setMonitoredError(message);
      return;
    }

    closeMonitorModal();
    setAuthorQuery('');
    setAuthorResults([]);
    setAuthorCards([]);
    setBookSearchResults([]);
    setSearchError(null);
    setView('landing');
  }, [closeMonitorModal, deriveRootFromAuthorDir, monitorModalState, normalizeAbsolutePath, persistLearnedRoots]);

  const navigateToAuthorPage = useCallback((payload: {
    name: string;
    provider?: string | null;
    provider_id?: string | null;
    source_url?: string | null;
    photo_url?: string | null;
    monitoredEntityId?: number | null;
    initialBookQuery?: string;
    initialBookProvider?: string | null;
    initialBookProviderId?: string | null;
    initialContentType?: ContentType;
    initialAction?: ReleasePrimaryAction;
  }) => {
    const normalized = normalizeAuthor(payload.name);
    if (!normalized) {
      return;
    }

    const params = new URLSearchParams();
    params.set('name', normalized);

    if (payload.provider) params.set('provider', payload.provider);
    if (payload.provider_id) params.set('provider_id', payload.provider_id);
    if (payload.source_url) params.set('source_url', payload.source_url);
    if (payload.photo_url) params.set('photo_url', payload.photo_url);
    if (typeof payload.monitoredEntityId === 'number' && Number.isFinite(payload.monitoredEntityId)) {
      params.set('entity_id', String(payload.monitoredEntityId));
    }

    const initialBookQuery = (payload.initialBookQuery || '').trim();
    const initialBookProvider = (payload.initialBookProvider || '').trim();
    const initialBookProviderId = (payload.initialBookProviderId || '').trim();
    if (initialBookQuery) params.set('initial_query', initialBookQuery);
    if (initialBookProvider) params.set('initial_provider', initialBookProvider);
    if (initialBookProviderId) params.set('initial_provider_id', initialBookProviderId);
    if (payload.initialContentType) params.set('initial_content_type', payload.initialContentType);
    if (payload.initialAction) params.set('initial_action', payload.initialAction);

    navigate(`/monitored/author?${params.toString()}`);
  }, [navigate]);

  const handleMonitoredBookResultSelect = useCallback((row: MonitoredAuthorBookSearchRow) => {
    const matchingAuthor = monitored.find((item) => item.id === row.entity_id);
    const resolvedAuthorName = matchingAuthor?.name || row.author_name;
    if (!resolvedAuthorName) return;

    navigateToAuthorPage({
      name: resolvedAuthorName,
      provider: matchingAuthor?.provider || row.author_provider || null,
      provider_id: matchingAuthor?.provider_id || row.author_provider_id || null,
      source_url: matchingAuthor?.cached_source_url || null,
      photo_url: matchingAuthor?.photo_url || row.author_photo_url || null,
      monitoredEntityId: matchingAuthor?.id ?? row.entity_id,
      initialBookQuery: row.book_title,
      initialBookProvider: row.book_provider || null,
      initialBookProviderId: row.book_provider_id || null,
    });

    setMonitoredBooksSearchQuery('');
    setMonitoredBooksSearchOpen(false);
  }, [monitored, navigateToAuthorPage]);

  const buildBookDetailsPayload = useCallback((book: MonitoredBookListRow): Book => {
    const resolvedAuthor = (book.author_name || '').trim() || 'Unknown author';
    return {
      id: `${book.author_entity_id}:${book.provider || 'unknown'}:${book.provider_book_id || book.id}`,
      title: book.title || 'Unknown title',
      author: resolvedAuthor,
      year: typeof book.publish_year === 'number' ? String(book.publish_year) : undefined,
      preview: book.cover_url || undefined,
      provider: book.provider || undefined,
      provider_id: book.provider_book_id || undefined,
      release_date: book.release_date || undefined,
      isbn_13: book.isbn_13 || undefined,
      source_url: undefined,
      series_name: book.series_name || undefined,
      series_position: book.series_position ?? undefined,
      series_count: book.series_count ?? undefined,
    };
  }, []);

  const openMonitoredBookDetails = useCallback((book: MonitoredBookListRow) => {
    const requestSeq = activeBookRequestSeq.current + 1;
    activeBookRequestSeq.current = requestSeq;

    setActiveBookSourceRow(book);
    setActiveBookEntityId(book.author_entity_id);
    setActiveBookDetails(buildBookDetailsPayload(book));
    setActiveBookFiles([]);

    void (async () => {
      try {
        const response = await listMonitoredBookFiles(book.author_entity_id);
        if (activeBookRequestSeq.current !== requestSeq) {
          return;
        }

        const provider = (book.provider || '').trim();
        const providerBookId = (book.provider_book_id || '').trim();
        const matchingFiles = (response.files || []).filter((file) => {
          if (provider && (file.provider || '').trim() !== provider) {
            return false;
          }
          if (providerBookId && (file.provider_book_id || '').trim() !== providerBookId) {
            return false;
          }
          return true;
        });
        setActiveBookFiles(matchingFiles);
      } catch {
        if (activeBookRequestSeq.current !== requestSeq) {
          return;
        }
        setActiveBookFiles([]);
      }
    })();
  }, [buildBookDetailsPayload]);

  const openMonitoredBookInAuthorPage = useCallback((
    book: MonitoredBookListRow,
    contentType?: ContentType,
    actionOverride?: ReleasePrimaryAction,
  ) => {
    const authorName = book.author_name || 'Unknown author';
    navigateToAuthorPage({
      name: authorName,
      provider: book.author_provider || 'hardcover',
      provider_id: book.author_provider_id || authorName,
      source_url: book.author_source_url || null,
      photo_url: book.author_photo_url || null,
      monitoredEntityId: book.author_entity_id,
      initialBookQuery: book.title || undefined,
      initialBookProvider: book.provider || null,
      initialBookProviderId: book.provider_book_id || null,
      initialContentType: contentType,
      initialAction: actionOverride,
    });
  }, [navigateToAuthorPage]);

  const renderMonitoredBookActions = useCallback((book: MonitoredBookListRow, compact = false) => {
    const menuContent = ({ close }: { close: () => void }) => (
      <div className="py-1">
        <button
          type="button"
          onClick={() => {
            close();
            openMonitoredBookDetails(book);
          }}
          className="w-full px-3 py-2 text-left text-sm hover-surface"
        >
          Open details
        </button>
        <div className="my-1 border-t border-[var(--border-muted)]" />
        <button
          type="button"
          onClick={() => {
            close();
            openMonitoredBookInAuthorPage(book, 'ebook', 'interactive_search');
          }}
          className="w-full px-3 py-2 text-left text-sm hover-surface"
        >
          Search eBooks
        </button>
        <button
          type="button"
          onClick={() => {
            close();
            openMonitoredBookInAuthorPage(book, 'audiobook', 'interactive_search');
          }}
          className="w-full px-3 py-2 text-left text-sm hover-surface"
        >
          Search audiobooks
        </button>
      </div>
    );

    if (compact) {
      return (
        <Dropdown
          widthClassName="w-auto"
          align="right"
          panelClassName="z-[2200] min-w-[220px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
          renderTrigger={({ isOpen, toggle }) => (
            <button
              type="button"
              onClick={toggle}
              className={`inline-flex items-center justify-center rounded-full text-gray-600 dark:text-gray-200 hover-action transition-colors h-6 w-6 ${isOpen ? 'text-gray-900 dark:text-gray-100' : ''}`}
              aria-label={`Book actions for ${book.title || 'this book'}`}
              title="Book actions"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM12 12.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM12 18.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Z" />
              </svg>
            </button>
          )}
        >
          {menuContent}
        </Dropdown>
      );
    }

    return (
      <div className="inline-flex items-stretch rounded-lg border border-[var(--border-muted)]">
        <button
          type="button"
          onClick={() => openMonitoredBookDetails(book)}
          className="inline-flex items-center justify-center h-8 w-8 text-gray-600 dark:text-gray-200 hover-action"
          aria-label={`Open default action for ${book.title || 'this book'}`}
          title="Open details"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 6.75A2.25 2.25 0 0 1 6.75 4.5h4.5A2.25 2.25 0 0 1 13.5 6.75v12A2.25 2.25 0 0 0 11.25 16.5h-4.5A2.25 2.25 0 0 0 4.5 18.75v-12Zm9 0A2.25 2.25 0 0 1 15.75 4.5h1.5A2.25 2.25 0 0 1 19.5 6.75v12a2.25 2.25 0 0 0-2.25-2.25h-1.5A2.25 2.25 0 0 0 13.5 18.75v-12Z" />
          </svg>
        </button>

        <Dropdown
          widthClassName="w-auto"
          align="right"
          panelClassName="z-[2200] min-w-[220px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
          renderTrigger={({ isOpen, toggle }) => (
            <button
              type="button"
              onClick={toggle}
              className={`inline-flex items-center justify-center h-8 w-7 border-l border-[var(--border-muted)] text-gray-600 dark:text-gray-200 hover-action ${isOpen ? 'bg-black/5 dark:bg-white/10' : ''}`}
              aria-label={`More actions for ${book.title || 'this book'}`}
              title="More actions"
            >
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" />
              </svg>
            </button>
          )}
        >
          {menuContent}
        </Dropdown>
      </div>
    );
  }, [openMonitoredBookDetails, openMonitoredBookInAuthorPage]);

  const clearSearchAndReturn = useCallback(() => {
    setAuthorQuery('');
    setAuthorResults([]);
    setAuthorCards([]);
    setBookSearchResults([]);
    setSearchError(null);
    setView(landingTab === 'search' ? 'search' : 'landing');
  }, [landingTab]);

  const handleHeaderAuthorSearchChange = useCallback((value: string) => {
    setAuthorQuery(value);
    if (!value.trim()) {
      clearSearchAndReturn();
    }
  }, [clearSearchAndReturn]);

  const handleSearchScopeChange = useCallback((value: string) => {
    const nextScope: 'authors' | 'books' = value === 'books' ? 'books' : 'authors';
    setSearchScope(nextScope);
    setSearchError(null);
    setAuthorResults([]);
    setAuthorCards([]);
    setBookSearchResults([]);
    if (!authorQuery.trim()) {
      setView(landingTab === 'search' ? 'search' : 'landing');
    }
  }, [authorQuery, landingTab]);

  const isAuthorDetailsRoute = location.pathname === '/monitored/author';
  const authorDetailsSearchParams = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const handleMonitoredHeaderSearch = useCallback(() => {
    if (isAuthorDetailsRoute) {
      navigate('/monitored');
    }
    void runAuthorSearch();
  }, [isAuthorDetailsRoute, navigate, runAuthorSearch]);

  const monitoredHeader = (
    <Header
      showSearch
      logoUrl={logoUrl}
      searchInput={authorQuery}
      searchPlaceholder="Search authors to monitor.."
      onSearchChange={handleHeaderAuthorSearchChange}
      onSearch={handleMonitoredHeaderSearch}
      searchScopeOptions={MONITORED_SEARCH_SCOPE_OPTIONS}
      searchScopeValue={searchScope}
      onSearchScopeChange={handleSearchScopeChange}
      isLoading={isSearching}
      onDownloadsClick={onActivityClick}
      isActivityOpen={isActivityOpen}
      onLogoClick={onBack}
      debug={debug}
      onMonitoredClick={onMonitoredClick}
      activeTopNav="monitoring"
      onSettingsClick={onSettingsClick}
      statusCounts={statusCounts}
      isAdmin={isAdmin}
      canAccessSettings={canAccessSettings}
      authRequired={authRequired}
      isAuthenticated={isAuthenticated}
      username={username}
      displayName={displayName}
      onLogout={onLogout}
    />
  );

  const authorDetailsAuthor = useMemo<AuthorModalAuthor | null>(() => {
    if (!isAuthorDetailsRoute) {
      return null;
    }
    const name = (authorDetailsSearchParams.get('name') || '').trim();
    if (!name) {
      return null;
    }
    const provider = (authorDetailsSearchParams.get('provider') || '').trim();
    const providerId = (authorDetailsSearchParams.get('provider_id') || '').trim();
    const sourceUrl = (authorDetailsSearchParams.get('source_url') || '').trim();
    const photoUrl = (authorDetailsSearchParams.get('photo_url') || '').trim();

    return {
      name,
      provider: provider || null,
      provider_id: providerId || null,
      source_url: sourceUrl || null,
      photo_url: photoUrl || null,
    };
  }, [isAuthorDetailsRoute, authorDetailsSearchParams]);

  const authorDetailsMonitoredEntityId = useMemo(() => {
    if (!isAuthorDetailsRoute) {
      return null;
    }
    const raw = (authorDetailsSearchParams.get('entity_id') || '').trim();
    if (!raw) {
      return null;
    }
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : null;
  }, [isAuthorDetailsRoute, authorDetailsSearchParams]);

  const authorDetailsInitialBooksQuery = (authorDetailsSearchParams.get('initial_query') || '').trim();
  const authorDetailsInitialBookProvider = (authorDetailsSearchParams.get('initial_provider') || '').trim() || undefined;
  const authorDetailsInitialBookProviderId = (authorDetailsSearchParams.get('initial_provider_id') || '').trim() || undefined;
  const authorDetailsInitialContentTypeParam = (authorDetailsSearchParams.get('initial_content_type') || '').trim();
  const authorDetailsInitialActionParam = (authorDetailsSearchParams.get('initial_action') || '').trim();
  const authorDetailsInitialContentTypeOverride: ContentType | undefined = authorDetailsInitialContentTypeParam === 'audiobook'
    ? 'audiobook'
    : authorDetailsInitialContentTypeParam === 'ebook'
      ? 'ebook'
      : undefined;
  const authorDetailsInitialActionOverride: ReleasePrimaryAction | undefined = authorDetailsInitialActionParam === 'auto_search_download'
    ? 'auto_search_download'
    : authorDetailsInitialActionParam === 'interactive_search'
      ? 'interactive_search'
      : undefined;
  const authorDetailsEffectiveDefaultContentType = authorDetailsInitialContentTypeOverride ?? defaultReleaseContentType;
  const authorDetailsEffectiveDefaultActionEbook: ReleasePrimaryAction = authorDetailsEffectiveDefaultContentType === 'ebook' && authorDetailsInitialActionOverride
    ? authorDetailsInitialActionOverride
    : defaultReleaseActionEbook;
  const authorDetailsEffectiveDefaultActionAudiobook: ReleasePrimaryAction = authorDetailsEffectiveDefaultContentType === 'audiobook' && authorDetailsInitialActionOverride
    ? authorDetailsInitialActionOverride
    : defaultReleaseActionAudiobook;

  if (isAuthorDetailsRoute) {
    return (
      <div className="min-h-screen" style={{ backgroundColor: 'var(--background-color)', color: 'var(--text-color)' }}>
        <div className="fixed top-0 left-0 right-0 z-40">
          {monitoredHeader}
        </div>

        <main className="relative w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 pt-32 lg:pt-24">
          {authorDetailsAuthor ? (
            <AuthorModal
              author={authorDetailsAuthor}
              displayMode="page"
              onClose={() => navigate('/monitored')}
              onGetReleases={onGetReleases}
              defaultReleaseContentType={authorDetailsEffectiveDefaultContentType}
              defaultReleaseActionEbook={authorDetailsEffectiveDefaultActionEbook}
              defaultReleaseActionAudiobook={authorDetailsEffectiveDefaultActionAudiobook}
              initialBooksQuery={authorDetailsInitialBooksQuery || undefined}
              initialBookProvider={authorDetailsInitialBookProvider}
              initialBookProviderId={authorDetailsInitialBookProviderId}
              monitoredEntityId={authorDetailsMonitoredEntityId}
              status={status}
            />
          ) : (
            <section className="rounded-2xl border border-black/10 dark:border-white/10 bg-white/80 dark:bg-white/5 p-5">
              <div className="text-sm text-gray-600 dark:text-gray-300">Missing author details in URL.</div>
              <button
                type="button"
                onClick={() => navigate('/monitored')}
                className="mt-3 px-4 py-2 rounded-full bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium"
              >
                Back to Monitored
              </button>
            </section>
          )}
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen" style={{ backgroundColor: 'var(--background-color)', color: 'var(--text-color)' }}>
      <div className="fixed top-0 left-0 right-0 z-40">
        {monitoredHeader}
      </div>

      <main className="relative w-full max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6 pt-32 lg:pt-24">
        <div className="flex flex-col gap-6">
          {searchError || monitoredError || rootsError ? (
            <div className="flex flex-col gap-3">
              {searchError && (
                <div className="text-sm text-red-500">{searchError}</div>
              )}

              {monitoredError && (
                <div className="text-sm text-red-500">{monitoredError}</div>
              )}

              {rootsError && (
                <div className="text-sm text-red-500">{rootsError}</div>
              )}
            </div>
          ) : null}

      {bookMonitorModalState.open && bookMonitorModalState.book ? (
        <div
          className="modal-overlay active sm:px-6 sm:py-6"
          style={{ zIndex: 1200 }}
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              closeBookMonitorModal();
            }
          }}
        >
          <div
            className="details-container w-full max-w-lg h-auto settings-modal-enter"
            role="dialog"
            aria-modal="true"
            aria-label="Monitor book"
          >
            <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-2xl overflow-hidden">
              <header className="flex items-start justify-between gap-3 border-b border-[var(--border-muted)] px-5 py-4">
                <div className="min-w-0">
                  <div className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Monitor book</div>
                  <div className="mt-1 text-base font-semibold truncate">{bookMonitorModalState.book.title || 'Unknown title'}</div>
                  <div className="text-xs text-gray-500 dark:text-gray-400 truncate">{bookMonitorModalState.book.author || 'Unknown author'}</div>
                </div>
                <button
                  type="button"
                  onClick={closeBookMonitorModal}
                  className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
                  aria-label="Close"
                >
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </header>

              <div className="px-5 py-4 space-y-4">
                <div className="flex items-center gap-2">
                  <label className="inline-flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
                    <input
                      type="checkbox"
                      checked={bookMonitorModalState.monitorEbook}
                      onChange={(e) => setBookMonitorModalState((prev) => ({ ...prev, monitorEbook: e.target.checked }))}
                      className="accent-emerald-600"
                    />
                    Monitor eBook
                  </label>
                  <label className="inline-flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
                    <input
                      type="checkbox"
                      checked={bookMonitorModalState.monitorAudiobook}
                      onChange={(e) => setBookMonitorModalState((prev) => ({ ...prev, monitorAudiobook: e.target.checked }))}
                      className="accent-emerald-600"
                    />
                    Monitor Audiobook
                  </label>
                </div>

                <div className="space-y-2">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Ebook folder</div>
                  <div className="space-y-2">
                    {(() => {
                      const authorName = extractPrimaryAuthorName(bookMonitorModalState.book?.author || '');
                      const rootValue = stripTrailingAuthorName(bookMonitorModalState.ebookAuthorDir, authorName);
                      const suffix = authorName ? `/${authorName}` : '';
                      return (
                        <>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => {
                                setFolderBrowserState({ open: true, kind: 'ebook', initialPath: rootValue || null });
                              }}
                              className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                            >
                              Browse
                            </button>
                            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">Pick the target folder.</div>
                          </div>
                          <div className="flex items-center gap-2">
                            <div className="flex-1 px-3 py-2 rounded-xl bg-white/60 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm break-all">
                              <span className="text-gray-900 dark:text-gray-100">{rootValue || '—'}</span>
                              {suffix ? <span className="text-gray-400 dark:text-gray-500">{suffix}</span> : null}
                            </div>
                            <button
                              type="button"
                              onClick={() => setEditingMonitorPathKind('ebook')}
                              className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                            >
                              Edit
                            </button>
                          </div>
                        </>
                      );
                    })()}
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Audiobook folder</div>
                  <div className="space-y-2">
                    {(() => {
                      const authorName = extractPrimaryAuthorName(bookMonitorModalState.book?.author || '');
                      const rootValue = stripTrailingAuthorName(bookMonitorModalState.audiobookAuthorDir, authorName);
                      const suffix = authorName ? `/${authorName}` : '';
                      return (
                        <>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => {
                                setFolderBrowserState({ open: true, kind: 'audiobook', initialPath: rootValue || null });
                              }}
                              className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                            >
                              Browse
                            </button>
                            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">Pick the target folder.</div>
                          </div>
                          <div className="flex items-center gap-2">
                            <div className="flex-1 px-3 py-2 rounded-xl bg-white/60 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm break-all">
                              <span className="text-gray-900 dark:text-gray-100">{rootValue || '—'}</span>
                              {suffix ? <span className="text-gray-400 dark:text-gray-500">{suffix}</span> : null}
                            </div>
                            <button
                              type="button"
                              onClick={() => setEditingMonitorPathKind('audiobook')}
                              className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                            >
                              Edit
                            </button>
                          </div>
                        </>
                      );
                    })()}
                  </div>
                </div>
              </div>

              <footer className="flex items-center justify-end gap-2 border-t border-[var(--border-muted)] px-5 py-4 bg-[var(--bg)] sm:bg-[var(--bg-soft)]">
                <button
                  type="button"
                  onClick={closeBookMonitorModal}
                  className="px-4 py-2 rounded-full bg-white/70 hover:bg-white text-gray-900 font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void confirmMonitorBook()}
                  className="px-4 py-2 rounded-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white font-medium"
                >
                  Monitor
                </button>
              </footer>
            </div>
          </div>
        </div>
      ) : null}

          {(view === 'landing' && landingTab !== 'search') ? (
            (!monitoredLoaded && monitored.length === 0) ? (
              <div className="rounded-2xl bg-white/0 dark:bg-white/0 py-10">
                <div className="mx-auto max-w-md text-center">
                  <div className="inline-flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
                    <span className="inline-block h-2 w-2 rounded-full bg-emerald-500 animate-pulse" aria-hidden="true" />
                    Loading monitored authors…
                  </div>
                </div>
              </div>
            ) : monitored.length === 0 ? (
              <div className="rounded-2xl bg-white/0 dark:bg-white/0 py-10">
                <div className="mx-auto max-w-md text-center">
                  <div className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-2xl bg-black/5 dark:bg-white/10">
                    <svg
                      className="h-6 w-6 text-gray-500 dark:text-gray-400"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                      strokeWidth={1.5}
                      aria-hidden="true"
                    >
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z"
                      />
                    </svg>
                  </div>
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">No monitored authors</div>
                  <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">Search for an author above to start monitoring.</div>
                </div>
              </div>
            ) : (
              <section className="rounded-2xl border border-black/10 dark:border-white/10 bg-white/80 dark:bg-white/5 p-4">
                <div className="flex items-center justify-between mb-3 pb-2 border-b border-black/10 dark:border-white/10 relative z-10 gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <button
                      type="button"
                      onClick={() => {
                        if (onBack) {
                          onBack();
                          return;
                        }
                        navigate('/');
                      }}
                      className="rounded-full p-1.5 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
                      aria-label="Back to home"
                      title="Back"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.5 7.5 12 15 4.5" />
                      </svg>
                    </button>
                    <div className="inline-flex items-center rounded-full border border-[var(--border-muted)] bg-transparent">
                      <button
                        type="button"
                        onClick={() => openMonitoredTab('authors')}
                        className={`px-3.5 py-2 rounded-full text-xs font-medium transition-colors ${landingTab === 'authors' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                        aria-pressed={landingTab === 'authors'}
                      >
                        Monitored Authors
                        <span className="ml-1 opacity-85">{displayAuthorsCount}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => openMonitoredTab('books')}
                        className={`px-3.5 py-2 rounded-full text-xs font-medium transition-colors ${landingTab === 'books' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                        aria-pressed={landingTab === 'books'}
                      >
                        Monitored Books
                        <span className="ml-1 opacity-85">{displayBooksCount}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => openMonitoredTab('upcoming')}
                        className={`px-3.5 py-2 rounded-full text-xs font-medium transition-colors ${landingTab === 'upcoming' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                        aria-pressed={landingTab === 'upcoming'}
                      >
                        Upcoming
                        <span className="ml-1 opacity-85">{displayUpcomingCount}</span>
                      </button>
                      {hasStartedSearch ? (
                        <button
                          type="button"
                          onClick={() => openMonitoredTab('search')}
                          className="px-3.5 py-2 rounded-full text-xs font-medium transition-colors text-gray-700 dark:text-gray-200 hover-action"
                          aria-pressed={false}
                        >
                          Search
                          <span className="ml-1 opacity-85">{displaySearchCount}</span>
                        </button>
                      ) : null}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-wrap justify-end">
                    {landingTab === 'authors' ? (
                      <div className="relative h-8 w-8 shrink-0">
                        {selectedMonitoredAuthorCount > 0 ? (
                          <button
                            type="button"
                            onClick={() => setBulkDeleteAuthorsConfirmOpen(true)}
                            className="absolute inset-0 flex items-center justify-center p-1 rounded-full border border-red-500/40 text-red-600 dark:text-red-400 hover-action"
                            title={`Delete selected authors (${selectedMonitoredAuthorCount})`}
                            aria-label={`Delete selected authors (${selectedMonitoredAuthorCount})`}
                          >
                            <svg className="w-5 h-5" fill="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                              <path d="M9 3.75A1.5 1.5 0 0 1 10.5 2.25h3A1.5 1.5 0 0 1 15 3.75v.75h3.75a.75.75 0 0 1 0 1.5h-.53l-.64 11.32A2.25 2.25 0 0 1 15.34 19.5H8.66a2.25 2.25 0 0 1-2.24-2.18L5.78 6h-.53a.75.75 0 0 1 0-1.5H9v-.75Zm2.25 0v.75h1.5v-.75h-1.5Zm-.7 5.18a.75.75 0 0 0-1.06 1.06L10.94 12l-1.45 2.01a.75.75 0 1 0 1.22.88L12 13.06l1.29 1.83a.75.75 0 0 0 1.22-.88L13.06 12l1.45-2.01a.75.75 0 1 0-1.22-.88L12 10.94l-1.45-2.01Z" />
                            </svg>
                            <span className="absolute -top-1 -right-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white leading-none">
                              {selectedMonitoredAuthorCount}
                            </span>
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                    <div className="relative" ref={monitoredBooksSearchRef}>
                        {!monitoredBooksSearchExpanded ? (
                          <button
                            type="button"
                            onClick={() => {
                              setMonitoredBooksSearchExpanded(true);
                              setMonitoredBooksSearchOpen(Boolean(monitoredBooksSearchQuery.trim()));
                            }}
                            className={`p-2 rounded-full transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${monitoredBooksSearchQuery.trim() ? 'text-white bg-emerald-600 hover:bg-emerald-700' : 'hover-action text-gray-900 dark:text-gray-100'}`}
                            title="Search monitored books"
                            aria-label="Search monitored books"
                          >
                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8" aria-hidden="true">
                              <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35m1.35-5.15a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z" />
                            </svg>
                          </button>
                        ) : (
                          <div className="relative w-[min(92vw,420px)]">
                            <div className="flex items-center gap-2 rounded-full border border-[var(--border-muted)] px-3 py-1.5 bg-white/70 dark:bg-white/10">
                              <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8" aria-hidden="true">
                                <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35m1.35-5.15a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z" />
                              </svg>
                              <input
                                ref={monitoredBooksSearchInputRef}
                                value={monitoredBooksSearchQuery}
                                onChange={(e) => {
                                  setMonitoredBooksSearchQuery(e.target.value);
                                  if (landingTab === 'authors') {
                                    setMonitoredBooksSearchOpen(true);
                                  }
                                }}
                                onFocus={() => {
                                  if (landingTab === 'authors') {
                                    setMonitoredBooksSearchOpen(true);
                                  }
                                }}
                                onKeyDown={(e) => {
                                  if (e.key === 'Escape') {
                                    setMonitoredBooksSearchOpen(false);
                                    setMonitoredBooksSearchExpanded(false);
                                    return;
                                  }
                                  if (landingTab === 'authors' && e.key === 'Enter' && scopedMonitoredBooksSearchResults.length > 0) {
                                    e.preventDefault();
                                    handleMonitoredBookResultSelect(scopedMonitoredBooksSearchResults[0]);
                                  }
                                }}
                                placeholder={landingTab === 'authors' ? 'Search monitored books' : 'Filter visible books'}
                                className="w-full bg-transparent outline-none text-xs text-gray-700 dark:text-gray-200 placeholder:text-gray-500"
                                aria-label="Search monitored books"
                              />
                              {monitoredBooksSearchQuery ? (
                                <button
                                  type="button"
                                  onClick={() => {
                                    setMonitoredBooksSearchQuery('');
                                    setMonitoredBooksSearchOpen(false);
                                  }}
                                  className="p-0.5 rounded-full text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action"
                                  aria-label="Clear monitored books search"
                                  title="Clear"
                                >
                                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                                  </svg>
                                </button>
                              ) : null}
                              <button
                                type="button"
                                onClick={() => {
                                  setMonitoredBooksSearchExpanded(false);
                                  setMonitoredBooksSearchOpen(false);
                                }}
                                className="p-0.5 rounded-full text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action"
                                aria-label="Collapse monitored books search"
                                title="Collapse search"
                              >
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.5 7.5 12 15 4.5" />
                                </svg>
                              </button>
                            </div>

                            {landingTab === 'authors' && monitoredBooksSearchOpen && monitoredBooksSearchQuery.trim() ? (
                              <div className="absolute right-0 mt-2 w-full max-h-72 overflow-y-auto rounded-xl border border-[var(--border-muted)] bg-[var(--bg)] shadow-2xl z-[120]">
                                {monitoredBooksSearchLoading ? (
                                  <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">Searching…</div>
                                ) : monitoredBooksSearchError ? (
                                  <div className="px-3 py-2 text-xs text-red-500">{monitoredBooksSearchError}</div>
                                ) : scopedMonitoredBooksSearchResults.length === 0 ? (
                                  <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
                                    No monitored database matches.
                                  </div>
                                ) : (
                                  <div className="py-1">
                                    {scopedMonitoredBooksSearchResults.map((row) => {
                                      const hasEpub = row.has_epub === true || row.has_epub === 1;
                                      const hasM4b = row.has_m4b === true || row.has_m4b === 1;
                                      const hasDownload = hasEpub || hasM4b;
                                      const hasSeries = Boolean(row.series_name);
                                      const seriesLabel = hasSeries
                                        ? `${row.series_name}${row.series_position != null ? ` #${row.series_position}` : ''}${row.series_count != null ? `/${row.series_count}` : ''}`
                                        : '';
                                      const authorYearLine = row.publish_year
                                        ? `${row.author_name} • ${row.publish_year}`
                                        : row.author_name;
                                      return (
                                        <button
                                          key={`${row.entity_id}:${row.book_provider || 'unknown'}:${row.book_provider_id || row.book_title}:${row.publish_year ?? 'na'}:${row.series_position ?? 'na'}`}
                                          type="button"
                                          onClick={() => handleMonitoredBookResultSelect(row)}
                                          className={`w-full text-left px-3 py-2 border-b last:border-b-0 border-black/5 dark:border-white/5 hover-surface ${hasDownload ? 'bg-emerald-500/[0.07] dark:bg-emerald-500/[0.09]' : ''}`}
                                        >
                                          <div className="min-h-[84px] flex items-center justify-between gap-3">
                                            <div className="min-w-0 flex-1">
                                              <div className="text-sm font-semibold text-gray-900 dark:text-gray-100 truncate">{row.book_title}</div>
                                              <div className="mt-0.5 text-xs text-gray-500 dark:text-gray-400 truncate italic">
                                                {authorYearLine}
                                              </div>
                                              <div className="mt-1.5 h-5 flex items-center gap-2 text-[11px]">
                                                {hasSeries ? (
                                                  <span className="inline-flex items-center truncate max-w-full text-sky-700 dark:text-sky-300" title={seriesLabel}>
                                                    {seriesLabel}
                                                  </span>
                                                ) : null}
                                              </div>
                                            </div>
                                            <div className="w-[92px] flex items-center justify-end gap-1 shrink-0">
                                              {hasEpub ? (
                                                <span className="inline-flex items-center justify-center min-w-[40px] px-1.5 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase bg-emerald-500/20 text-emerald-700 dark:text-emerald-300">EPUB</span>
                                              ) : null}
                                              {hasM4b ? (
                                                <span className="inline-flex items-center justify-center min-w-[40px] px-1.5 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase bg-violet-500/20 text-violet-700 dark:text-violet-300">M4B</span>
                                              ) : null}
                                            </div>
                                          </div>
                                          {(hasEpub || hasM4b) ? null : (
                                            <div className="sr-only">No downloaded files found</div>
                                          )}
                                        </button>
                                      );
                                    })}
                                  </div>
                                )}
                              </div>
                            ) : null}
                          </div>
                        )}
                      </div>
                    {landingTab === 'authors' ? (
                      <>
                        <ViewModeToggle
                          value={monitoredViewMode}
                          onChange={(next) => setMonitoredViewMode(next as 'compact' | 'table')}
                          options={[
                            {
                              value: 'table',
                              label: 'Table view',
                              icon: (
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 6.75h15m-15 5.25h15m-15 5.25h15" />
                                </svg>
                              ),
                            },
                            {
                              value: 'compact',
                              label: 'Compact view',
                              icon: (
                                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8">
                                  <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 4.5h6.75v6.75H4.5V4.5Zm8.25 0h6.75v6.75h-6.75V4.5ZM4.5 12.75h6.75v6.75H4.5v-6.75Zm8.25 0h6.75v6.75h-6.75v-6.75Z" />
                                </svg>
                              ),
                            },
                          ]}
                        />
                        <Dropdown
                          align="right"
                          widthClassName="w-auto"
                          panelClassName="z-[2200] min-w-[280px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
                          renderTrigger={({ isOpen, toggle }) => (
                            <button
                              type="button"
                              onClick={toggle}
                              className={`p-2 rounded-full transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${isOpen ? 'text-white bg-emerald-600 hover:bg-emerald-700' : 'hover-action text-gray-900 dark:text-gray-100'}`}
                              title="Author view settings"
                              aria-label="Author view settings"
                              aria-expanded={isOpen}
                            >
                              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8">
                                <circle cx="12" cy="5" r="1.5" />
                                <circle cx="12" cy="12" r="1.5" />
                                <circle cx="12" cy="19" r="1.5" />
                              </svg>
                            </button>
                          )}
                        >
                          {() => (
                            <div className="px-3 py-3 space-y-3">
                              <div>
                                <div className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">Sort</div>
                                <div className="space-y-1" role="listbox" aria-label="Sort monitored authors">
                                  <button
                                    type="button"
                                    className={`w-full px-2.5 py-1.5 rounded-lg text-left text-sm hover-surface ${monitoredSortBy === 'alphabetical' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setMonitoredSortBy('alphabetical')}
                                    role="option"
                                    aria-selected={monitoredSortBy === 'alphabetical'}
                                  >
                                    Alphabetical (A–Z)
                                  </button>
                                  <button
                                    type="button"
                                    className={`w-full px-2.5 py-1.5 rounded-lg text-left text-sm hover-surface ${monitoredSortBy === 'date_added' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setMonitoredSortBy('date_added')}
                                    role="option"
                                    aria-selected={monitoredSortBy === 'date_added'}
                                  >
                                    Date added
                                  </button>
                                  <button
                                    type="button"
                                    className={`w-full px-2.5 py-1.5 rounded-lg text-left text-sm hover-surface ${monitoredSortBy === 'books_count' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setMonitoredSortBy('books_count')}
                                    role="option"
                                    aria-selected={monitoredSortBy === 'books_count'}
                                  >
                                    Number of books
                                  </button>
                                </div>
                              </div>

                              <div>
                                <div className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">Size</div>
                                <input
                                  type="range"
                                  min={MONITORED_COMPACT_MIN_WIDTH_MIN}
                                  max={MONITORED_COMPACT_MIN_WIDTH_MAX}
                                  step={5}
                                  value={monitoredCompactMinWidth}
                                  onChange={(e) => setMonitoredCompactMinWidth(Number(e.target.value))}
                                  className="w-full accent-emerald-600"
                                  aria-label="Compact card size"
                                  title="Compact card size"
                                  disabled={monitoredViewMode !== 'compact'}
                                />
                                <div className="mt-1 text-[11px] text-gray-500 dark:text-gray-400 tabular-nums text-right">
                                  {monitoredCompactMinWidth}px
                                </div>
                                {monitoredViewMode !== 'compact' ? (
                                  <div className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">Switch to compact view to adjust grid size.</div>
                                ) : null}
                              </div>
                            </div>
                          )}
                        </Dropdown>
                      </>
                    ) : (
                      <>
                        {selectedMonitoredBookCount > 0 ? (
                          <button
                            type="button"
                            onClick={() => void runBulkUnmonitorSelected()}
                            disabled={bulkUnmonitorRunning}
                            className="relative p-2 rounded-full border border-red-500/40 text-red-600 dark:text-red-400 hover-action disabled:opacity-50 disabled:cursor-not-allowed"
                            title={bulkUnmonitorRunning ? 'Unmonitoring selected books' : `Unmonitor selected books (${selectedMonitoredBookCount})`}
                            aria-label={bulkUnmonitorRunning ? 'Unmonitoring selected books' : `Unmonitor selected books (${selectedMonitoredBookCount})`}
                          >
                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M6 7.5h12m-1.5 0-.8 11.2a2.25 2.25 0 0 1-2.24 2.09H10.54A2.25 2.25 0 0 1 8.3 18.7L7.5 7.5m3-3h3a1.5 1.5 0 0 1 1.5 1.5V7.5h-6V6a1.5 1.5 0 0 1 1.5-1.5Z" />
                            </svg>
                            <span className="absolute -top-1 -right-1 inline-flex h-4 min-w-4 items-center justify-center rounded-full bg-red-600 px-1 text-[10px] font-semibold text-white leading-none">
                              {selectedMonitoredBookCount}
                            </span>
                          </button>
                        ) : null}
                        <ViewModeToggle
                          value={monitoredBooksViewMode}
                          onChange={(next) => setMonitoredBooksViewMode(next as 'table' | 'compact')}
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
                              icon: SEARCH_VIEW_ICON_COMPACT_LINES,
                            },
                          ]}
                        />
                        <Dropdown
                          align="right"
                          widthClassName="w-auto"
                          panelClassName="z-[2200] min-w-[280px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
                          renderTrigger={({ isOpen, toggle }) => (
                            <button
                              type="button"
                              onClick={toggle}
                              className={`p-2 rounded-full transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${isOpen ? 'text-white bg-emerald-600 hover:bg-emerald-700' : 'hover-action text-gray-900 dark:text-gray-100'}`}
                              title="Books view settings"
                              aria-label="Books view settings"
                              aria-expanded={isOpen}
                            >
                              <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                                <circle cx="12" cy="5" r="1.5" />
                                <circle cx="12" cy="12" r="1.5" />
                                <circle cx="12" cy="19" r="1.5" />
                              </svg>
                            </button>
                          )}
                        >
                          {() => (
                            <div className="px-3 py-3 space-y-3">
                              <div>
                                <div className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">Sort</div>
                                <div className="space-y-1" role="listbox" aria-label="Sort monitored books">
                                  <button
                                    type="button"
                                    className={`w-full px-2.5 py-1.5 rounded-lg text-left text-sm hover-surface ${monitoredBooksSortBy === 'alphabetical' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setMonitoredBooksSortBy('alphabetical')}
                                    role="option"
                                    aria-selected={monitoredBooksSortBy === 'alphabetical'}
                                  >
                                    Alphabetical (A-Z)
                                  </button>
                                  <button
                                    type="button"
                                    className={`w-full px-2.5 py-1.5 rounded-lg text-left text-sm hover-surface ${monitoredBooksSortBy === 'year' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setMonitoredBooksSortBy('year')}
                                    role="option"
                                    aria-selected={monitoredBooksSortBy === 'year'}
                                  >
                                    Year (soonest first)
                                  </button>
                                </div>
                              </div>

                              <div>
                                <div className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-1">Group</div>
                                <div className="space-y-1" role="listbox" aria-label="Group monitored books">
                                  <button
                                    type="button"
                                    className={`w-full px-2.5 py-1.5 rounded-lg text-left text-sm hover-surface ${monitoredBooksGroupBy === 'none' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setMonitoredBooksGroupBy('none')}
                                    role="option"
                                    aria-selected={monitoredBooksGroupBy === 'none'}
                                  >
                                    No grouping
                                  </button>
                                  <button
                                    type="button"
                                    className={`w-full px-2.5 py-1.5 rounded-lg text-left text-sm hover-surface ${monitoredBooksGroupBy === 'author' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setMonitoredBooksGroupBy('author')}
                                    role="option"
                                    aria-selected={monitoredBooksGroupBy === 'author'}
                                  >
                                    Group by author
                                  </button>
                                  <button
                                    type="button"
                                    className={`w-full px-2.5 py-1.5 rounded-lg text-left text-sm hover-surface ${monitoredBooksGroupBy === 'year' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setMonitoredBooksGroupBy('year')}
                                    role="option"
                                    aria-selected={monitoredBooksGroupBy === 'year'}
                                  >
                                    Group by year
                                  </button>
                                </div>
                              </div>

                              <div>
                                <div className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mb-2">Size</div>
                                <input
                                  type="range"
                                  min={MONITORED_COMPACT_MIN_WIDTH_MIN}
                                  max={MONITORED_COMPACT_MIN_WIDTH_MAX}
                                  step={5}
                                  value={monitoredCompactMinWidth}
                                  onChange={(e) => setMonitoredCompactMinWidth(Number(e.target.value))}
                                  className="w-full accent-emerald-600"
                                  aria-label="Books compact size"
                                  title="Books compact size"
                                  disabled={monitoredBooksViewMode !== 'compact'}
                                />
                                <div className="mt-1 text-[11px] text-gray-500 dark:text-gray-400 tabular-nums text-right">
                                  {monitoredCompactMinWidth}px
                                </div>
                                {monitoredBooksViewMode !== 'compact' ? (
                                  <div className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">Switch to compact view to adjust size.</div>
                                ) : null}
                              </div>
                            </div>
                          )}
                        </Dropdown>
                      </>
                    )}
                  </div>
                </div>

                {landingTab === 'authors' ? (
                  monitoredViewMode === 'table' ? (
                    <div className="flex flex-col gap-2">
                      {monitoredAuthorsForCards.map((author) => {
                        const booksCountLabel = typeof author.stats?.books_count === 'number' ? `${author.stats.books_count} books` : 'Unknown';
                        const subtitle = author.provider ? `${booksCountLabel} • ${author.provider}` : booksCountLabel;
                        const authorEntityId = monitoredEntityIdByName.get((author.name || '').toLowerCase());
                        const isSelected = typeof authorEntityId === 'number'
                          ? Boolean(selectedMonitoredAuthorKeys[String(authorEntityId)])
                          : false;
                        return (
                          <MonitoredAuthorTableRow
                            key={`${author.provider}:${author.provider_id}`}
                            name={author.name || 'Unknown author'}
                            subtitle={subtitle}
                            thumbnail={<AuthorRowThumbnail photo_url={author.photo_url || undefined} name={author.name || 'Unknown author'} />}
                            onOpen={() => navigateToAuthorPage({ ...author, monitoredEntityId: authorEntityId ?? null })}
                            onToggleSelect={typeof authorEntityId === 'number' ? () => toggleMonitoredAuthorSelection(authorEntityId) : undefined}
                            isSelected={isSelected}
                            hasActiveSelection={hasActiveMonitoredAuthorSelection}
                          />
                        );
                      })}
                    </div>
                  ) : (
                    <div
                      className={`grid gap-4 ${!isDesktop ? GRID_CLASSES.mobile : 'items-stretch'}`}
                      style={monitoredCompactGridStyle}
                    >
                      {monitoredAuthorsForCards.map((author) => {
                        const booksCountLabel = typeof author.stats?.books_count === 'number' ? `${author.stats.books_count} books` : 'Unknown';
                        const providerLabel = author.provider ? author.provider : null;
                        const subtitle = providerLabel ? `${booksCountLabel} • ${providerLabel}` : booksCountLabel;
                        const authorEntityId = monitoredEntityIdByName.get((author.name || '').toLowerCase());
                        const isSelected = typeof authorEntityId === 'number'
                          ? Boolean(selectedMonitoredAuthorKeys[String(authorEntityId)])
                          : false;
                        return (
                          <MonitoredAuthorCompactTile
                            key={`${author.provider}:${author.provider_id}`}
                            name={author.name || 'Unknown author'}
                            thumbnail={
                              <div className="w-full aspect-[2/3] bg-black/10 dark:bg-white/10">
                                {author.photo_url ? (
                                  <img
                                    src={author.photo_url}
                                    alt={author.name || 'Author photo'}
                                    className="w-full h-full object-cover object-center"
                                    loading="lazy"
                                  />
                                ) : (
                                  <div className="w-full h-full flex items-center justify-center text-xs opacity-60">
                                    No Photo
                                  </div>
                                )}
                              </div>
                            }
                            subtitle={subtitle}
                            onOpenDetails={() => navigateToAuthorPage({ ...author, monitoredEntityId: authorEntityId ?? null })}
                            onToggleSelect={typeof authorEntityId === 'number' ? () => toggleMonitoredAuthorSelection(authorEntityId) : undefined}
                            isSelected={isSelected}
                            hasActiveSelection={hasActiveMonitoredAuthorSelection}
                          />
                        );
                      })}
                    </div>
                  )
                ) : monitoredBooksLoading ? (
                  <div className="text-sm text-gray-500 dark:text-gray-400">Loading monitored books…</div>
                ) : activeBooksCount === 0 ? (
                  <div className="text-sm text-gray-500 dark:text-gray-400">{isUpcomingTab ? 'No upcoming monitored books yet.' : 'No books with active eBook/audiobook monitoring yet.'}</div>
                ) : monitoredBooksViewMode === 'table' ? (
                  <div className="flex flex-col gap-4">
                    {activeBookGroups.map((group) => (
                      <div key={group.key} className="flex flex-col gap-2">
                        {monitoredBooksGroupBy !== 'none' ? (
                          <div className="flex items-center gap-2 px-1 pt-1">
                            <h3 className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-200">{group.title}</h3>
                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-black/5 dark:bg-white/10 text-gray-600 dark:text-gray-300">
                              {group.rows.length}
                            </span>
                          </div>
                        ) : null}
                        {group.rows.map((book) => {
                          const isSelected = Boolean(selectedMonitoredBookKeys[getMonitoredBookSelectionKey(book)]);
                          const tracksEbook = book.monitor_ebook === true || book.monitor_ebook === 1;
                          const tracksAudiobook = book.monitor_audiobook === true || book.monitor_audiobook === 1;
                          const authorName = book.author_name || 'Unknown author';
                          const subtitleRow = (
                            <div className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-600 dark:text-gray-300 truncate">
                              {authorName}
                            </div>
                          );
                          const titleRow = (
                            <h3 className="font-semibold text-xs min-[400px]:text-sm sm:text-base leading-tight truncate" title={book.title || 'Unknown title'}>
                              {book.title || 'Unknown title'}
                            </h3>
                          );
                          const seriesLabel = book.series_name
                            ? `${book.series_name}${book.series_position != null ? ` #${book.series_position}` : ''}${book.series_count != null ? `/${book.series_count}` : ''}`
                            : null;
                          const ratingLabel = typeof book.rating === 'number' ? `★ ${book.rating.toFixed(1)}` : null;
                          const popularityLabel = typeof book.readers_count === 'number'
                            ? `${book.readers_count.toLocaleString()} readers`
                            : typeof book.ratings_count === 'number'
                              ? `${book.ratings_count.toLocaleString()} ratings`
                              : null;
                          const statsLabel = [ratingLabel, popularityLabel].filter(Boolean).join(' • ');
                          const infoLabel = [
                            seriesLabel || (book.publish_year ? String(book.publish_year) : null),
                            statsLabel || null,
                          ].filter(Boolean).join(' • ');
                          const metaRow = (
                            <div className="text-[10px] min-[400px]:text-xs text-gray-500 dark:text-gray-400 truncate">
                              {infoLabel || 'No series or stats'}
                            </div>
                          );
                          const availabilitySlot = (
                            <div className="flex items-center justify-center gap-1">
                              {tracksEbook ? (
                                <span
                                  className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase bg-emerald-500/20 text-emerald-700 dark:text-emerald-300"
                                  title="Monitored format target"
                                >
                                  eBook wanted
                                </span>
                              ) : null}
                              {tracksAudiobook ? (
                                <span
                                  className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase bg-violet-500/20 text-violet-700 dark:text-violet-300"
                                  title="Monitored format target"
                                >
                                  Audiobook wanted
                                </span>
                              ) : null}
                            </div>
                          );

                          return (
                            <MonitoredBookTableRow
                              key={`${book.author_entity_id}:${book.provider || 'unknown'}:${book.provider_book_id || book.id}`}
                              leadingControl={(
                                <button
                                  type="button"
                                  onClick={() => toggleMonitoredBookSelection(book)}
                                  className={`${isSelected ? 'text-emerald-500 dark:text-emerald-400' : 'text-gray-500 dark:text-gray-400'} transition-colors`}
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
                              )}
                              thumbnail={<BookRowThumbnail coverUrl={book.cover_url} title={book.title || 'Unknown title'} />}
                              onOpen={() => openMonitoredBookDetails(book)}
                              titleRow={titleRow}
                              subtitleRow={subtitleRow}
                              metaRow={metaRow}
                              availabilitySlot={availabilitySlot}
                              trailingSlot={renderMonitoredBookActions(book)}
                            />
                          );
                        })}
                      </div>
                    ))}
                  </div>
                ) : (
                  <div className="flex flex-col gap-5">
                    {activeBookGroups.map((group) => (
                      <div key={group.key} className="flex flex-col gap-3">
                        {monitoredBooksGroupBy !== 'none' ? (
                          <div className="flex items-center gap-2 px-1 pt-1">
                            <h3 className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-200">{group.title}</h3>
                            <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-black/5 dark:bg-white/10 text-gray-600 dark:text-gray-300">
                              {group.rows.length}
                            </span>
                          </div>
                        ) : null}
                        <div
                          className={`grid gap-4 ${!isDesktop ? GRID_CLASSES.mobile : 'items-stretch'}`}
                          style={monitoredBooksGridStyle}
                        >
                          {group.rows.map((book) => {
                            const isSelected = Boolean(selectedMonitoredBookKeys[getMonitoredBookSelectionKey(book)]);
                            const tracksEbook = book.monitor_ebook === true || book.monitor_ebook === 1;
                            const tracksAudiobook = book.monitor_audiobook === true || book.monitor_audiobook === 1;
                            const authorName = book.author_name || 'Unknown author';
                            const badge = tracksEbook && tracksAudiobook ? 'eBook + Audio' : tracksEbook ? 'eBook' : 'Audio';
                            const seriesLabel = book.series_name
                              ? `${book.series_name}${book.series_position != null ? ` #${book.series_position}` : ''}${book.series_count != null ? `/${book.series_count}` : ''}`
                              : undefined;
                            const metaLine = book.publish_year ? String(book.publish_year) : undefined;

                            return (
                              <MediaCompactTileBase
                                key={`${book.author_entity_id}:${book.provider || 'unknown'}:${book.provider_book_id || book.id}:compact`}
                                title={book.title || 'Unknown title'}
                                media={
                                  <div className="w-full aspect-[2/3] bg-black/10 dark:bg-white/10">
                                    {book.cover_url ? (
                                      <img
                                        src={book.cover_url}
                                        alt={book.title || 'Book cover'}
                                        className="w-full h-full object-cover object-center"
                                        loading="lazy"
                                      />
                                    ) : (
                                      <div className="w-full h-full flex items-center justify-center text-xs opacity-60">
                                        No Cover
                                      </div>
                                    )}
                                  </div>
                                }
                                onOpen={() => openMonitoredBookDetails(book)}
                                topLeftOverlay={(
                                  <button
                                    type="button"
                                    onClick={(event) => {
                                      event.stopPropagation();
                                      toggleMonitoredBookSelection(book);
                                    }}
                                    className={`${isSelected ? 'text-emerald-500 dark:text-emerald-400' : 'text-white/80'} hover-action rounded-full p-0.5 bg-black/30 backdrop-blur-[1px]`}
                                    role="checkbox"
                                    aria-checked={isSelected}
                                    aria-label={`Select ${book.title || 'book'}`}
                                  >
                                    {isSelected ? (
                                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><rect x="4" y="4" width="16" height="16" rx="3" /><path strokeLinecap="round" strokeLinejoin="round" d="m8 12 2.5 2.5L16 9" /></svg>
                                    ) : (
                                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><rect x="4" y="4" width="16" height="16" rx="3" /></svg>
                                    )}
                                  </button>
                                )}
                                topRightOverlay={badge ? <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-emerald-600/90 text-white text-[9px] font-semibold uppercase shadow">{badge}</span> : undefined}
                                subtitle={authorName}
                                metaLine={seriesLabel || metaLine}
                                overflowMenu={renderMonitoredBookActions(book, true)}
                              />
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                {(landingTab === 'books' || landingTab === 'upcoming') && monitoredBooksLoadError ? (
                  <div className="mt-2 text-xs text-amber-600 dark:text-amber-400">{monitoredBooksLoadError}</div>
                ) : null}
              </section>
            )
          ) : (
            <section className="rounded-2xl border border-black/10 dark:border-white/10 bg-white/80 dark:bg-white/5 p-4">
              <div className="mb-3 pb-2 border-b border-black/10 dark:border-white/10">
                <div className="flex items-center justify-between gap-3">
                  <div className="flex items-center gap-2 min-w-0">
                    <button
                      type="button"
                      onClick={() => {
                        if (onBack) {
                          onBack();
                          return;
                        }
                        navigate('/');
                      }}
                      className="rounded-full p-1.5 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
                      aria-label="Back to home"
                      title="Back"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.5 7.5 12 15 4.5" />
                      </svg>
                    </button>
                    <div className="inline-flex items-center rounded-full border border-[var(--border-muted)] bg-transparent">
                      <button
                        type="button"
                        onClick={() => openMonitoredTab('authors')}
                        className={`px-3.5 py-2 rounded-full text-xs font-medium transition-colors ${landingTab === 'authors' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                        aria-pressed={landingTab === 'authors'}
                      >
                        Monitored Authors
                        <span className="ml-1 opacity-85">{displayAuthorsCount}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => openMonitoredTab('books')}
                        className={`px-3.5 py-2 rounded-full text-xs font-medium transition-colors ${landingTab === 'books' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                        aria-pressed={landingTab === 'books'}
                      >
                        Monitored Books
                        <span className="ml-1 opacity-85">{displayBooksCount}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => openMonitoredTab('upcoming')}
                        className={`px-3.5 py-2 rounded-full text-xs font-medium transition-colors ${landingTab === 'upcoming' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                        aria-pressed={landingTab === 'upcoming'}
                      >
                        Upcoming
                        <span className="ml-1 opacity-85">{displayUpcomingCount}</span>
                      </button>
                      {hasStartedSearch ? (
                        <button
                          type="button"
                          onClick={() => openMonitoredTab('search')}
                          className={`px-3.5 py-2 rounded-full text-xs font-medium transition-colors ${landingTab === 'search' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                          aria-pressed={landingTab === 'search'}
                        >
                          Search
                          <span className="ml-1 opacity-85">{displaySearchCount}</span>
                        </button>
                      ) : null}
                    </div>
                  </div>
                  <div className="shrink-0">
                    {searchScope === 'authors' ? (
                      <ViewModeToggle
                        value={authorViewMode}
                        onChange={(next) => setAuthorViewMode(next as 'compact' | 'list')}
                        options={authorSearchViewOptions}
                      />
                    ) : (
                      <ViewModeToggle
                        value={bookSearchViewMode}
                        onChange={(next) => setBookSearchViewMode(next as 'compact' | 'list')}
                        options={bookSearchViewOptions}
                      />
                    )}
                  </div>
                </div>
              </div>
              <div className="flex items-center mb-3 pb-2 border-b border-black/10 dark:border-white/10 relative z-10 gap-3">
                <div className="flex items-center gap-2 min-w-0">
                  <h2 className="text-base sm:text-lg font-semibold tracking-tight text-gray-900 dark:text-gray-100 truncate">
                    {searchScope === 'books' ? 'New Books' : 'New Authors'}
                  </h2>
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-black/5 dark:bg-white/10 text-gray-600 dark:text-gray-300">
                    {searchScope === 'books' ? bookSearchResults.length : authorResults.length}
                  </span>
                  {searchScope === 'books' ? (
                    <Dropdown
                      align="left"
                      widthClassName="w-60 sm:w-72"
                      renderTrigger={({ isOpen, toggle }) => (
                        <button
                          type="button"
                          onClick={toggle}
                          className={`px-3 py-1.5 rounded-full text-xs font-medium bg-white/70 hover:bg-white text-gray-900 dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100 ${isOpen ? 'ring-1 ring-emerald-500/50' : ''}`}
                          title="Sort"
                          aria-label="Sort"
                          aria-haspopup="listbox"
                          aria-expanded={isOpen}
                        >
                          {monitoredSearchSortOptions.find((option) => option.value === bookSearchSortValue)?.label || monitoredSearchSortOptions[0]?.label || 'Most relevant'}
                        </button>
                      )}
                    >
                      {({ close }) => (
                        <div role="listbox" aria-label="Sort search results">
                          {monitoredSearchSortOptions.map((option) => {
                            const isSelected = option.value === bookSearchSortValue;
                            return (
                              <button
                                type="button"
                                key={option.value}
                                className={`w-full px-3 py-2 text-left text-base flex items-center justify-between gap-2 hover-surface ${
                                  isSelected ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''
                                }`}
                                onClick={() => {
                                  setBookSearchSortValue(option.value);
                                  close();
                                }}
                                role="option"
                                aria-selected={isSelected}
                              >
                                <span>{option.label}</span>
                                {isSelected ? (
                                  <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-4 h-4">
                                    <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                                  </svg>
                                ) : null}
                              </button>
                            );
                          })}
                        </div>
                      )}
                    </Dropdown>
                  ) : (
                    <span className="px-3 py-1.5 rounded-full text-xs font-medium bg-white/70 text-gray-900 dark:bg-white/10 dark:text-gray-100">
                      Most relevant
                    </span>
                  )}
                </div>

              </div>

              {!authorQuery.trim() && !isSearching ? (
                <div className="rounded-2xl border border-dashed border-black/10 dark:border-white/10 bg-white/60 dark:bg-white/5 px-4 py-8 text-center">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Start a search from the top bar</div>
                  <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">Use the header search input to find authors or books to monitor.</div>
                  <div className="mt-4">
                    <button
                      type="button"
                      onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
                      className="px-3 py-1.5 rounded-full text-xs font-medium bg-white/70 hover:bg-white text-gray-900 dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                    >
                      Go to search bar
                    </button>
                  </div>
                </div>
              ) : searchScope === 'books' ? (
                bookSearchResults.length === 0 ? (
                  <div className="text-sm text-gray-500 dark:text-gray-400">Search for a book to monitor.</div>
                ) : (
                  <ResultsSection
                    books={bookSearchResults}
                    visible
                    onDetails={handleBookSearchResultDetails}
                    onDownload={noopDownload}
                    onGetReleases={handleBookSearchResultGet}
                    getButtonState={getMonitorResultButtonState}
                    getUniversalButtonState={getMonitorResultButtonState}
                    sortValue={bookSearchSortValue}
                    onSortChange={setBookSearchSortValue}
                    hideSortControl
                    hideViewToggle
                    viewMode={bookSearchViewMode}
                    onViewModeChange={(next) => setBookSearchViewMode(next === 'list' ? 'list' : 'compact')}
                    customAction={{
                      label: 'Monitor',
                      onClick: (book) => openBookMonitorModal(book),
                      isDisabled: (book) => isBookSearchResultMonitored(book),
                      getLabel: (book) => (isBookSearchResultMonitored(book) ? 'Monitored' : 'Monitor'),
                    }}
                  />
                )
              ) : authorResults.length === 0 ? (
                <div className="text-sm text-gray-500 dark:text-gray-400">Search for an author to add.</div>
              ) : (
                authorViewMode === 'list' ? (
                  <div className="flex flex-col gap-2">
                    {(authorCards.length > 0
                      ? authorCards
                      : authorResults.map((name) => ({
                          provider: 'hardcover',
                          provider_id: name,
                          name,
                          stats: { books_count: null },
                        } as MetadataAuthor))
                    ).map((author) => {
                      const name = author.name;
                      const isMonitored = monitoredNames.has(name.toLowerCase());
                      const booksCount = author.stats?.books_count;
                      const subtitle = `${typeof booksCount === 'number' ? `${booksCount} books` : 'Unknown'}${author.provider ? ` • ${author.provider}` : ''}`;
                      return (
                        <MonitoredAuthorTableRow
                          key={`${author.provider}:${author.provider_id}`}
                          name={name || 'Unknown author'}
                          subtitle={subtitle}
                          thumbnail={<AuthorRowThumbnail photo_url={author.photo_url || undefined} name={name || 'Unknown author'} />}
                          onOpen={() => navigateToAuthorPage(author)}
                          trailingAction={(
                            <button
                              type="button"
                              onClick={() => openMonitorModal({
                                name,
                                provider: author.provider,
                                provider_id: author.provider_id,
                                photo_url: author.photo_url || undefined,
                                books_count: typeof author.stats?.books_count === 'number' ? author.stats?.books_count : undefined,
                              })}
                              disabled={isMonitored}
                              className="px-3 py-1 rounded-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white text-xs font-medium"
                            >
                              {isMonitored ? 'Monitored' : 'Monitor'}
                            </button>
                          )}
                        />
                      );
                    })}
                  </div>
                ) : (
                  <div className={`grid gap-4 ${!isDesktop ? GRID_CLASSES.mobile : GRID_CLASSES.compact}`}>
                    {(authorCards.length > 0
                      ? authorCards
                      : authorResults.map((name) => ({
                          provider: 'hardcover',
                          provider_id: name,
                          name,
                          stats: { books_count: null },
                        } as MetadataAuthor))
                    ).map((author, index) => {
                      const name = author.name;
                      const isMonitored = monitoredNames.has(name.toLowerCase());
                      return (
                        <AuthorCompactView
                          key={`${author.provider}:${author.provider_id}`}
                          author={author}
                          actionLabel={isMonitored ? 'Monitored' : 'Monitor'}
                          actionDisabled={isMonitored}
                          onAction={() => openMonitorModal({
                            name,
                            provider: author.provider,
                            provider_id: author.provider_id,
                            photo_url: author.photo_url || undefined,
                            books_count: typeof author.stats?.books_count === 'number' ? author.stats?.books_count : undefined,
                          })}
                          onOpen={() => navigateToAuthorPage(author)}
                          animationDelay={index * 50}
                        />
                      );
                    })}
                  </div>
                )
              )}
            </section>
          )}
        </div>
      </main>

      {bulkDeleteAuthorsConfirmOpen && selectedMonitoredAuthorCount > 0 ? (
        <div
          className="modal-overlay active sm:px-6 sm:py-6"
          style={{ zIndex: 1300 }}
          onClick={(e) => {
            if (e.target === e.currentTarget && !bulkDeleteAuthorsRunning) {
              setBulkDeleteAuthorsConfirmOpen(false);
            }
          }}
        >
          <div
            className="details-container w-full max-w-md h-auto settings-modal-enter"
            role="dialog"
            aria-modal="true"
            aria-label="Confirm delete monitored authors"
          >
            <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] text-[var(--text)] shadow-2xl overflow-hidden">
              <header className="flex items-start justify-between gap-3 border-b border-[var(--border-muted)] px-5 py-4">
                <div className="min-w-0">
                  <div className="text-base font-semibold">Delete monitored {selectedMonitoredAuthorCount === 1 ? 'author' : 'authors'}</div>
                </div>
                <button
                  type="button"
                  onClick={() => setBulkDeleteAuthorsConfirmOpen(false)}
                  disabled={bulkDeleteAuthorsRunning}
                  className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100 disabled:opacity-50"
                  aria-label="Close"
                >
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </header>

              <div className="px-5 py-4 space-y-3">
                <p className="text-sm text-gray-800 dark:text-gray-100">
                  {selectedMonitoredAuthorCount === 1
                    ? `Are you sure you want to delete ${selectedSingleMonitoredAuthorName}?`
                    : 'Are you sure you want to delete these authors?'}
                </p>
                <p className="text-sm text-gray-600 dark:text-gray-300">
                  This action is not reversible. This will not delete files on the disk.
                </p>
              </div>

              <footer className="flex items-center justify-end gap-2 border-t border-[var(--border-muted)] px-5 py-4">
                <button
                  type="button"
                  onClick={() => setBulkDeleteAuthorsConfirmOpen(false)}
                  disabled={bulkDeleteAuthorsRunning}
                  className="px-3 py-1.5 rounded-full text-sm font-medium hover-action disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void runBulkDeleteSelectedAuthors()}
                  disabled={bulkDeleteAuthorsRunning}
                  className="px-3 py-1.5 rounded-full text-sm font-medium text-white bg-red-600 hover:bg-red-700 disabled:opacity-50"
                >
                  {bulkDeleteAuthorsRunning ? 'Deleting…' : 'Delete'}
                </button>
              </footer>
            </div>
          </div>
        </div>
      ) : null}

      {monitorModalState.open && monitorModalState.author ? (
        <div
          className="modal-overlay active sm:px-6 sm:py-6"
          style={{ zIndex: 1200 }}
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              closeMonitorModal();
            }
          }}
        >
          <div
            className="details-container w-full max-w-lg h-auto settings-modal-enter"
            role="dialog"
            aria-modal="true"
            aria-label="Monitor author folders"
          >
            <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-2xl overflow-hidden">
              <header className="flex items-start justify-between gap-3 border-b border-[var(--border-muted)] px-5 py-4">
                <div className="min-w-0">
                  <div className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Monitor author</div>
                  <div className="mt-1 text-base font-semibold truncate">{monitorModalState.author.name}</div>
                </div>
                <button
                  type="button"
                  onClick={closeMonitorModal}
                  className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
                  aria-label="Close"
                >
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </header>

              <div className="px-5 py-4 space-y-4">
                <div className="space-y-2">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Ebook folder</div>
                  <div className="space-y-2">
                    {(() => {
                      const authorName = monitorModalState.author?.name || '';
                      const rootValue = stripTrailingAuthorName(monitorModalState.ebookAuthorDir, authorName);
                      const suffix = authorName ? `/${authorName}` : '';
                      return (
                        <>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => {
                                setFolderBrowserState({
                                  open: true,
                                  kind: 'ebook',
                                  initialPath: rootValue || null,
                                });
                              }}
                              className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                            >
                              Browse
                            </button>
                            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">Pick the final author folder.</div>
                          </div>
                          {editingMonitorPathKind === 'ebook' ? (
                            <div className="relative">
                              <input
                                value={rootValue}
                                onChange={(e) => {
                                  const value = e.target.value;
                                  const nextFull = authorName ? joinPath(value, authorName) : value;
                                  setMonitorModalState((prev) => ({ ...prev, ebookAuthorDir: nextFull }));
                                  void refreshPathSuggestions('ebook', value);
                                }}
                                onFocus={() => void refreshPathSuggestions('ebook', rootValue)}
                                onBlur={() => {
                                  window.setTimeout(() => {
                                    setPathSuggestState((prev) => ({ ...prev, open: false }));
                                  }, 150);
                                }}
                                placeholder="/books/ebooks"
                                className="w-full px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm"
                              />
                              {pathSuggestState.open && pathSuggestState.kind === 'ebook' ? (
                                <div className="absolute z-10 mt-1 w-full rounded-xl border border-[var(--border-muted)] bg-[var(--bg)] shadow-lg overflow-hidden">
                                  {pathSuggestState.loading ? (
                                    <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">Loading…</div>
                                  ) : pathSuggestState.error ? (
                                    <div className="px-3 py-2 text-xs text-red-500">{pathSuggestState.error}</div>
                                  ) : pathSuggestState.entries.length === 0 ? (
                                    <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">No folders</div>
                                  ) : (
                                    <div className="max-h-56 overflow-auto">
                                      {pathSuggestState.entries.map((entry) => (
                                        <button
                                          key={entry.path}
                                          type="button"
                                          onMouseDown={(e) => e.preventDefault()}
                                          onClick={() => {
                                            const nextFull = authorName ? joinPath(entry.path, authorName) : entry.path;
                                            setMonitorModalState((prev) => ({ ...prev, ebookAuthorDir: nextFull }));
                                            setPathSuggestState((prev) => ({ ...prev, open: false }));
                                          }}
                                          className="w-full text-left px-3 py-2 text-sm hover:bg-black/5 dark:hover:bg-white/10"
                                        >
                                          {entry.path}
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              ) : null}
                            </div>
                          ) : (
                            <div className="flex items-center gap-2">
                              <div className="flex-1 px-3 py-2 rounded-xl bg-white/60 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm break-all">
                                <span className="text-gray-900 dark:text-gray-100">{rootValue || '—'}</span>
                                {suffix ? <span className="text-gray-400 dark:text-gray-500">{suffix}</span> : null}
                              </div>
                              <button
                                type="button"
                                onClick={() => setEditingMonitorPathKind('ebook')}
                                className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                              >
                                Edit
                              </button>
                            </div>
                          )}
                        </>
                      );
                    })()}
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Audiobook folder</div>
                  <div className="space-y-2">
                    {(() => {
                      const authorName = monitorModalState.author?.name || '';
                      const rootValue = stripTrailingAuthorName(monitorModalState.audiobookAuthorDir, authorName);
                      const suffix = authorName ? `/${authorName}` : '';
                      return (
                        <>
                          <div className="flex items-center gap-2">
                            <button
                              type="button"
                              onClick={() => {
                                setFolderBrowserState({
                                  open: true,
                                  kind: 'audiobook',
                                  initialPath: rootValue || null,
                                });
                              }}
                              className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                            >
                              Browse
                            </button>
                            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">Pick the final author folder.</div>
                          </div>
                          {editingMonitorPathKind === 'audiobook' ? (
                            <div className="relative">
                              <input
                                value={rootValue}
                                onChange={(e) => {
                                  const value = e.target.value;
                                  const nextFull = authorName ? joinPath(value, authorName) : value;
                                  setMonitorModalState((prev) => ({ ...prev, audiobookAuthorDir: nextFull }));
                                  void refreshPathSuggestions('audiobook', value);
                                }}
                                onFocus={() => void refreshPathSuggestions('audiobook', rootValue)}
                                onBlur={() => {
                                  window.setTimeout(() => {
                                    setPathSuggestState((prev) => ({ ...prev, open: false }));
                                  }, 150);
                                }}
                                placeholder="/books/audiobooks"
                                className="w-full px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm"
                              />
                              {pathSuggestState.open && pathSuggestState.kind === 'audiobook' ? (
                                <div className="absolute z-10 mt-1 w-full rounded-xl border border-[var(--border-muted)] bg-[var(--bg)] shadow-lg overflow-hidden">
                                  {pathSuggestState.loading ? (
                                    <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">Loading…</div>
                                  ) : pathSuggestState.error ? (
                                    <div className="px-3 py-2 text-xs text-red-500">{pathSuggestState.error}</div>
                                  ) : pathSuggestState.entries.length === 0 ? (
                                    <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">No folders</div>
                                  ) : (
                                    <div className="max-h-56 overflow-auto">
                                      {pathSuggestState.entries.map((entry) => (
                                        <button
                                          key={entry.path}
                                          type="button"
                                          onMouseDown={(e) => e.preventDefault()}
                                          onClick={() => {
                                            const nextFull = authorName ? joinPath(entry.path, authorName) : entry.path;
                                            setMonitorModalState((prev) => ({ ...prev, audiobookAuthorDir: nextFull }));
                                            setPathSuggestState((prev) => ({ ...prev, open: false }));
                                          }}
                                          className="w-full text-left px-3 py-2 text-sm hover:bg-black/5 dark:hover:bg-white/10"
                                        >
                                          {entry.path}
                                        </button>
                                      ))}
                                    </div>
                                  )}
                                </div>
                              ) : null}
                            </div>
                          ) : (
                            <div className="flex items-center gap-2">
                              <div className="flex-1 px-3 py-2 rounded-xl bg-white/60 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm break-all">
                                <span className="text-gray-900 dark:text-gray-100">{rootValue || '—'}</span>
                                {suffix ? <span className="text-gray-400 dark:text-gray-500">{suffix}</span> : null}
                              </div>
                              <button
                                type="button"
                                onClick={() => setEditingMonitorPathKind('audiobook')}
                                className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                              >
                                Edit
                              </button>
                            </div>
                          )}
                        </>
                      );
                    })()}
                  </div>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <label className="space-y-1">
                    <div className="text-xs font-medium text-gray-700 dark:text-gray-300">eBook monitoring</div>
                    <select
                      value={monitorModalState.monitorEbookMode}
                      onChange={(e) => {
                        const value = e.target.value as 'all' | 'missing' | 'upcoming';
                        setMonitorModalState((prev) => ({ ...prev, monitorEbookMode: value }));
                      }}
                      className="w-full px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm"
                    >
                      <option value="all">Monitor all books</option>
                      <option value="missing">Monitor missing only</option>
                      <option value="upcoming">Monitor upcoming only</option>
                    </select>
                  </label>

                  <label className="space-y-1">
                    <div className="text-xs font-medium text-gray-700 dark:text-gray-300">Audiobook monitoring</div>
                    <select
                      value={monitorModalState.monitorAudiobookMode}
                      onChange={(e) => {
                        const value = e.target.value as 'all' | 'missing' | 'upcoming';
                        setMonitorModalState((prev) => ({ ...prev, monitorAudiobookMode: value }));
                      }}
                      className="w-full px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm"
                    >
                      <option value="all">Monitor all books</option>
                      <option value="missing">Monitor missing only</option>
                      <option value="upcoming">Monitor upcoming only</option>
                    </select>
                  </label>
                </div>
              </div>

              <footer className="flex items-center justify-end gap-2 border-t border-[var(--border-muted)] px-5 py-4 bg-[var(--bg)] sm:bg-[var(--bg-soft)]">
                <button
                  type="button"
                  onClick={closeMonitorModal}
                  className="px-4 py-2 rounded-full bg-white/70 hover:bg-white text-gray-900 font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void confirmMonitorAuthor()}
                  className="px-4 py-2 rounded-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white font-medium"
                >
                  Monitor
                </button>
              </footer>
            </div>
          </div>
        </div>
      ) : null}

      <BookDetailsModal
        book={activeBookDetails}
        files={activeBookFiles}
        monitoredEntityId={activeBookEntityId}
        onClose={() => {
          activeBookRequestSeq.current += 1;
          setActiveBookDetails(null);
          setActiveBookFiles([]);
          setActiveBookEntityId(null);
          setActiveBookSourceRow(null);
        }}
        onOpenSearch={(_contentType: ContentType) => {
          if (!activeBookSourceRow) {
            return;
          }
          openMonitoredBookInAuthorPage(activeBookSourceRow);
        }}
      />

      <FolderBrowserModal
        open={folderBrowserState.open}
        title={folderBrowserState.kind === 'audiobook' ? 'Select audiobook folder' : 'Select ebook folder'}
        initialPath={folderBrowserState.initialPath}
        onClose={() => setFolderBrowserState({ open: false, kind: null, initialPath: null })}
        onSelect={(path) => {
          const authorName = monitorModalState.author?.name
            || extractPrimaryAuthorName(bookMonitorModalState.book?.author || '');
          const suggested = authorName ? joinPath(path, authorName) : path;
          if (bookMonitorModalState.open && bookMonitorModalState.book) {
            if (folderBrowserState.kind === 'audiobook') {
              setBookMonitorModalState((prev) => ({
                ...prev,
                audiobookAuthorDir: suggested,
              }));
            } else {
              setBookMonitorModalState((prev) => ({
                ...prev,
                ebookAuthorDir: suggested,
              }));
            }
            return;
          }
          if (folderBrowserState.kind === 'audiobook') {
            setMonitorModalState((prev) => ({
              ...prev,
              audiobookAuthorDir: suggested,
            }));
          } else {
            setMonitorModalState((prev) => ({
              ...prev,
              ebookAuthorDir: suggested,
            }));
          }
        }}
      />
    </div>
  );
};
