import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useSocket } from '../contexts/SocketContext';
import { useLocation, useNavigate } from 'react-router-dom';
import { Header } from '../components/Header';
import { ActivityStatusCounts } from '../utils/activityBadge';
import {
  getSelfUserEditContext,
  updateSelfUser,
  searchMetadata,
} from '../services/api';
import { ActivityItem } from '../components/activity/activityTypes';
import {
  createMonitoredEntity,
  listMonitoredEntities,
  listMonitoredBooks,
  updateMonitoredBooksMonitorFlags,
  fsListDirectories,
  MetadataAuthor,
  MonitoredEntity,
  MonitoredAuthorBookSearchRow,
  searchMonitoredAuthorBooks,
  searchMetadataAuthors,
  deleteMonitoredAuthorsByIds,
  syncMonitoredEntity,
  syncAllMonitoredEntities,
} from '../services/monitoredApi';
import { FolderBrowserModal } from '../components/FolderBrowserModal';
import { BookMonitorModal } from '../components/BookMonitorModal';
import { EditAuthorModal } from '../components/EditAuthorModal';
import { Dropdown } from '../components/Dropdown';
import { BookDetailsModal } from '../components/BookDetailsModal';
import ReleaseDateSearchModal from '../components/ReleaseDateSearchModal';
import { AuthorModal, AuthorModalAuthor } from '../components/AuthorModal';
import { ViewModeToggle, type ViewModeToggleOption } from '../components/ViewModeToggle';
import { MonitoredAuthorsView, type AuthorAvailabilityStats } from '../components/MonitoredAuthorsView';
import { SlideSheet } from '../components/SlideSheet';
import { withBasePath } from '../utils/basePath';
import { MonitoredBooksView, type MonitoredBookListRow, type MonitoredBooksGroup } from '../components/MonitoredBooksView';
import { MonitoredSearchView } from '../components/MonitoredSearchView';
import { Book, ButtonStateInfo, ContentType, OpenReleasesOptions, ReleasePrimaryAction, SortOption, StatusData } from '../types';
import {
  isEnabledMonitoredFlag,
  isMonitoredBookUpcoming,
  monitoredBookHasAnyAvailable,
  monitoredBookHasFormatAvailable,
  monitoredBookTracksAudiobook,
  monitoredBookTracksEbook,
} from '../utils/monitoredBookState';

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
  last_error?: string | null;
  visibility?: 'public' | 'private';
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

const groupMonitoredBooks = (
  rows: MonitoredBookListRow[],
  groupBy: 'none' | 'author' | 'year',
  allLabel: string,
  yearAscending?: boolean,
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
      const aYear = a.title === 'Unknown year' ? Number.POSITIVE_INFINITY : Number(a.title);
      const bYear = b.title === 'Unknown year' ? Number.POSITIVE_INFINITY : Number(b.title);
      return yearAscending ? aYear - bYear : bYear - aYear;
    });
  } else {
    sortedGroups.sort((a, b) => a.title.localeCompare(b.title, undefined, { sensitivity: 'base' }));
  }

  return sortedGroups;
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
  releaseCombinedMode?: boolean;
  showBooksInMultipleSeries?: boolean;
  metadataSortOptions?: SortOption[];
  status?: StatusData;
  renderEmbeddedSearch?: (book: Book, contentType: ContentType) => ReactNode;
  onShowToast?: (message: string, type?: 'info' | 'success' | 'error', persistent?: boolean) => string;
  onRemoveToast?: (id: string) => void;
  setTransientActivityItems?: (updater: (prev: ActivityItem[]) => ActivityItem[]) => void;
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


const MONITORED_COMPACT_MIN_WIDTH_MIN = 120;
const MONITORED_COMPACT_MIN_WIDTH_MAX = 185;
const MONITORED_COMPACT_MIN_WIDTH_DEFAULT = 150;
const MONITORED_COUNTS_CACHE_KEY = 'monitoredCountsSnapshot';
// Stale-while-revalidate cache for the entity list so the page renders instantly on revisit.
const MONITORED_ENTITY_CACHE_KEY = 'monitoredEntities_v2';
const MONITORED_ENTITY_CACHE_MAX_AGE = 10 * 60 * 1000; // 10 minutes
const MONITORED_BOOKS_SEARCH_QUERY_KEY = 'monitoredBooksSearchQuery';
const MONITORED_BOOKS_SEARCH_EXPANDED_KEY = 'monitoredBooksSearchExpanded';
const MONITORED_BOOKS_AVAILABILITY_FILTER_KEY = 'monitoredBooksAvailabilityFilter';
const MONITORED_UPCOMING_TIME_FILTER_KEY = 'monitoredUpcomingTimeFilter';

// Computed once at module load — stable for the lifetime of the session.
const _todayStartMs = (() => { const d = new Date(); d.setHours(0, 0, 0, 0); return d.getTime(); })();
const _currentYear = new Date(_todayStartMs).getFullYear();
const _threeMonthsMs = (() => { const d = new Date(_todayStartMs); d.setMonth(d.getMonth() + 3); return d.getTime(); })();

type UpcomingTimeFilter = 'all' | '3months' | 'this_year' | 'tba';

const getUpcomingTimeCategory = (
  book: MonitoredBookListRow,
  threeMonthsMs: number,
  currentYear: number,
): Exclude<UpcomingTimeFilter, 'all'> => {
  if (typeof book.release_date === 'string' && book.release_date.trim()) {
    const parsed = Date.parse(book.release_date);
    if (Number.isFinite(parsed)) {
      const releaseMs = new Date(new Date(parsed).setHours(0, 0, 0, 0)).getTime();
      if (releaseMs <= threeMonthsMs) return '3months';
      if (new Date(parsed).getFullYear() === currentYear) return 'this_year';
    }
  }
  if (typeof book.publish_year === 'number' && book.publish_year === currentYear) return 'this_year';
  return 'tba';
};


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
  releaseCombinedMode = false,
  showBooksInMultipleSeries,
  metadataSortOptions,
  status,
  renderEmbeddedSearch,
  onShowToast,
  onRemoveToast,
  setTransientActivityItems,
}: MonitoredPageProps) => {
  const [landingTab, setLandingTab] = useState<'authors' | 'books' | 'upcoming' | 'search'>(() => {
    const saved = localStorage.getItem('monitoredLandingTab');
    return saved === 'books' || saved === 'upcoming' || saved === 'search' ? saved : 'authors';
  });
  const [isMobileNavOpen, setIsMobileNavOpen] = useState(false);
  const [isMobileSearchOpen, setIsMobileSearchOpen] = useState(false);
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
  const [authorSearchSortValue, setAuthorSearchSortValue] = useState('relevance');
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
  const [monitoredBooksSortBy, setMonitoredBooksSortBy] = useState<'title' | 'date' | 'recently_added' | 'popularity'>(() => {
    const saved = localStorage.getItem('monitoredBooksSortBy');
    if (saved === 'date' || saved === 'year') return 'date';
    if (saved === 'recently_added') return 'recently_added';
    if (saved === 'popularity') return 'popularity';
    return 'title';
  });
  const [monitoredBooksSortAsc, setMonitoredBooksSortAsc] = useState(() => {
    const saved = localStorage.getItem('monitoredBooksSortAsc');
    return saved === 'false' ? false : true;
  });
  const [monitoredBooksGroupBy, setMonitoredBooksGroupBy] = useState<'none' | 'author' | 'year'>(() => {
    const saved = localStorage.getItem('monitoredBooksGroupBy');
    return saved === 'author' || saved === 'year' ? saved : 'none';
  });
  const [monitoredBooksAvailabilityFilter, setMonitoredBooksAvailabilityFilter] = useState<'missing' | 'fulfilled'>(() => {
    const saved = localStorage.getItem(MONITORED_BOOKS_AVAILABILITY_FILTER_KEY);
    return saved === 'fulfilled' ? 'fulfilled' : 'missing';
  });
  const [upcomingTimeFilter, setUpcomingTimeFilter] = useState<UpcomingTimeFilter>(() => {
    const saved = localStorage.getItem(MONITORED_UPCOMING_TIME_FILTER_KEY);
    return saved === '3months' || saved === 'this_year' || saved === 'tba' ? saved : 'all';
  });
  const [monitoredSortBy, setMonitoredSortBy] = useState<'alphabetical' | 'date_added' | 'books_count'>(() => {
    const saved = localStorage.getItem('monitoredAuthorSortBy');
    return saved === 'date_added' || saved === 'books_count' || saved === 'alphabetical'
      ? saved
      : 'alphabetical';
  });
  const [monitoredSortAsc, setMonitoredSortAsc] = useState(() => {
    const saved = localStorage.getItem('monitoredAuthorSortAsc');
    return saved === 'false' ? false : true;
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
  const [monitoredBooksReloadTick, setMonitoredBooksReloadTick] = useState(0);
  const [monitoredLoaded, setMonitoredLoaded] = useState(false);
  const [monitoredBooksRows, setMonitoredBooksRows] = useState<MonitoredBookListRow[]>([]);
  const [monitoredBooksLoading, setMonitoredBooksLoading] = useState(false);
  const [monitoredBooksEverLoaded, setMonitoredBooksEverLoaded] = useState(false);
  const [monitoredBooksLoadError, setMonitoredBooksLoadError] = useState<string | null>(null);
  const [activeBookEntityId, setActiveBookEntityId] = useState<number | null>(null);
  const [activeBookSourceRow, setActiveBookSourceRow] = useState<MonitoredBookListRow | null>(null);
  const [releaseDateBook, setReleaseDateBook] = useState<{ row: MonitoredBookListRow; entityId: number } | null>(null);
  const navigate = useNavigate();
  const location = useLocation();
  const { socket } = useSocket();
  const syncActivityTimeoutsRef = useRef<Map<number, number>>(new Map());
  const monitoredRef = useRef<MonitoredAuthor[]>([]);
  monitoredRef.current = monitored;
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
  const [searchPanelLeft, setSearchPanelLeft] = useState<number | null>(null);
  const [searchPanelCaretLeft, setSearchPanelCaretLeft] = useState<number>(16);
  const [selectedMonitoredBookKeys, setSelectedMonitoredBookKeys] = useState<Record<string, boolean>>({});
  const [selectedMonitoredAuthorKeys, setSelectedMonitoredAuthorKeys] = useState<Record<string, boolean>>({});
  const [bulkUnmonitorRunning, setBulkUnmonitorRunning] = useState(false);
  const [bulkDeleteAuthorsRunning, setBulkDeleteAuthorsRunning] = useState(false);
  const [bulkDeleteAuthorsConfirmOpen, setBulkDeleteAuthorsConfirmOpen] = useState(false);
  const [bulkSyncAuthorsRunning, setBulkSyncAuthorsRunning] = useState(false);
  const [cachedMonitoredCounts, setCachedMonitoredCounts] = useState<MonitoredCountsSnapshot | null>(() => readMonitoredCountsSnapshot());

  const [editAuthorModalState, setEditAuthorModalState] = useState<{
    open: boolean;
    entityId: number | null;
    authorName: string;
  }>({
    open: false,
    entityId: null,
    authorName: '',
  });

  const [monitorModalState, setMonitorModalState] = useState<{
    open: boolean;
    author: { name: string; provider?: string; provider_id?: string; photo_url?: string; books_count?: number } | null;
    ebookAuthorDir: string;
    audiobookAuthorDir: string;
    monitorEbookMode: 'all' | 'missing' | 'upcoming';
    monitorAudiobookMode: 'all' | 'missing' | 'upcoming';
    visibility: 'public' | 'private';
  }>(() => ({
    open: false,
    author: null,
    ebookAuthorDir: '',
    audiobookAuthorDir: '',
    monitorEbookMode: 'missing',
    monitorAudiobookMode: 'missing',
    visibility: 'public',
  }));

  const [bookMonitorModalState, setBookMonitorModalState] = useState<{
    book: Book | null;
  }>({
    book: null,
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

  const [isDesktop, setIsDesktop] = useState(false);

  // Activity-panel notifications for background author sync events
  useEffect(() => {
    if (!socket || !setTransientActivityItems) return;

    const phaseDetail: Record<string, string> = {
      fetching_books: 'Fetching books…',
      scanning_files: 'Scanning filesystem…',
      fetching_covers: 'Fetching covers…',
    };

    const upsert = (entityId: number, patch: Partial<ActivityItem>, name?: string, photoUrl?: string | null) => {
      const id = `sync:${entityId}`;
      setTransientActivityItems((prev) => {
        const exists = prev.some((item) => item.id === id);
        if (exists) {
          return prev.map((item) => item.id === id ? { ...item, ...patch } : item);
        }
        if (!name) return prev;
        const newItem: ActivityItem = {
          id,
          kind: 'download',
          visualStatus: 'resolving',
          title: name,
          author: 'Author sync',
          metaLine: 'Monitored authors',
          statusLabel: 'Syncing',
          statusDetail: 'Fetching book data…',
          progressAnimated: true,
          timestamp: Date.now(),
          preview: photoUrl ?? undefined,
          ...patch,
        };
        return [...prev, newItem];
      });
    };

    const scheduleRemoval = (entityId: number, delayMs: number) => {
      const existing = syncActivityTimeoutsRef.current.get(entityId);
      if (existing) clearTimeout(existing);
      const tid = window.setTimeout(() => {
        setTransientActivityItems((prev) => prev.filter((item) => item.id !== `sync:${entityId}`));
        syncActivityTimeoutsRef.current.delete(entityId);
      }, delayMs);
      syncActivityTimeoutsRef.current.set(entityId, tid);
    };

    const onStarted = (data: { entity_id: number; name: string }) => {
      const existing = syncActivityTimeoutsRef.current.get(data.entity_id);
      if (existing) clearTimeout(existing);
      syncActivityTimeoutsRef.current.delete(data.entity_id);
      const photoUrl = monitoredRef.current.find((a) => a.id === data.entity_id)?.photo_url ?? null;
      onShowToast?.(`Syncing ${data.name}…`, 'info', false);
      upsert(data.entity_id, {
        visualStatus: 'resolving',
        statusLabel: 'Syncing',
        statusDetail: 'Fetching book data…',
        progressAnimated: true,
        timestamp: Date.now(),
        preview: photoUrl ?? undefined,
      }, data.name, photoUrl);
    };

    const onProgress = (data: { entity_id: number; phase: string }) => {
      upsert(data.entity_id, { statusDetail: phaseDetail[data.phase] ?? 'Syncing…' });
    };

    const onComplete = (data: { entity_id: number; name: string; books_count: number }) => {
      upsert(data.entity_id, {
        visualStatus: 'complete',
        statusLabel: 'Synced',
        statusDetail: `${data.books_count} books synced`,
        progressAnimated: false,
      }, data.name);
      scheduleRemoval(data.entity_id, 12000);
    };

    const onError = (data: { entity_id: number; error: string }) => {
      upsert(data.entity_id, {
        visualStatus: 'error',
        statusLabel: 'Sync failed',
        statusDetail: data.error,
        progressAnimated: false,
      });
      scheduleRemoval(data.entity_id, 20000);
    };

    socket.on('monitored_sync_started', onStarted);
    socket.on('monitored_sync_progress', onProgress);
    socket.on('monitored_sync_complete', onComplete);
    socket.on('monitored_sync_error', onError);
    return () => {
      socket.off('monitored_sync_started', onStarted);
      socket.off('monitored_sync_progress', onProgress);
      socket.off('monitored_sync_complete', onComplete);
      socket.off('monitored_sync_error', onError);
    };
  }, [socket, setTransientActivityItems, onShowToast]);

  // Batch sync notifications (scheduled + manual sync-all)
  const batchSyncTimeoutsRef = useRef<Map<string, number>>(new Map());
  const [syncAllRunning, setSyncAllRunning] = useState(false);

  useEffect(() => {
    if (!socket || !setTransientActivityItems) return;

    const batchUpsert = (batchId: string, patch: Partial<ActivityItem>) => {
      const id = `batch-sync:${batchId}`;
      setTransientActivityItems((prev) => {
        const exists = prev.some((item) => item.id === id);
        if (exists) return prev.map((item) => item.id === id ? { ...item, ...patch } : item);
        const newItem: ActivityItem = {
          id,
          kind: 'download',
          visualStatus: 'resolving',
          title: 'Sync all authors',
          author: 'Monitored authors',
          metaLine: 'Batch sync',
          statusLabel: 'Syncing',
          statusDetail: 'Starting…',
          progressAnimated: true,
          timestamp: Date.now(),
          ...patch,
        };
        return [...prev, newItem];
      });
    };

    const scheduleBatchRemoval = (batchId: string, delayMs: number) => {
      const existing = batchSyncTimeoutsRef.current.get(batchId);
      if (existing) clearTimeout(existing);
      const tid = window.setTimeout(() => {
        setTransientActivityItems((prev) => prev.filter((item) => item.id !== `batch-sync:${batchId}`));
        batchSyncTimeoutsRef.current.delete(batchId);
      }, delayMs);
      batchSyncTimeoutsRef.current.set(batchId, tid);
    };

    const onBatchStarted = (data: { batch_id: string; total: number }) => {
      setSyncAllRunning(true);
      onShowToast?.(`Syncing ${data.total} authors…`, 'info', false);
      batchUpsert(data.batch_id, {
        visualStatus: 'resolving',
        statusLabel: 'Syncing',
        statusDetail: `0/${data.total}`,
        progressAnimated: true,
        timestamp: Date.now(),
      });
    };

    const onBatchProgress = (data: { batch_id: string; index: number; total: number; entity_name: string; entity_cover?: string }) => {
      batchUpsert(data.batch_id, {
        statusDetail: `${data.entity_name} (${data.index}/${data.total})`,
        progress: Math.round((data.index / data.total) * 100),
        ...(data.entity_cover ? { preview: data.entity_cover } : {}),
      });
    };

    const onBatchComplete = (data: { batch_id: string; total: number; successful: number; failed: number; info?: { entity_name?: string; message?: string; is_error?: boolean }[]; retried?: number; retry_succeeded?: number }) => {
      setSyncAllRunning(false);
      const errors = (data.info ?? []).filter((i) => i.is_error);
      const notices = (data.info ?? []).filter((i) => !i.is_error);
      let statusDetail = `${data.successful}/${data.total} synced`;
      if (data.failed > 0) statusDetail += ` · ${data.failed} failed`;
      if (notices.length > 0) statusDetail += ` · ${notices.length} info`;
      if (data.retried && data.retried > 0) statusDetail += ` · ${data.retry_succeeded ?? 0}/${data.retried} retried`;

      const hasFailed = data.failed > 0;
      batchUpsert(data.batch_id, {
        visualStatus: hasFailed ? 'error' : 'complete',
        statusLabel: hasFailed ? 'Errors' : 'Complete',
        statusDetail,
        progressAnimated: false,
        progress: 100,
      });

      if (hasFailed && errors.length > 0) {
        const failedNames = errors.slice(0, 3).map((e) => e.entity_name).join(', ');
        const suffix = errors.length > 3 ? ` +${errors.length - 3} more` : '';
        onShowToast?.(`Sync failed for: ${failedNames}${suffix}`, 'error', false);
      } else {
        const toastType = hasFailed ? 'error' : 'success';
        onShowToast?.(`Batch sync: ${statusDetail}`, toastType, false);
      }

      if (!hasFailed) {
        scheduleBatchRemoval(data.batch_id, notices.length > 0 ? 20000 : 12000);
      }
    };

    socket.on('monitored_batch_sync_started', onBatchStarted);
    socket.on('monitored_batch_sync_progress', onBatchProgress);
    socket.on('monitored_batch_sync_complete', onBatchComplete);
    return () => {
      socket.off('monitored_batch_sync_started', onBatchStarted);
      socket.off('monitored_batch_sync_progress', onBatchProgress);
      socket.off('monitored_batch_sync_complete', onBatchComplete);
      for (const tid of batchSyncTimeoutsRef.current.values()) clearTimeout(tid);
      batchSyncTimeoutsRef.current.clear();
    };
  }, [socket, setTransientActivityItems, onShowToast]);

  const runSyncAll = useCallback(async () => {
    if (syncAllRunning) return;
    setSyncAllRunning(true);
    try {
      const res = await syncAllMonitoredEntities();
      if (res.already_running) {
        onShowToast?.('A batch sync is already running', 'info', false);
        setSyncAllRunning(false);
      }
      // setSyncAllRunning(false) will happen via the batch_sync_complete event
    } catch {
      setSyncAllRunning(false);
      onShowToast?.('Failed to start batch sync', 'error', false);
    }
  }, [syncAllRunning, onShowToast]);

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
      const photo_url = (typeof (settings as Record<string, unknown>).photo_url === 'string'
        ? ((settings as Record<string, unknown>).photo_url as string)
        : undefined) || entity.best_book_cover_url || undefined;
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
        last_error: entity.last_error || undefined,
        visibility: entity.visibility || 'public',
      };
    };

    const load = async () => {
      setMonitoredError(null);

      // Render from cache immediately so the page feels instant on revisit.
      // The fresh fetch below will update the UI if anything changed.
      try {
        const raw = localStorage.getItem(MONITORED_ENTITY_CACHE_KEY);
        if (raw) {
          const { ts, authors, sources } = JSON.parse(raw) as {
            ts: number;
            authors: MonitoredAuthor[];
            sources: MonitoredBooksSourceEntity[];
          };
          if (Date.now() - ts < MONITORED_ENTITY_CACHE_MAX_AGE && Array.isArray(authors) && authors.length > 0) {
            if (alive) {
              setMonitored(authors);
              setMonitoredBooksSources(sources ?? []);
              setMonitoredLoaded(true);
            }
          }
        }
      } catch {
        // Corrupt cache — ignore, fresh fetch will populate it
      }

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
        // Persist for next visit
        try {
          localStorage.setItem(MONITORED_ENTITY_CACHE_KEY, JSON.stringify({ ts: Date.now(), authors: next, sources: nextSources }));
        } catch {
          // localStorage quota exceeded — non-fatal
        }
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
    const dir = monitoredSortAsc ? 1 : -1;
    const sorted = [...monitored].sort((a, b) => {
      if (monitoredSortBy === 'date_added') {
        const aDate = a.created_at ? Date.parse(a.created_at) : NaN;
        const bDate = b.created_at ? Date.parse(b.created_at) : NaN;
        const aHasDate = Number.isFinite(aDate);
        const bHasDate = Number.isFinite(bDate);
        if (aHasDate && bHasDate && aDate !== bDate) {
          return (bDate - aDate) * dir;
        }
        if (aHasDate !== bHasDate) {
          return (aHasDate ? -1 : 1) * dir;
        }
        return (b.id - a.id) * dir;
      }

      if (monitoredSortBy === 'books_count') {
        const aCount = typeof a.books_count === 'number' ? a.books_count : -1;
        const bCount = typeof b.books_count === 'number' ? b.books_count : -1;
        if (bCount !== aCount) {
          return (bCount - aCount) * dir;
        }
      }

      return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }) * dir;
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
  }, [monitored, monitoredSortBy, monitoredSortAsc]);

  const authorAvailabilityStats = useMemo(() => {
    const map = new Map<number, AuthorAvailabilityStats>();
    for (const book of monitoredBooksRows) {
      if (isEnabledMonitoredFlag(book.hidden)) continue;
      let entry = map.get(book.author_entity_id);
      if (!entry) {
        entry = { ebookAvailable: 0, audiobookAvailable: 0, booksTotal: 0 };
        map.set(book.author_entity_id, entry);
      }
      entry.booksTotal++;
      if (monitoredBookHasFormatAvailable(book, 'ebook')) entry.ebookAvailable++;
      if (monitoredBookHasFormatAvailable(book, 'audiobook')) entry.audiobookAvailable++;
    }
    return map;
  }, [monitoredBooksRows]);

  const monitoredBooksForTable = useMemo(() => {
    const trackedOrFulfilled = monitoredBooksRows.filter((book) => (
      !isEnabledMonitoredFlag(book.hidden)
      && (monitoredBookTracksEbook(book)
        || monitoredBookTracksAudiobook(book)
        || monitoredBookHasAnyAvailable(book))
    ));

    const getReleaseSortKey = (book: MonitoredBookListRow): number => {
      if (typeof book.release_date === 'string' && book.release_date.trim()) {
        const parsed = Date.parse(book.release_date);
        if (Number.isFinite(parsed)) return parsed;
      }
      if (typeof book.publish_year === 'number') return new Date(book.publish_year, 0, 1).getTime();
      return Number.POSITIVE_INFINITY;
    };

    const dir = monitoredBooksSortAsc ? 1 : -1;

    return trackedOrFulfilled.sort((a, b) => {
      if (monitoredBooksSortBy === 'date') {
        const diff = getReleaseSortKey(a) - getReleaseSortKey(b);
        if (diff !== 0) return diff * dir;
      } else if (monitoredBooksSortBy === 'recently_added') {
        const aTime = Date.parse(a.first_seen_at || '');
        const bTime = Date.parse(b.first_seen_at || '');
        const diff = (Number.isFinite(bTime) ? bTime : 0) - (Number.isFinite(aTime) ? aTime : 0);
        if (diff !== 0) return diff * dir;
      } else if (monitoredBooksSortBy === 'popularity') {
        const aPopularity = typeof a.readers_count === 'number' ? a.readers_count : -1;
        const bPopularity = typeof b.readers_count === 'number' ? b.readers_count : -1;
        if (aPopularity !== bPopularity) return (bPopularity - aPopularity) * dir;
      }

      const titleCompare = (a.title || '').localeCompare(b.title || '', undefined, { sensitivity: 'base' });
      if (titleCompare !== 0) return titleCompare * dir;
      return (a.author_name || '').localeCompare(b.author_name || '', undefined, { sensitivity: 'base' });
    });
  }, [monitoredBooksRows, monitoredBooksSortBy, monitoredBooksSortAsc]);

  const upcomingMonitoredBooksForTable = useMemo(() => {
    return monitoredBooksForTable.filter((book) => isMonitoredBookUpcoming(book, _todayStartMs, _currentYear));
  }, [monitoredBooksForTable]);

  const filteredUpcomingByTime = useMemo(() => {
    if (upcomingTimeFilter === 'all') return upcomingMonitoredBooksForTable;
    return upcomingMonitoredBooksForTable.filter(
      (book) => getUpcomingTimeCategory(book, _threeMonthsMs, _currentYear) === upcomingTimeFilter,
    );
  }, [upcomingMonitoredBooksForTable, upcomingTimeFilter]);

  const regularMonitoredBooksForTable = useMemo(() => {
    return monitoredBooksForTable.filter((book) => !isMonitoredBookUpcoming(book, _todayStartMs, _currentYear));
  }, [monitoredBooksForTable]);

  const filteredRegularMonitoredBooksByAvailability = useMemo(() => {
    if (monitoredBooksAvailabilityFilter === 'fulfilled') {
      return regularMonitoredBooksForTable.filter((book) => monitoredBookHasAnyAvailable(book));
    }
    return regularMonitoredBooksForTable.filter((book) => !monitoredBookHasAnyAvailable(book));
  }, [regularMonitoredBooksForTable, monitoredBooksAvailabilityFilter]);

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
    if (landingTab === 'authors') {
      return filteredRegularMonitoredBooksByAvailability;
    }
    if (!normalizedMonitoredBooksFilterQuery) {
      return filteredRegularMonitoredBooksByAvailability;
    }
    return filteredRegularMonitoredBooksByAvailability.filter(matchesMonitoredBooksFilter);
  }, [
    normalizedMonitoredBooksFilterQuery,
    landingTab,
    filteredRegularMonitoredBooksByAvailability,
    matchesMonitoredBooksFilter,
  ]);

  const filteredUpcomingMonitoredBooksForTable = useMemo(() => {
    if (!normalizedMonitoredBooksFilterQuery || landingTab === 'authors') {
      return filteredUpcomingByTime;
    }
    return filteredUpcomingByTime.filter(matchesMonitoredBooksFilter);
  }, [
    normalizedMonitoredBooksFilterQuery,
    landingTab,
    filteredUpcomingByTime,
    matchesMonitoredBooksFilter,
  ]);

  const monitoredBookGroups = useMemo<MonitoredBooksGroup[]>(() => {
    return groupMonitoredBooks(filteredRegularMonitoredBooksForTable, monitoredBooksGroupBy, 'All monitored books', false);
  }, [filteredRegularMonitoredBooksForTable, monitoredBooksGroupBy]);

  const upcomingBookGroups = useMemo<MonitoredBooksGroup[]>(() => {
    return groupMonitoredBooks(filteredUpcomingMonitoredBooksForTable, monitoredBooksGroupBy, 'All upcoming books', true);
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
      localStorage.setItem('monitoredAuthorViewMode', monitoredViewMode);
      localStorage.setItem('monitoredBooksViewMode', monitoredBooksViewMode);
      localStorage.setItem('monitoredBooksSortBy', monitoredBooksSortBy);
      localStorage.setItem('monitoredBooksSortAsc', String(monitoredBooksSortAsc));
      localStorage.setItem('monitoredBooksGroupBy', monitoredBooksGroupBy);
      localStorage.setItem(MONITORED_BOOKS_AVAILABILITY_FILTER_KEY, monitoredBooksAvailabilityFilter);
      localStorage.setItem(MONITORED_UPCOMING_TIME_FILTER_KEY, upcomingTimeFilter);
      localStorage.setItem('monitoredLandingTab', landingTab);
      localStorage.setItem('monitoredAuthorSortBy', monitoredSortBy);
      localStorage.setItem('monitoredAuthorSortAsc', String(monitoredSortAsc));
      localStorage.setItem('monitoredCompactMinWidth', String(monitoredCompactMinWidth));
    } catch {
      // ignore
    }
  }, [
    authorViewMode,
    monitoredViewMode,
    monitoredBooksViewMode,
    monitoredBooksSortBy,
    monitoredBooksSortAsc,
    monitoredBooksGroupBy,
    monitoredBooksAvailabilityFilter,
    upcomingTimeFilter,
    landingTab,
    monitoredSortBy,
    monitoredSortAsc,
    monitoredCompactMinWidth,
  ]);

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

  // All books that are monitored via any entity (author or book kind)
  const monitoredBooksKeySet = useMemo(() => {
    const keys = new Set<string>();
    for (const row of monitoredBooksRows) {
      const provider = (row.provider || '').trim().toLowerCase();
      const providerId = (row.provider_book_id || '').trim().toLowerCase();
      if (provider && providerId) keys.add(`${provider}:${providerId}`);
    }
    return keys;
  }, [monitoredBooksRows]);

  useEffect(() => {
    if (monitoredBooksSources.length === 0) {
      setMonitoredBooksRows([]);
      setMonitoredBooksLoading(false);
      setMonitoredBooksEverLoaded(true);
      setMonitoredBooksLoadError(null);
      return;
    }

    let alive = true;

    void (async () => {
      setMonitoredBooksLoading(true);
      setMonitoredBooksLoadError(null);

      const responses = await Promise.allSettled(
        monitoredBooksSources.map(async (entity) => {
          const booksResponse = await listMonitoredBooks(entity.id);
          return { entity, books: booksResponse.books };
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
      setMonitoredBooksEverLoaded(true);
    })();

    return () => {
      alive = false;
    };
  }, [monitoredBooksSources, monitoredBooksReloadTick]);

  // Re-fetch books when navigating back from author page (hidden state may have changed)
  const prevPathnameRef = useRef(location.pathname);
  useEffect(() => {
    const prev = prevPathnameRef.current;
    prevPathnameRef.current = location.pathname;
    if (prev !== location.pathname && location.pathname === '/monitored') {
      setMonitoredBooksReloadTick((t) => t + 1);
    }
  }, [location.pathname]);

  const monitoredEntityIdByName = useMemo(() => {
    const map = new Map<string, number>();
    for (const item of monitored) {
      map.set(item.name.toLowerCase(), item.id);
    }
    return map;
  }, [monitored]);

  const monitoredEntityErrorById = useMemo(() => {
    const map = new Map<number, string>();
    for (const item of monitored) {
      if (item.last_error) map.set(item.id, item.last_error);
    }
    return map;
  }, [monitored]);

  const monitoredCompactGridStyle = useMemo(() => {
    if (monitoredViewMode !== 'compact') return undefined;
    const minWidth = isDesktop ? monitoredCompactMinWidth : Math.max(80, monitoredCompactMinWidth - 30);
    return { gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))` };
  }, [isDesktop, monitoredViewMode, monitoredCompactMinWidth]);

  const monitoredBooksGridStyle = useMemo(() => {
    if (monitoredBooksViewMode !== 'compact') return undefined;
    const minWidth = isDesktop ? monitoredCompactMinWidth : Math.max(80, monitoredCompactMinWidth - 30);
    return { gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))` };
  }, [isDesktop, monitoredBooksViewMode, monitoredCompactMinWidth]);

  const searchCompactGridStyle = useMemo(() => {
    if (authorViewMode !== 'compact') return undefined;
    const minWidth = isDesktop ? monitoredCompactMinWidth : Math.max(80, monitoredCompactMinWidth - 30);
    return { gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))` };
  }, [isDesktop, authorViewMode, monitoredCompactMinWidth]);

  const isUpcomingTab = landingTab === 'upcoming';
  const activeBookGroups = isUpcomingTab ? upcomingBookGroups : monitoredBookGroups;
  const activeBooksCount = isUpcomingTab ? filteredUpcomingMonitoredBooksForTable.length : filteredRegularMonitoredBooksForTable.length;
  const monitoredBooksCountsReady = monitoredLoaded && (monitored.length === 0 || (monitoredBooksEverLoaded && !monitoredBooksLoading));
  const displayAuthorsCount = monitoredLoaded ? monitored.length : (cachedMonitoredCounts?.authors ?? '–');
  const displayBooksCount = monitoredBooksCountsReady ? filteredRegularMonitoredBooksForTable.length : (cachedMonitoredCounts?.books ?? '–');
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

  const activeBookMonitorState = useMemo(() => {
    if (!activeBookSourceRow) return { monitorEbook: false, monitorAudiobook: false, row: null };
    const provider = (activeBookSourceRow.provider || '').trim();
    const providerId = (activeBookSourceRow.provider_book_id || '').trim();
    const entityId = activeBookSourceRow.author_entity_id;
    const currentRow = monitoredBooksRows.find(
      (r) => r.author_entity_id === entityId && r.provider === provider && r.provider_book_id === providerId
    );
    if (!currentRow) return { monitorEbook: false, monitorAudiobook: false, row: null };
    return {
      monitorEbook: monitoredBookTracksEbook(currentRow),
      monitorAudiobook: monitoredBookTracksAudiobook(currentRow),
      row: currentRow,
    };
  }, [activeBookSourceRow, monitoredBooksRows]);

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
  const allMonitoredAuthorsSelected = monitored.length > 0 && selectedMonitoredAuthorCount === monitored.length;
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

  const toggleSingleBookMonitor = useCallback(async (
    book: MonitoredBookListRow,
    type: 'ebook' | 'audiobook' | 'both'
  ) => {
    const provider = (book.provider || '').trim();
    const providerBookId = (book.provider_book_id || '').trim();
    if (!provider || !providerBookId) return;

    const currentEbook = monitoredBookTracksEbook(book);
    const currentAudiobook = monitoredBookTracksAudiobook(book);

    const patch: { provider: string; provider_book_id: string; monitor_ebook?: boolean; monitor_audiobook?: boolean } = {
      provider,
      provider_book_id: providerBookId,
    };

    if (type === 'ebook') {
      patch.monitor_ebook = !currentEbook;
    } else if (type === 'audiobook') {
      patch.monitor_audiobook = !currentAudiobook;
    } else {
      const targetValue = !(currentEbook && currentAudiobook);
      patch.monitor_ebook = targetValue;
      patch.monitor_audiobook = targetValue;
    }

    // Optimistic update
    setMonitoredBooksRows((prev) =>
      prev.map((r) =>
        r.provider === provider && r.provider_book_id === providerBookId && r.author_entity_id === book.author_entity_id
          ? {
              ...r,
              monitor_ebook: patch.monitor_ebook !== undefined ? patch.monitor_ebook : r.monitor_ebook,
              monitor_audiobook: patch.monitor_audiobook !== undefined ? patch.monitor_audiobook : r.monitor_audiobook,
            }
          : r
      )
    );

    try {
      await updateMonitoredBooksMonitorFlags(book.author_entity_id, patch);
    } catch (e) {
      // Revert on error
      setMonitoredBooksRows((prev) =>
        prev.map((r) =>
          r.provider === provider && r.provider_book_id === providerBookId && r.author_entity_id === book.author_entity_id
            ? { ...r, monitor_ebook: currentEbook, monitor_audiobook: currentAudiobook }
            : r
        )
      );
      console.error('Failed to update monitoring state:', e);
    }
  }, []);

  const toggleSingleBookHidden = useCallback(async (book: MonitoredBookListRow) => {
    const provider = (book.provider || '').trim();
    const providerBookId = (book.provider_book_id || '').trim();
    if (!provider || !providerBookId) return;
    const wasHidden = isEnabledMonitoredFlag(book.hidden);
    const newHidden = !wasHidden;
    setMonitoredBooksRows((prev) =>
      prev.map((r) =>
        r.provider === provider && r.provider_book_id === providerBookId && r.author_entity_id === book.author_entity_id
          ? { ...r, hidden: newHidden, ...(newHidden ? { monitor_ebook: 0, monitor_audiobook: 0 } : {}) }
          : r
      )
    );
    try {
      await updateMonitoredBooksMonitorFlags(book.author_entity_id, { provider, provider_book_id: providerBookId, hidden: newHidden });
    } catch (e) {
      setMonitoredBooksRows((prev) =>
        prev.map((r) =>
          r.provider === provider && r.provider_book_id === providerBookId && r.author_entity_id === book.author_entity_id
            ? { ...r, hidden: wasHidden, ...(newHidden ? { monitor_ebook: book.monitor_ebook, monitor_audiobook: book.monitor_audiobook } : {}) }
            : r
        )
      );
      console.error('Failed to update hidden state:', e);
    }
  }, []);

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

  const selectAllMonitoredAuthors = useCallback(() => {
    const all: Record<string, boolean> = {};
    for (const author of monitored) all[String(author.id)] = true;
    setSelectedMonitoredAuthorKeys(all);
  }, [monitored]);

  const clearMonitoredAuthorSelection = useCallback(() => {
    setSelectedMonitoredAuthorKeys({});
  }, []);

  const runBulkSyncSelectedAuthors = useCallback(async () => {
    if (bulkSyncAuthorsRunning) return;
    const selectedAuthors = monitored.filter((author) => selectedMonitoredAuthorKeys[String(author.id)]);
    if (selectedAuthors.length === 0) return;
    setBulkSyncAuthorsRunning(true);
    try {
      await Promise.all(selectedAuthors.map((author) => syncMonitoredEntity(author.id).catch(() => null)));
    } finally {
      setBulkSyncAuthorsRunning(false);
    }
  }, [bulkSyncAuthorsRunning, monitored, selectedMonitoredAuthorKeys]);

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
      setSearchPanelLeft(null);
      return;
    }
    const id = window.setTimeout(() => {
      monitoredBooksSearchInputRef.current?.focus();
    }, 0);
    // Compute viewport-clamped panel position relative to the trigger container
    const el = monitoredBooksSearchRef.current;
    if (el) {
      const panelWidth = Math.min(420, window.innerWidth * 0.92);
      const rect = el.getBoundingClientRect();
      const idealLeft = rect.right - panelWidth; // right-aligned to trigger
      const clampedLeft = Math.max(8, Math.min(window.innerWidth - panelWidth - 8, idealLeft));
      setSearchPanelLeft(clampedLeft - rect.left); // relative to container
      const triggerCenter = rect.left + rect.width / 2;
      setSearchPanelCaretLeft(Math.max(10, Math.min(panelWidth - 10, triggerCenter - clampedLeft)));
    }
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

      const result = await searchMetadata('', 40, authorSearchSortValue, { author: q }, 1, 'ebook');
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
  }, [authorQuery, authorSearchSortValue, bookSearchSortValue, defaultReleaseContentType, searchScope]);

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

  useEffect(() => {
    if (searchScope !== 'authors') {
      return;
    }
    if (!normalizeAuthor(authorQuery)) {
      return;
    }
    void runAuthorSearch();
  }, [authorSearchSortValue, runAuthorSearch, searchScope, authorQuery]);

  const openMonitoredTab = useCallback((tab: 'authors' | 'books' | 'upcoming' | 'search') => {
    setLandingTab(tab);
    if (tab === 'search') {
      setView('search');
      if (!authorQuery.trim()) {
        window.setTimeout(() => {
          window.scrollTo({ top: 0, behavior: 'smooth' });
        }, 0);
      }
    } else {
      setView('landing');
    }
    if (location.pathname === '/monitored/author') {
      navigate('/monitored');
    }
  }, [authorQuery, location.pathname, navigate]);

  const closeBookMonitorModal = useCallback(() => {
    setBookMonitorModalState({ book: null });
  }, []);

  const openBookMonitorModal = useCallback((book: Book) => {
    setBookMonitorModalState({ book });
  }, []);

  // Wrap onGetReleases to inject combined flag from monitored settings.
  // Skip when: batch auto-downloads, or caller explicitly set combined to false.
  const onGetReleasesWithCombined = useCallback(
    (book: Book, ct: ContentType, entityId?: number | null, action?: ReleasePrimaryAction, opts?: OpenReleasesOptions) => {
      if (!onGetReleases) return Promise.resolve();
      const useCombined = releaseCombinedMode && !opts?.batchAutoDownload && opts?.combined !== false;
      return onGetReleases(book, ct, entityId, action, useCombined ? { ...opts, combined: true } : opts);
    },
    [onGetReleases, releaseCombinedMode],
  );

  const runBookResultInteractiveSearch = useCallback((book: Book, contentType: ContentType) => {
    if (!onGetReleasesWithCombined) {
      return;
    }
    const actionOverride = contentType === 'ebook'
      ? defaultReleaseActionEbook
      : defaultReleaseActionAudiobook;
    void onGetReleasesWithCombined(book, contentType, null, actionOverride);
  }, [defaultReleaseActionAudiobook, defaultReleaseActionEbook, onGetReleasesWithCombined]);

  const isBookSearchResultMonitored = useCallback((book: Book): boolean => {
    const provider = (book.provider || '').trim().toLowerCase();
    const providerId = (book.provider_id || '').trim().toLowerCase();
    if (!provider || !providerId) return false;
    const key = `${provider}:${providerId}`;
    return monitoredSingleBookKeySet.has(key) || monitoredBooksKeySet.has(key);
  }, [monitoredSingleBookKeySet, monitoredBooksKeySet]);

  const findMonitoredBookRow = useCallback((book: Book): MonitoredBookListRow | undefined => {
    const provider = (book.provider || '').trim();
    const providerId = (book.provider_id || '').trim();
    if (!provider || !providerId) return undefined;
    return monitoredBooksRows.find(
      (r) => r.provider === provider && r.provider_book_id === providerId
    );
  }, [monitoredBooksRows]);

  const handleBookSearchResultMonitorAction = useCallback((book: Book) => {
    const existingRow = findMonitoredBookRow(book);
    if (existingRow) {
      // Book is monitored - toggle to unmonitor both formats
      void toggleSingleBookMonitor(existingRow, 'both');
    } else {
      // Book is not monitored - open monitor modal
      openBookMonitorModal(book);
    }
  }, [findMonitoredBookRow, toggleSingleBookMonitor, openBookMonitorModal]);

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
      visibility: 'public',
    });
    setPathSuggestState({ kind: null, open: false, loading: false, parent: null, entries: [], error: null });
  }, [joinPath, monitoredAudiobookRoots, monitoredEbookRoots]);

  const closeMonitorModal = useCallback(() => {
    setMonitorModalState({
      open: false,
      author: null,
      ebookAuthorDir: '',
      audiobookAuthorDir: '',
      monitorEbookMode: 'missing',
      monitorAudiobookMode: 'missing',
      visibility: 'public',
    });
    setPathSuggestState({ kind: null, open: false, loading: false, parent: null, entries: [], error: null });
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
        visibility: monitorModalState.visibility,
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
    // Keep search results visible so user can monitor more authors from the same results.
    // monitoredNames auto-updates from setMonitored above, flipping the button to "Monitored".
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
    openEdit?: boolean;
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
    if (payload.openEdit) params.set('open_edit', '1');

    navigate(`/monitored/author?${params.toString()}`);
  }, [navigate]);

  const openEditAuthorModal = useCallback((entityId: number, authorName: string) => {
    setEditAuthorModalState({ open: true, entityId, authorName });
  }, []);

  const closeEditAuthorModal = useCallback(() => {
    setEditAuthorModalState({ open: false, entityId: null, authorName: '' });
  }, []);

  const handleEditAuthorDeleted = useCallback(() => {
    const { entityId } = editAuthorModalState;
    if (entityId) {
      setMonitored((prev) => prev.filter((author) => author.id !== entityId));
      setMonitoredBooksSources((prev) => prev.filter((entity) => entity.id !== entityId));
      setMonitoredBooksRows((prev) => prev.filter((book) => book.author_entity_id !== entityId));
    }
  }, [editAuthorModalState]);

  const handleEditAuthorSaved = useCallback(() => {
    setMonitoredBooksReloadTick((prev) => prev + 1);
  }, []);

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

  const openMonitoredBookDetails = useCallback((book: MonitoredBookListRow) => {
    setActiveBookSourceRow(book);
    setActiveBookEntityId(book.author_entity_id);
  }, []);

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
    const tracksEbook = monitoredBookTracksEbook(book);
    const tracksAudiobook = monitoredBookTracksAudiobook(book);
    const isFullyMonitored = tracksEbook && tracksAudiobook;

    const menuContent = ({ close }: { close: () => void }) => (
      <div className="py-1">
        <button
          type="button"
          onClick={() => {
            close();
            openMonitoredBookDetails(book);
          }}
          className="w-full px-3 py-2 text-left text-sm hover-surface whitespace-nowrap"
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
          className="w-full px-3 py-2 text-left text-sm hover-surface whitespace-nowrap"
        >
          Search eBooks
        </button>
        <button
          type="button"
          onClick={() => {
            close();
            openMonitoredBookInAuthorPage(book, 'audiobook', 'interactive_search');
          }}
          className="w-full px-3 py-2 text-left text-sm hover-surface whitespace-nowrap"
        >
          Search audiobooks
        </button>
        <div className="my-1 border-t border-[var(--border-muted)]" />
        <div className="px-3 py-1.5 text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">
          Monitoring
        </div>
        <button
          type="button"
          onClick={() => {
            void toggleSingleBookMonitor(book, 'both');
          }}
          className="w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between whitespace-nowrap"
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
            void toggleSingleBookMonitor(book, 'ebook');
          }}
          className="w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between whitespace-nowrap"
        >
          <span>Monitor eBook</span>
          {tracksEbook ? (
            <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
            </svg>
          ) : null}
        </button>
        <button
          type="button"
          onClick={() => {
            void toggleSingleBookMonitor(book, 'audiobook');
          }}
          className="w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between whitespace-nowrap"
        >
          <span>Monitor Audiobook</span>
          {tracksAudiobook ? (
            <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
              <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
            </svg>
          ) : null}
        </button>
      </div>
    );

    if (compact) {
      return (
        <Dropdown
          widthClassName="w-auto"
          align="right"
          panelClassName="z-[2200] min-w-[250px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
          noScrollLimit={true}
          usePortal={true}
          renderTrigger={({ isOpen, toggle }) => (
            <button
              type="button"
              onClick={toggle}
              className={`inline-flex items-center justify-center rounded-full text-gray-600 dark:text-gray-200 hover-action transition-colors h-6 w-6 ${isOpen ? 'text-gray-900 dark:text-gray-100' : ''}`}
              aria-label={`Book actions for ${book.title || 'this book'}`}
              title="Book actions"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
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
          noScrollLimit={true}
          usePortal={true}
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
  }, [openMonitoredBookDetails, openMonitoredBookInAuthorPage, toggleSingleBookMonitor]);

  const clearSearchAndReturn = useCallback(() => {
    setAuthorQuery('');
    setAuthorResults([]);
    setAuthorCards([]);
    setBookSearchResults([]);
    setSearchError(null);
    setView(landingTab === 'search' ? 'search' : 'landing');
  }, [landingTab]);

  const handleHeaderAuthorSearchChange = useCallback((value: string | number | boolean) => {
    const strValue = String(value);
    setAuthorQuery(strValue);
    setSearchScope('authors');
    if (!strValue.trim()) {
      clearSearchAndReturn();
    }
  }, [clearSearchAndReturn, setSearchScope]);

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
      showSearch={isDesktop}
      logoUrl={logoUrl}
      searchInput={authorQuery}
      onSearchChange={handleHeaderAuthorSearchChange}
      onSearch={handleMonitoredHeaderSearch}
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
      onMobileMenuClick={() => setIsMobileNavOpen(true)}
      showMobileSearchToggle={!isDesktop}
      mobileSearchOpen={isMobileSearchOpen}
      onMobileSearchToggle={() => setIsMobileSearchOpen((prev) => !prev)}
      mobileSearchPlaceholder="Search authors..."
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
  const authorDetailsOpenEdit = authorDetailsSearchParams.get('open_edit') === '1';
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

  const mobileNavSheet = (
    <SlideSheet isOpen={isMobileNavOpen} onClose={() => setIsMobileNavOpen(false)} label="Navigation">
      {/* Sheet header */}
      <div className="flex items-center justify-between px-4 py-3 border-b" style={{ borderColor: 'var(--border-muted)' }}>
        <span className="text-sm font-semibold">Navigation</span>
        <button
          type="button"
          onClick={() => setIsMobileNavOpen(false)}
          className="p-1.5 rounded-full hover-action text-gray-500 hover:text-gray-900 dark:hover:text-gray-100"
          aria-label="Close navigation"
        >
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Navigation */}
      <div className="flex-1 overflow-y-auto min-h-0 flex flex-col py-2">
        {/* Search Books — navigates to main search page */}
        <button
          type="button"
          onClick={() => { setIsMobileNavOpen(false); navigate('/'); }}
          className="w-full text-left px-4 py-3 flex items-center gap-3 text-sm transition-colors text-gray-700 dark:text-gray-200 hover-surface"
        >
          <svg className="w-4 h-4 shrink-0 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35m1.35-5.15a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z" />
          </svg>
          <span>Search Books</span>
        </button>

        {/* Monitored Books parent */}
        <div className="px-4 pt-3 pb-1">
          <span className="text-xs font-semibold uppercase tracking-wider text-gray-400 dark:text-gray-500">Monitored Books</span>
        </div>

        {/* Sub-items: Authors, Books, Upcoming */}
        {(['authors', 'books', 'upcoming'] as const).map((tab) => {
          const count = tab === 'authors' ? displayAuthorsCount : tab === 'books' ? displayBooksCount : displayUpcomingCount;
          const label = tab === 'authors' ? 'Authors' : tab === 'books' ? 'Books' : 'Upcoming';
          const isActive = landingTab === tab;
          return (
            <button
              key={tab}
              type="button"
              onClick={() => { openMonitoredTab(tab); setIsMobileNavOpen(false); }}
              className={`w-full text-left pl-8 pr-4 py-2.5 flex items-center justify-between text-sm transition-colors ${isActive ? 'text-emerald-600 dark:text-emerald-400 font-semibold bg-emerald-50/60 dark:bg-emerald-500/10' : 'text-gray-700 dark:text-gray-200 hover-surface'}`}
              aria-pressed={isActive}
            >
              <span>{label}</span>
              <span className={`inline-flex items-center justify-center rounded-full text-xs font-semibold px-1.5 py-0.5 leading-none min-w-[1.25rem] ${isActive ? 'bg-emerald-600 text-white' : 'bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400'}`}>{count}</span>
            </button>
          );
        })}
        {hasStartedSearch && (
          <button
            type="button"
            onClick={() => { openMonitoredTab('search'); setIsMobileNavOpen(false); }}
            className={`w-full text-left pl-8 pr-4 py-2.5 flex items-center justify-between text-sm transition-colors ${landingTab === 'search' ? 'text-emerald-600 dark:text-emerald-400 font-semibold bg-emerald-50/60 dark:bg-emerald-500/10' : 'text-gray-700 dark:text-gray-200 hover-surface'}`}
          >
            <span>Search results</span>
            <span className="inline-flex items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 text-xs font-semibold px-1.5 py-0.5 leading-none min-w-[1.25rem]">{displaySearchCount}</span>
          </button>
        )}

      </div>

      {/* Settings / app actions */}
      <div className="border-t py-2" style={{ borderColor: 'var(--border-muted)' }}>
        <a
          href="https://github.com/calibrain/shelfmark/issues"
          target="_blank"
          rel="noopener noreferrer"
          className="w-full text-left px-4 py-3 hover-surface transition-colors flex items-center gap-3 text-slate-700 dark:text-slate-200 text-sm"
        >
          <svg className="w-5 h-5 shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" d="M3 3v1.5M3 21v-6m0 0 2.77-.693a9 9 0 0 1 6.208.682l.108.054a9 9 0 0 0 6.086.71l3.114-.732a48.524 48.524 0 0 1-.005-10.499l-3.11.732a9 9 0 0 1-6.085-.711l-.108-.054a9 9 0 0 0-6.208-.682L3 4.5M3 15V4.5" />
          </svg>
          <span>Report a Bug</span>
        </a>
        {onSettingsClick && (
          <button
            type="button"
            onClick={() => { setIsMobileNavOpen(false); onSettingsClick(); }}
            disabled={!(canAccessSettings ?? isAdmin)}
            className={`w-full text-left px-4 py-3 transition-colors flex items-center gap-3 text-sm ${(canAccessSettings ?? isAdmin) ? 'hover-surface text-slate-700 dark:text-slate-200' : 'opacity-40 cursor-not-allowed text-slate-700 dark:text-slate-200'}`}
          >
            <svg className="w-5 h-5 shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
            <span>Settings</span>
          </button>
        )}
        {debug && (
          <>
            <button
              type="button"
              className="w-full text-left px-4 py-3 hover-surface transition-colors flex items-center gap-3 text-orange-600 dark:text-orange-400 text-sm"
              onClick={async () => {
                setIsMobileNavOpen(false);
                const loadingId = onShowToast?.('Gathering debug logs…', 'info', true);
                try {
                  const res = await fetch(withBasePath('/api/debug'), { method: 'GET', credentials: 'include' });
                  if (loadingId) onRemoveToast?.(loadingId);
                  if (!res.ok) { onShowToast?.('Debug download failed', 'error'); return; }
                  const cd = res.headers.get('Content-Disposition');
                  const fn = cd?.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/)?.[1]?.replace(/['"]/g, '') ?? 'debug.zip';
                  const url = window.URL.createObjectURL(await res.blob());
                  const a = document.createElement('a'); a.href = url; a.download = fn;
                  document.body.appendChild(a); a.click(); window.URL.revokeObjectURL(url); a.remove();
                  onShowToast?.('Debug logs downloaded', 'success');
                } catch { if (loadingId) onRemoveToast?.(loadingId); onShowToast?.('Debug download failed', 'error'); }
              }}
            >
              <svg className="w-5 h-5 shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M12 12.75c1.148 0 2.278.08 3.383.237 1.037.146 1.866.966 1.866 2.013 0 3.728-2.35 6.75-5.25 6.75S6.75 18.728 6.75 15c0-1.046.83-1.867 1.866-2.013A24.204 24.204 0 0112 12.75zm0 0c2.883 0 5.647.508 8.207 1.44a23.91 23.91 0 01-1.152 6.06M12 12.75c-2.883 0-5.647.508-8.208 1.44.125 2.104.52 4.136 1.153 6.06M12 12.75a2.25 2.25 0 002.248-2.354M12 12.75a2.25 2.25 0 01-2.248-2.354M12 8.25c.995 0 1.971-.08 2.922-.236.403-.066.74-.358.795-.762a3.778 3.778 0 00-.399-2.25M12 8.25c-.995 0-1.97-.08-2.922-.236-.402-.066-.74-.358-.795-.762a3.734 3.734 0 01.4-2.253M12 8.25a2.25 2.25 0 00-2.248 2.146M12 8.25a2.25 2.25 0 012.248 2.146M8.683 5a6.032 6.032 0 01-1.155-1.002c.07-.63.27-1.222.574-1.747m.581 2.749A3.75 3.75 0 0115.318 5m0 0c.427-.283.815-.62 1.155-.999a4.471 4.471 0 00-.575-1.752M4.921 6a24.048 24.048 0 00-.392 3.314c1.668.546 3.416.914 5.223 1.082M19.08 6c.205 1.08.337 2.187.392 3.314a23.882 23.882 0 01-5.223 1.082" />
              </svg>
              <span>Debug</span>
            </button>
            <form action={withBasePath('/api/restart')} method="get" className="w-full">
              <button type="submit" className="w-full text-left px-4 py-3 hover-surface transition-colors flex items-center gap-3 text-orange-600 dark:text-orange-400 text-sm">
                <svg className="w-5 h-5 shrink-0" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0l3.181 3.183a8.25 8.25 0 0013.803-3.7M4.031 9.865a8.25 8.25 0 0113.803-3.7l3.181 3.182m0-4.991v4.99" />
                </svg>
                <span>Restart</span>
              </button>
            </form>
          </>
        )}
      </div>

      {/* User footer */}
      {authRequired && isAuthenticated && username && (
        <div className="border-t px-4 py-3 flex items-center gap-2.5" style={{ borderColor: 'var(--border-muted)' }}>
          <span
            className="w-8 h-8 rounded-full flex items-center justify-center text-xs font-semibold shrink-0 uppercase"
            style={{ backgroundColor: 'var(--hover-surface)', color: 'var(--text)' }}
          >
            {(displayName || username).slice(0, 2)}
          </span>
          <div className="flex-1 min-w-0 truncate text-sm font-medium">{displayName || username}</div>
          {onLogout && (
            <button
              type="button"
              onClick={() => { setIsMobileNavOpen(false); onLogout?.(); }}
              className="shrink-0 p-2 rounded-full hover-action transition-colors text-red-600 dark:text-red-400"
              title="Sign Out"
            >
              <svg className="w-5 h-5" xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
                <path strokeLinecap="round" strokeLinejoin="round" d="M15.75 9V5.25A2.25 2.25 0 0013.5 3h-6a2.25 2.25 0 00-2.25 2.25v13.5A2.25 2.25 0 007.5 21h6a2.25 2.25 0 002.25-2.25V15m3 0l3-3m0 0l-3-3m3 3H9" />
              </svg>
            </button>
          )}
        </div>
      )}
    </SlideSheet>
  );

  if (isAuthorDetailsRoute) {
    return (
      <div className="min-h-screen overflow-x-clip" style={{ backgroundColor: 'var(--background-color)', color: 'var(--text-color)' }}>
        <div className="fixed top-0 left-0 right-0 z-50">
          {monitoredHeader}
        </div>

        <main className="relative w-full max-w-7xl mx-auto px-0 sm:px-6 lg:px-8 py-6 pt-24">
          {authorDetailsAuthor ? (
            <AuthorModal
              author={authorDetailsAuthor}
              displayMode="page"
              onClose={() => navigate('/monitored')}
              onGetReleases={onGetReleasesWithCombined}
              defaultReleaseContentType={authorDetailsEffectiveDefaultContentType}
              defaultReleaseActionEbook={authorDetailsEffectiveDefaultActionEbook}
              defaultReleaseActionAudiobook={authorDetailsEffectiveDefaultActionAudiobook}
              releaseCombinedMode={releaseCombinedMode}
              initialBooksQuery={authorDetailsInitialBooksQuery || undefined}
              initialBookProvider={authorDetailsInitialBookProvider}
              initialBookProviderId={authorDetailsInitialBookProviderId}
              monitoredEntityId={authorDetailsMonitoredEntityId}
              status={status}
              openEditOnMount={authorDetailsOpenEdit}
              renderEmbeddedSearch={renderEmbeddedSearch}
              showBooksInMultipleSeries={showBooksInMultipleSeries}
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
        {mobileNavSheet}
      </div>
    );
  }

  return (
    <div className="min-h-screen overflow-x-clip" style={{ backgroundColor: 'var(--background-color)', color: 'var(--text-color)' }}>
      <div className="fixed top-0 left-0 right-0 z-40">
        {monitoredHeader}
      </div>

      <main className="relative w-full max-w-7xl mx-auto px-0 sm:px-6 lg:px-8 py-6 pt-20 sm:pt-24">
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

      <BookMonitorModal
        book={bookMonitorModalState.book}
        onClose={closeBookMonitorModal}
        onMonitored={(created) => {
          setMonitoredBooksSources((prev) => {
            if (prev.some((entity) => entity.id === created.id)) return prev;
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
        }}
      />

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
              <section className="rounded-none sm:rounded-2xl border-0 sm:border border-black/10 dark:border-white/10 bg-transparent sm:bg-white/80 sm:dark:bg-white/5 sm:shadow-xl sm:overflow-hidden flex flex-col" style={{ maxHeight: 'calc(100dvh - 8rem)' }}>
                <div className="flex flex-wrap items-center pb-2 border-b border-black/10 dark:border-white/10 relative z-10 gap-3 gap-y-2 shrink-0 px-4 pt-4">
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
                      className="hidden sm:block rounded-full p-1.5 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
                      aria-label="Back to home"
                      title="Back"
                    >
                      <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.5 7.5 12 15 4.5" />
                      </svg>
                    </button>
                    {/* Mobile: compact active-tab label */}
                    <div className="sm:hidden flex items-center gap-2">
                      <span className="text-base font-bold text-gray-900 dark:text-gray-100">
                        {landingTab === 'authors' ? 'Authors' : landingTab === 'books' ? 'Books' : landingTab === 'upcoming' ? 'Upcoming' : 'Search'}
                      </span>
                      <span className="inline-flex items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 text-xs font-semibold px-2 py-0.5 min-w-[1.5rem] leading-none">
                        {landingTab === 'authors' ? displayAuthorsCount : landingTab === 'books' ? displayBooksCount : landingTab === 'upcoming' ? displayUpcomingCount : displaySearchCount}
                      </span>
                    </div>
                    {/* Desktop: full tab pills */}
                    <div className="hidden sm:inline-flex items-center rounded-full border border-[var(--border-muted)] bg-transparent">
                      <button
                        type="button"
                        onClick={() => openMonitoredTab('authors')}
                        className={`px-3.5 py-2 rounded-full text-sm font-medium transition-colors flex items-center gap-1.5 ${landingTab === 'authors' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                        aria-pressed={landingTab === 'authors'}
                      >
                        <span className="hidden sm:inline">Monitored </span>Authors
                        <span className={`inline-flex items-center justify-center rounded-full text-xs font-semibold px-1.5 py-0.5 leading-none min-w-[1.25rem] ${landingTab === 'authors' ? 'bg-white/25 text-white' : 'bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400'}`}>{displayAuthorsCount}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => openMonitoredTab('books')}
                        className={`px-3.5 py-2 rounded-full text-sm font-medium transition-colors flex items-center gap-1.5 ${landingTab === 'books' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                        aria-pressed={landingTab === 'books'}
                      >
                        <span className="hidden sm:inline">Monitored </span>Books
                        <span className={`inline-flex items-center justify-center rounded-full text-xs font-semibold px-1.5 py-0.5 leading-none min-w-[1.25rem] ${landingTab === 'books' ? 'bg-white/25 text-white' : 'bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400'}`}>{displayBooksCount}</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => openMonitoredTab('upcoming')}
                        className={`px-3.5 py-2 rounded-full text-sm font-medium transition-colors flex items-center gap-1.5 ${landingTab === 'upcoming' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                        aria-pressed={landingTab === 'upcoming'}
                      >
                        Upcoming
                        <span className={`inline-flex items-center justify-center rounded-full text-xs font-semibold px-1.5 py-0.5 leading-none min-w-[1.25rem] ${landingTab === 'upcoming' ? 'bg-white/25 text-white' : 'bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400'}`}>{displayUpcomingCount}</span>
                      </button>
                      {hasStartedSearch ? (
                        <button
                          type="button"
                          onClick={() => openMonitoredTab('search')}
                          className="px-3.5 py-2 rounded-full text-sm font-medium transition-colors text-gray-700 dark:text-gray-200 hover-action flex items-center gap-1.5"
                          aria-pressed={false}
                        >
                          Search
                          <span className="inline-flex items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 text-xs font-semibold px-1.5 py-0.5 leading-none min-w-[1.25rem]">{displaySearchCount}</span>
                        </button>
                      ) : null}
                    </div>
                  </div>
                  <div className="flex items-center gap-2 flex-wrap justify-end ml-auto">
                    {landingTab === 'authors' && hasActiveMonitoredAuthorSelection ? (
                      <div className="flex items-center gap-1 shrink-0">
                        {/* Select all */}
                        <button
                          type="button"
                          onClick={allMonitoredAuthorsSelected ? clearMonitoredAuthorSelection : selectAllMonitoredAuthors}
                          className="relative flex items-center justify-center h-8 w-8 rounded-full hover-action text-gray-600 dark:text-gray-300"
                          title={allMonitoredAuthorsSelected ? 'Deselect all authors' : `Select all authors (${monitored.length})`}
                          aria-label={allMonitoredAuthorsSelected ? 'Deselect all authors' : `Select all authors (${monitored.length})`}
                        >
                          {allMonitoredAuthorsSelected ? (
                            /* check-square (all selected) */
                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8" aria-hidden="true">
                              <rect x="3" y="3" width="18" height="18" rx="3" />
                              <path strokeLinecap="round" strokeLinejoin="round" d="m7.5 12 3 3 6-6" />
                            </svg>
                          ) : (
                            /* minus-square (partial) */
                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8" aria-hidden="true">
                              <rect x="3" y="3" width="18" height="18" rx="3" />
                              <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h8" />
                            </svg>
                          )}
                        </button>
                        {/* Deselect all */}
                        <button
                          type="button"
                          onClick={clearMonitoredAuthorSelection}
                          className="flex items-center justify-center h-8 w-8 rounded-full hover-action text-gray-600 dark:text-gray-300"
                          title="Deselect all"
                          aria-label="Deselect all"
                        >
                          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8" aria-hidden="true">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                          </svg>
                        </button>
                        {/* Refresh selected */}
                        <button
                          type="button"
                          onClick={runBulkSyncSelectedAuthors}
                          disabled={bulkSyncAuthorsRunning}
                          className="flex items-center justify-center h-8 w-8 rounded-full hover-action text-gray-600 dark:text-gray-300 disabled:opacity-50"
                          title={`Refresh selected authors (${selectedMonitoredAuthorCount})`}
                          aria-label={`Refresh selected authors (${selectedMonitoredAuthorCount})`}
                        >
                          <svg className={`w-5 h-5${bulkSyncAuthorsRunning ? ' animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8" aria-hidden="true">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
                          </svg>
                        </button>
                        {/* Delete selected */}
                        <button
                          type="button"
                          onClick={() => setBulkDeleteAuthorsConfirmOpen(true)}
                          className="relative flex items-center justify-center h-8 w-8 rounded-full border border-red-500/40 text-red-600 dark:text-red-400 hover-action"
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
                      </div>
                    ) : landingTab === 'authors' && monitored.length > 0 ? (
                      <div className="flex items-center gap-1 shrink-0">
                        {/* Sync All */}
                        <button
                          type="button"
                          onClick={runSyncAll}
                          disabled={syncAllRunning}
                          className="flex items-center justify-center h-8 w-8 rounded-full hover-action text-gray-600 dark:text-gray-300 disabled:opacity-50"
                          title="Sync all authors"
                          aria-label="Sync all authors"
                        >
                          <svg className={`w-5 h-5${syncAllRunning ? ' animate-spin' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8" aria-hidden="true">
                            <path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" />
                          </svg>
                        </button>
                      </div>
                    ) : null}
                    <div className="relative" ref={monitoredBooksSearchRef}>
                        <button
                          type="button"
                          onClick={() => {
                            if (monitoredBooksSearchExpanded) {
                              setMonitoredBooksSearchExpanded(false);
                              setMonitoredBooksSearchOpen(false);
                            } else {
                              setMonitoredBooksSearchExpanded(true);
                              setMonitoredBooksSearchOpen(Boolean(monitoredBooksSearchQuery.trim()));
                            }
                          }}
                          className={`p-2 rounded-full transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${(monitoredBooksSearchQuery.trim() || monitoredBooksSearchExpanded) ? 'text-white bg-emerald-600 hover:bg-emerald-700' : 'hover-action text-gray-900 dark:text-gray-100'}`}
                          title="Search monitored books"
                          aria-label="Search monitored books"
                        >
                          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8" aria-hidden="true">
                            <path strokeLinecap="round" strokeLinejoin="round" d="m21 21-4.35-4.35m1.35-5.15a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z" />
                          </svg>
                        </button>
                        {monitoredBooksSearchExpanded ? (
                          <div
                            className="absolute top-full mt-2 z-[120]"
                            style={{
                              width: `min(${window.innerWidth * 0.92}px, 420px)`,
                              left: searchPanelLeft !== null ? searchPanelLeft : undefined,
                              right: searchPanelLeft === null ? 0 : undefined,
                            }}
                          >
                            {/* Caret — rotated square, seamless border */}
                            <span
                              className="pointer-events-none absolute z-10"
                              aria-hidden="true"
                              style={{
                                top: -8,
                                left: searchPanelCaretLeft - 8,
                                width: 16,
                                height: 16,
                                transform: 'rotate(45deg)',
                                background: 'var(--bg)',
                                borderTop: '1px solid var(--border-muted)',
                                borderLeft: '1px solid var(--border-muted)',
                              }}
                            />
                            <div className="rounded-xl border border-[var(--border-muted)] shadow-2xl overflow-hidden" style={{ background: 'var(--bg)' }}>
                            <div className="flex items-center gap-2 px-3 py-2 border-b border-black/10 dark:border-white/10">
                              <svg className="w-4 h-4 text-gray-500 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8" aria-hidden="true">
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
                                className="w-full bg-transparent outline-none text-sm text-gray-700 dark:text-gray-200 placeholder:text-gray-500"
                                aria-label="Search monitored books"
                                autoFocus
                              />
                              {monitoredBooksSearchQuery ? (
                                <button
                                  type="button"
                                  onClick={() => {
                                    setMonitoredBooksSearchQuery('');
                                    setMonitoredBooksSearchOpen(false);
                                  }}
                                  className="p-0.5 rounded-full text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action flex-shrink-0"
                                  aria-label="Clear monitored books search"
                                  title="Clear"
                                >
                                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                                  </svg>
                                </button>
                              ) : null}
                            </div>

                            {landingTab === 'authors' && monitoredBooksSearchOpen && monitoredBooksSearchQuery.trim() ? (
                              <div className="max-h-72 overflow-y-auto">
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
                                      const hasEbookAvailable = isEnabledMonitoredFlag(row.has_ebook_available);
                                      const hasAudiobookAvailable = isEnabledMonitoredFlag(row.has_audiobook_available);
                                      const hasAnyAvailable = hasEbookAvailable || hasAudiobookAvailable;
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
                                          className={`w-full text-left px-3 py-2 border-b last:border-b-0 border-black/5 dark:border-white/5 hover-surface ${hasAnyAvailable ? 'bg-emerald-500/[0.07] dark:bg-emerald-500/[0.09]' : ''}`}
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
                                              {hasEbookAvailable ? (
                                                <span className="inline-flex items-center justify-center min-w-[40px] px-1.5 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase bg-emerald-500/20 text-emerald-700 dark:text-emerald-300">{(row.ebook_available_format || 'ebook').toUpperCase()}</span>
                                              ) : null}
                                              {hasAudiobookAvailable ? (
                                                <span className="inline-flex items-center justify-center min-w-[40px] px-1.5 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase bg-violet-500/20 text-violet-700 dark:text-violet-300">{(row.audiobook_available_format || 'audio').toUpperCase()}</span>
                                              ) : null}
                                            </div>
                                          </div>
                                          {hasAnyAvailable ? null : (
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
                          </div>
                        ) : null}
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
                                  {([
                                    { key: 'alphabetical' as const, label: 'Name' },
                                    { key: 'date_added' as const, label: 'Date Added' },
                                    { key: 'books_count' as const, label: 'Number of Books' },
                                  ] as const).map(({ key, label }) => {
                                    const active = monitoredSortBy === key;
                                    return (
                                      <button
                                        key={key}
                                        type="button"
                                        className={`w-full px-2.5 py-1.5 rounded-lg text-left text-sm hover-surface flex items-center justify-between ${active ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                        onClick={() => {
                                          if (active) {
                                            setMonitoredSortAsc((prev) => !prev);
                                          } else {
                                            setMonitoredSortBy(key);
                                            setMonitoredSortAsc(true);
                                          }
                                        }}
                                        role="option"
                                        aria-selected={active}
                                      >
                                        {label}
                                        {active && (
                                          <svg className={`w-3.5 h-3.5 shrink-0 transition-transform ${monitoredSortAsc ? '' : 'rotate-180'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" /></svg>
                                        )}
                                      </button>
                                    );
                                  })}
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
                        {landingTab === 'books' ? (
                          <div className="inline-flex items-center rounded-full border border-[var(--border-muted)] bg-transparent p-0.5">
                            <button
                              type="button"
                              onClick={() => setMonitoredBooksAvailabilityFilter('missing')}
                              className={`px-2.5 py-1.5 rounded-full text-[11px] font-medium transition-colors ${monitoredBooksAvailabilityFilter === 'missing' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                              aria-pressed={monitoredBooksAvailabilityFilter === 'missing'}
                              title="Show missing monitored books"
                            >
                              Missing
                            </button>
                            <button
                              type="button"
                              onClick={() => setMonitoredBooksAvailabilityFilter('fulfilled')}
                              className={`px-2.5 py-1.5 rounded-full text-[11px] font-medium transition-colors ${monitoredBooksAvailabilityFilter === 'fulfilled' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                              aria-pressed={monitoredBooksAvailabilityFilter === 'fulfilled'}
                              title="Show fulfilled monitored books"
                            >
                              Fulfilled
                            </button>
                          </div>
                        ) : landingTab === 'upcoming' ? (
                          <div className="inline-flex items-center rounded-full border border-[var(--border-muted)] bg-transparent p-0.5">
                            {([ ['all', 'All'], ['3months', 'Soon'], ['this_year', 'This Year'], ['tba', 'TBA'] ] as const).map(([value, label]) => (
                              <button
                                key={value}
                                type="button"
                                onClick={() => setUpcomingTimeFilter(value)}
                                className={`px-2.5 py-1.5 rounded-full text-[11px] font-medium transition-colors ${upcomingTimeFilter === value ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
                                aria-pressed={upcomingTimeFilter === value}
                              >
                                {label}
                              </button>
                            ))}
                          </div>
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
                                  {([
                                    { key: 'title' as const, label: 'Title' },
                                    { key: 'date' as const, label: 'Date' },
                                    { key: 'recently_added' as const, label: 'Recently Added' },
                                    { key: 'popularity' as const, label: 'Popularity' },
                                  ] as const).map(({ key, label }) => {
                                    const active = monitoredBooksSortBy === key;
                                    return (
                                      <button
                                        key={key}
                                        type="button"
                                        className={`w-full px-2.5 py-1.5 rounded-lg text-left text-sm hover-surface flex items-center justify-between ${active ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                        onClick={() => {
                                          if (active) {
                                            setMonitoredBooksSortAsc((prev) => !prev);
                                          } else {
                                            setMonitoredBooksSortBy(key);
                                            setMonitoredBooksSortAsc(key !== 'popularity');
                                          }
                                        }}
                                        role="option"
                                        aria-selected={active}
                                      >
                                        {label}
                                        {active && (
                                          <svg className={`w-3.5 h-3.5 shrink-0 transition-transform ${monitoredBooksSortAsc ? '' : 'rotate-180'}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M5 15l7-7 7 7" /></svg>
                                        )}
                                      </button>
                                    );
                                  })}
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

                <div className="flex-1 min-h-0 overflow-y-auto px-4 pt-3 pb-4">
                  {landingTab === 'authors' ? (
                    <MonitoredAuthorsView
                      viewMode={monitoredViewMode}
                      authors={monitoredAuthorsForCards}
                      entityIdByName={monitoredEntityIdByName}
                      entityErrorById={monitoredEntityErrorById}
                      authorAvailabilityStats={authorAvailabilityStats}
                      selectedAuthorKeys={selectedMonitoredAuthorKeys}
                      hasActiveSelection={hasActiveMonitoredAuthorSelection}
                      compactGridStyle={monitoredCompactGridStyle}
                      onNavigate={(author) => navigateToAuthorPage(author)}
                      onEdit={(entityId, name) => void openEditAuthorModal(entityId, name)}
                      onToggleSelect={toggleMonitoredAuthorSelection}
                    />
                  ) : (
                    <MonitoredBooksView
                      isLoading={monitoredBooksLoading}
                      isUpcomingTab={isUpcomingTab}
                      activeBooksCount={activeBooksCount}
                      viewMode={monitoredBooksViewMode}
                      bookGroups={activeBookGroups}
                      groupBy={monitoredBooksGroupBy}
                      selectedBookKeys={selectedMonitoredBookKeys}
                      booksGridStyle={monitoredBooksGridStyle}
                      compactMinWidth={monitoredCompactMinWidth}
                      loadError={monitoredBooksLoadError}
                      showLoadError={landingTab === 'books' || landingTab === 'upcoming'}
                      onOpenDetails={openMonitoredBookDetails}
                      onToggleSelect={toggleMonitoredBookSelection}
                      getSelectionKey={getMonitoredBookSelectionKey}
                      renderBookActions={renderMonitoredBookActions}
                    />
                  )}
                </div>
              </section>
            )
          ) : (
            <MonitoredSearchView
              searchScope={searchScope}
              authorViewMode={authorViewMode}
              bookSearchViewMode={bookSearchViewMode}
              authorSearchViewOptions={authorSearchViewOptions}
              bookSearchViewOptions={bookSearchViewOptions}
              onAuthorViewModeChange={(next) => setAuthorViewMode(next as 'compact' | 'list')}
              onBookSearchViewModeChange={(next) => setBookSearchViewMode(next as 'compact' | 'list')}
              authorQuery={authorQuery}
              isSearching={isSearching}
              bookSearchResults={bookSearchResults}
              authorSearchSortValue={authorSearchSortValue}
              onAuthorSortChange={setAuthorSearchSortValue}
              onScopeChange={setSearchScope}
              bookSearchSortValue={bookSearchSortValue}
              monitoredSearchSortOptions={monitoredSearchSortOptions}
              onBookSortChange={setBookSearchSortValue}
              authorResults={authorResults}
              authorCards={authorCards}
              monitoredNames={monitoredNames}
              onAuthorNavigate={navigateToAuthorPage}
              onMonitorAuthor={openMonitorModal}
              onBookDetails={handleBookSearchResultDetails}
              onBookGet={handleBookSearchResultGet}
              onBookMonitorAction={handleBookSearchResultMonitorAction}
              isBookMonitored={isBookSearchResultMonitored}
              getMonitorResultButtonState={getMonitorResultButtonState}
              noopDownload={noopDownload}
              compactGridStyle={searchCompactGridStyle}
              onTabChange={openMonitoredTab}
              onBack={() => { if (onBack) { onBack(); } else { navigate('/'); } }}
              displayAuthorsCount={displayAuthorsCount}
              displayBooksCount={displayBooksCount}
              displayUpcomingCount={displayUpcomingCount}
              displaySearchCount={displaySearchCount}
            />
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
                            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">Type or browse to set the root author folder.</div>
                          </div>
                          <div className="relative">
                            <div className="flex items-stretch rounded-xl border border-black/10 dark:border-white/10 overflow-hidden bg-white/80 dark:bg-white/10">
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
                                className="flex-1 min-w-0 px-3 py-2 text-sm bg-transparent outline-none"
                              />
                              {suffix ? (
                                <div className="flex items-center px-2 text-sm text-gray-400 dark:text-gray-500 border-l border-black/10 dark:border-white/10 bg-black/5 dark:bg-white/5 select-none whitespace-nowrap">
                                  {suffix}
                                </div>
                              ) : null}
                            </div>
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
                            <div className="text-xs text-gray-500 dark:text-gray-400 truncate">Type or browse to set the root author folder.</div>
                          </div>
                          <div className="relative">
                            <div className="flex items-stretch rounded-xl border border-black/10 dark:border-white/10 overflow-hidden bg-white/80 dark:bg-white/10">
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
                                className="flex-1 min-w-0 px-3 py-2 text-sm bg-transparent outline-none"
                              />
                              {suffix ? (
                                <div className="flex items-center px-2 text-sm text-gray-400 dark:text-gray-500 border-l border-black/10 dark:border-white/10 bg-black/5 dark:bg-white/5 select-none whitespace-nowrap">
                                  {suffix}
                                </div>
                              ) : null}
                            </div>
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

                {authRequired && (
                  <label className="flex items-center gap-2 mt-1">
                    <button
                      type="button"
                      onClick={() =>
                        setMonitorModalState((prev) => ({
                          ...prev,
                          visibility: prev.visibility === 'public' ? 'private' : 'public',
                        }))
                      }
                      className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
                        monitorModalState.visibility === 'public'
                          ? 'bg-emerald-500'
                          : 'bg-gray-400 dark:bg-gray-600'
                      }`}
                    >
                      <span
                        className={`pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transform transition-transform ${
                          monitorModalState.visibility === 'public' ? 'translate-x-4' : 'translate-x-0'
                        }`}
                      />
                    </button>
                    <span className="text-xs text-gray-600 dark:text-gray-300">
                      {monitorModalState.visibility === 'public' ? 'Shared with all users' : 'Private (only you)'}
                    </span>
                  </label>
                )}
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
        entityId={activeBookEntityId}
        provider={activeBookSourceRow?.provider ?? null}
        providerBookId={activeBookSourceRow?.provider_book_id ?? null}
        monitorEbook={activeBookMonitorState.monitorEbook}
        monitorAudiobook={activeBookMonitorState.monitorAudiobook}
        onClose={() => {
          setActiveBookEntityId(null);
          setActiveBookSourceRow(null);
        }}
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
        onToggleMonitor={activeBookMonitorState.row ? (type) => void toggleSingleBookMonitor(activeBookMonitorState.row!, type) : undefined}
        hidden={activeBookMonitorState.row ? isEnabledMonitoredFlag(activeBookMonitorState.row.hidden) : false}
        onToggleHidden={activeBookMonitorState.row ? () => void toggleSingleBookHidden(activeBookMonitorState.row!) : undefined}
        onSetReleaseDate={activeBookEntityId != null && activeBookSourceRow ? (_row) => {
          setActiveBookEntityId(null);
          setActiveBookSourceRow(null);
          setReleaseDateBook({ row: activeBookSourceRow, entityId: activeBookEntityId });
        } : undefined}
      />

      {releaseDateBook && (
        <ReleaseDateSearchModal
          book={releaseDateBook.row}
          entityId={releaseDateBook.entityId}
          onClose={() => setReleaseDateBook(null)}
          onMatched={(releaseDate) => {
            setMonitoredBooksRows((prev) =>
              prev.map((r) =>
                r.provider === releaseDateBook.row.provider && r.provider_book_id === releaseDateBook.row.provider_book_id && r.entity_id === releaseDateBook.row.entity_id
                  ? { ...r, release_date: releaseDate, publish_year: releaseDate ? parseInt(releaseDate.slice(0, 4), 10) : r.publish_year }
                  : r
              )
            );
          }}
        />
      )}

      <FolderBrowserModal
        open={folderBrowserState.open}
        title={folderBrowserState.kind === 'audiobook' ? 'Select audiobook folder' : 'Select ebook folder'}
        initialPath={folderBrowserState.initialPath}
        overlayZIndex={1300}
        onClose={() => setFolderBrowserState({ open: false, kind: null, initialPath: null })}
        onSelect={(path) => {
          const authorName = monitorModalState.author?.name;
          const suggested = authorName ? joinPath(path, authorName) : path;
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

      <EditAuthorModal
        open={editAuthorModalState.open}
        entityId={editAuthorModalState.entityId}
        authorName={editAuthorModalState.authorName}
        onClose={closeEditAuthorModal}
        onDeleted={handleEditAuthorDeleted}
        onSaved={handleEditAuthorSaved}
      />

      {mobileNavSheet}
    </div>
  );
};
