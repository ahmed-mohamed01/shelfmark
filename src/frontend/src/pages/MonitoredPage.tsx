import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';

import { ActivityItem } from '../components/activity/activityTypes';
import { AuthorModal, AuthorModalAuthor } from '../components/AuthorModal';
import { AuthorMonitorModal, type AuthorMonitorTarget } from '../components/AuthorMonitorModal';
import { BookDetailsModal } from '../components/BookDetailsModal';
import { BookMonitorModal } from '../components/BookMonitorModal';
import { Dropdown } from '../components/Dropdown';
import { EditAuthorModal } from '../components/EditAuthorModal';
import {
  FloatingSelectionBar,
  type FloatingSelectionBarAction,
} from '../components/FloatingSelectionBar';
import { Header } from '../components/Header';
import {
  MonitoredAuthorsView,
  type AuthorAvailabilityStats,
} from '../components/MonitoredAuthorsView';
import {
  MonitoredBooksView,
  type MonitoredBookListRow,
  type MonitoredBooksGroup,
} from '../components/MonitoredBooksView';
import { MonitoredHistoryTab } from '../components/MonitoredHistoryTab';
import { MonitoredSearchView } from '../components/MonitoredSearchView';
import ReleaseDateSearchModal from '../components/ReleaseDateSearchModal';
import { SearchScopeDropdown } from '../components/SearchScopeDropdown';
import { ViewModeToggle, type ViewModeToggleOption } from '../components/ViewModeToggle';
import { useSocket } from '../contexts/SocketContext';
import { useSwipe } from '../hooks/useSwipe';
import { searchMetadata } from '../services/api';
import {
  listMonitoredEntities,
  listMonitoredBooks,
  updateMonitoredBooksMonitorFlags,
  MetadataAuthor,
  MonitoredEntity,
  MonitoredAuthorBookSearchRow,
  searchMonitoredAuthorBooks,
  searchMetadataAuthors,
  deleteMonitoredAuthorsByIds,
  syncMonitoredEntity,
  syncAllMonitoredEntities,
} from '../services/monitoredApi';
import {
  Book,
  ButtonStateInfo,
  ContentType,
  OpenReleasesOptions,
  ReleasePrimaryAction,
  SortOption,
  StatusData,
} from '../types';
import { ActivityStatusCounts } from '../utils/activityBadge';
import { hapticTap } from '../utils/haptics';
import {
  isEnabledMonitoredFlag,
  isMonitoredBookRecentlyReleased,
  isMonitoredBookUpcoming,
  monitoredBookHasAnyAvailable,
  monitoredBookHasFormatAvailable,
  monitoredBookHasMissingTrackedFormat,
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
    const groupKey =
      groupBy === 'author'
        ? `author:${(row.author_name || 'Unknown author').trim().toLowerCase()}`
        : `year:${typeof row.publish_year === 'number' ? row.publish_year : 'unknown'}`;
    const groupTitle =
      groupBy === 'author'
        ? row.author_name || 'Unknown author'
        : typeof row.publish_year === 'number'
          ? String(row.publish_year)
          : 'Unknown year';

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

const LANDING_TAB_ORDER: readonly ('authors' | 'books' | 'upcoming' | 'search' | 'history')[] = [
  'authors',
  'books',
  'upcoming',
  'search',
  'history',
];

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
  renderEmbeddedSearch?: (
    book: Book,
    contentType: ContentType,
    monitoredEntityId?: number | null,
  ) => ReactNode;
  onShowToast?: (
    message: string,
    type?: 'info' | 'success' | 'error',
    persistent?: boolean,
  ) => string;
  onRemoveToast?: (id: string) => void;
  setTransientActivityItems?: (updater: (prev: ActivityItem[]) => ActivityItem[]) => void;
}

const normalizeAuthor = (value: string): string => {
  return value.split(/\s+/).join(' ').trim();
};

const extractPrimaryAuthorName = (value: string): string => {
  const first = (value || '').split(',')[0] || '';
  return normalizeAuthor(first);
};

const SEARCH_VIEW_ICON_GRID = (
  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M4.5 4.5h6.75v6.75H4.5V4.5Zm8.25 0h6.75v6.75h-6.75V4.5ZM4.5 12.75h6.75v6.75H4.5v-6.75Zm8.25 0h6.75v6.75h-6.75v-6.75Z"
    />
  </svg>
);

const SEARCH_VIEW_ICON_LIST = (
  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M8.25 6.75h12M8.25 12h12m-12 5.25h12M3.75 6.75h.007v.008H3.75V6.75Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0ZM3.75 12h.007v.008H3.75V12Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Zm-.375 5.25h.007v.008H3.75v-.008Zm.375 0a.375.375 0 1 1-.75 0 .375.375 0 0 1 .75 0Z"
    />
  </svg>
);

const SEARCH_VIEW_ICON_COMPACT_LINES = (
  <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
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
const MONITORED_RELEASES_SHOW_UNMONITORED_KEY = 'monitoredReleasesShowUnmonitored';

// Recomputed when the date changes (e.g. page stays open past midnight).
function _computeDateConstants() {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  const todayStartMs = d.getTime();
  const currentYear = d.getFullYear();
  const d3 = new Date(todayStartMs);
  d3.setMonth(d3.getMonth() + 3);
  return { todayStartMs, currentYear, threeMonthsMs: d3.getTime() };
}
let _dateConstants = _computeDateConstants();
let _dateConstantsDay = new Date().toDateString();

function _getDateConstants() {
  const today = new Date().toDateString();
  if (today !== _dateConstantsDay) {
    _dateConstants = _computeDateConstants();
    _dateConstantsDay = today;
  }
  return _dateConstants;
}

type UpcomingTimeFilter = 'all' | 'recent' | '3months' | 'this_year' | 'tba';

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
  if (typeof book.publish_year === 'number' && book.publish_year === currentYear)
    return 'this_year';
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
      typeof parsed.authors === 'number' &&
      typeof parsed.books === 'number' &&
      typeof parsed.upcoming === 'number' &&
      typeof parsed.search === 'number'
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
  onMonitoredClick: _onMonitoredClick,
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
  onRemoveToast: _onRemoveToast,
  setTransientActivityItems,
}: MonitoredPageProps) => {
  const [landingTab, setLandingTab] = useState<
    'authors' | 'books' | 'upcoming' | 'search' | 'history' | 'author-detail'
  >(() => {
    const saved = localStorage.getItem('monitoredLandingTab');
    return saved === 'books' || saved === 'upcoming' || saved === 'search' || saved === 'history'
      ? saved
      : 'authors';
  });
  const [view, setView] = useState<'landing' | 'search'>('landing');
  const [activeAuthorDetail, setActiveAuthorDetail] = useState<{
    author: AuthorModalAuthor;
    monitoredEntityId: number | null;
    initialBooksQuery?: string;
    initialBookProvider?: string | null;
    initialBookProviderId?: string | null;
    openEdit?: boolean;
  } | null>(null);
  const [authorDetailBooksQuery, setAuthorDetailBooksQuery] = useState('');
  const [authorBooksControls, setAuthorBooksControls] = useState<
    import('../components/MonitoredAuthorBooksTab').AuthorBooksTabControls | null
  >(null);
  const [swipeDirection, setSwipeDirection] = useState<'left' | 'right' | null>(null);
  const prevTabIndexRef = useRef(
    LANDING_TAB_ORDER.indexOf(landingTab as (typeof LANDING_TAB_ORDER)[number]),
  );
  const historyExportRef = useRef<(() => void) | null>(null);
  const historyClearRef = useRef<(() => void) | null>(null);
  const [historyDateRange, setHistoryDateRange] = useState('');
  const mobileTabRefs = useRef<Record<string, HTMLElement | null>>({});
  const mobileTabIndicatorRef = useRef<HTMLDivElement | null>(null);
  const desktopTabRefs = useRef<Record<string, HTMLElement | null>>({});
  const desktopTabIndicatorRef = useRef<HTMLDivElement | null>(null);
  const skipIndicatorTransition = useRef(false);

  const syncMobileTabIndicator = useCallback(() => {
    const el = mobileTabIndicatorRef.current;
    const btn = mobileTabRefs.current[landingTab];
    if (!el || !btn) return;
    const container = btn.parentElement;
    if (!container) return;
    const shouldAnchored = skipIndicatorTransition.current;
    if (shouldAnchored) {
      // Anchor the indicator at the "authors" tab position instantly, then slide to target
      const anchorBtn = mobileTabRefs.current['authors'];
      if (anchorBtn) {
        el.style.transition = 'none';
        const cr = container.getBoundingClientRect();
        const ar = anchorBtn.getBoundingClientRect();
        el.style.left = `${ar.left - cr.left + container.scrollLeft + 8}px`;
        el.style.width = `${ar.width - 16}px`;
      }
      skipIndicatorTransition.current = false;
    }
    btn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    requestAnimationFrame(() => {
      if (shouldAnchored) el.style.transition = '';
      const containerRect = container.getBoundingClientRect();
      const btnRect = btn.getBoundingClientRect();
      el.style.left = `${btnRect.left - containerRect.left + container.scrollLeft + 8}px`;
      el.style.width = `${btnRect.width - 16}px`;
    });
  }, [landingTab]);

  const syncDesktopTabIndicator = useCallback(() => {
    const el = desktopTabIndicatorRef.current;
    const btn = desktopTabRefs.current[landingTab];
    if (!el || !btn) return;
    const container = btn.parentElement;
    if (!container) return;
    const shouldAnchored = skipIndicatorTransition.current;
    if (shouldAnchored) {
      // Anchor the indicator at the "authors" tab position instantly, then slide to target
      const anchorBtn = desktopTabRefs.current['authors'];
      if (anchorBtn) {
        el.style.transition = 'none';
        const cr = container.getBoundingClientRect();
        const ar = anchorBtn.getBoundingClientRect();
        el.style.left = `${ar.left - cr.left + 12}px`;
        el.style.width = `${ar.width - 24}px`;
      }
      skipIndicatorTransition.current = false;
    }
    requestAnimationFrame(() => {
      if (shouldAnchored) el.style.transition = '';
      const containerRect = container.getBoundingClientRect();
      const btnRect = btn.getBoundingClientRect();
      el.style.left = `${btnRect.left - containerRect.left + 12}px`;
      el.style.width = `${btnRect.width - 24}px`;
    });
  }, [landingTab]);

  useEffect(() => {
    syncMobileTabIndicator();
  }, [syncMobileTabIndicator]);
  useEffect(() => {
    syncDesktopTabIndicator();
  }, [syncDesktopTabIndicator]);

  // When the tab set composition changes (author opens/closes), skip indicator animation
  const prevActiveAuthorDetail = useRef(activeAuthorDetail);
  if (prevActiveAuthorDetail.current !== activeAuthorDetail) {
    prevActiveAuthorDetail.current = activeAuthorDetail;
    skipIndicatorTransition.current = true;
  }

  // Re-sync tab indicators on window resize
  useEffect(() => {
    const onResize = () => {
      syncMobileTabIndicator();
      syncDesktopTabIndicator();
    };
    window.addEventListener('resize', onResize);
    return () => window.removeEventListener('resize', onResize);
  }, [syncMobileTabIndicator, syncDesktopTabIndicator]);

  // Track tab change direction for animation
  useEffect(() => {
    const newIndex = LANDING_TAB_ORDER.indexOf(landingTab as (typeof LANDING_TAB_ORDER)[number]);
    const oldIndex = prevTabIndexRef.current;
    if (newIndex !== oldIndex && newIndex >= 0) {
      setSwipeDirection(newIndex > oldIndex ? 'left' : 'right');
      prevTabIndexRef.current = newIndex;
      const timer = setTimeout(() => setSwipeDirection(null), 250);
      return () => clearTimeout(timer);
    }
  }, [landingTab]);
  const [searchScope, setSearchScope] = useState<'authors' | 'books'>('authors');
  // Accents the header search pill's border while its scope popover is open.
  const [headerScopeOpen, setHeaderScopeOpen] = useState(false);
  const [authorQuery, setAuthorQuery] = useState('');
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [monitoredError, setMonitoredError] = useState<string | null>(null);
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
  const [monitoredBooksSortBy, setMonitoredBooksSortBy] = useState<
    'title' | 'date' | 'recently_added' | 'popularity'
  >(() => {
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
  const [monitoredBooksGroupBy, setMonitoredBooksGroupBy] = useState<'none' | 'author' | 'year'>(
    () => {
      const saved = localStorage.getItem('monitoredBooksGroupBy');
      return saved === 'author' || saved === 'year' ? saved : 'none';
    },
  );
  const [monitoredBooksAvailabilityFilter, setMonitoredBooksAvailabilityFilter] = useState<
    'missing' | 'fulfilled'
  >(() => {
    const saved = localStorage.getItem(MONITORED_BOOKS_AVAILABILITY_FILTER_KEY);
    return saved === 'fulfilled' ? 'fulfilled' : 'missing';
  });
  const [upcomingTimeFilter, setUpcomingTimeFilter] = useState<UpcomingTimeFilter>(() => {
    const saved = localStorage.getItem(MONITORED_UPCOMING_TIME_FILTER_KEY);
    return saved === '3months' || saved === 'this_year' || saved === 'tba' ? saved : 'all';
  });
  const [showUnmonitoredInReleases, setShowUnmonitoredInReleases] = useState<boolean>(() => {
    return localStorage.getItem(MONITORED_RELEASES_SHOW_UNMONITORED_KEY) === 'true';
  });
  const [monitoredSortBy, setMonitoredSortBy] = useState<
    'alphabetical' | 'date_added' | 'books_count'
  >(() => {
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
    return Math.max(
      MONITORED_COMPACT_MIN_WIDTH_MIN,
      Math.min(MONITORED_COMPACT_MIN_WIDTH_MAX, parsed),
    );
  });
  const [monitored, setMonitored] = useState<MonitoredAuthor[]>([]);
  const [monitoredBooksSources, setMonitoredBooksSources] = useState<MonitoredBooksSourceEntity[]>(
    [],
  );
  const [monitoredBooksReloadTick, setMonitoredBooksReloadTick] = useState(0);
  const [monitoredLoaded, setMonitoredLoaded] = useState(false);
  const [monitoredBooksRows, setMonitoredBooksRows] = useState<MonitoredBookListRow[]>([]);
  const [monitoredBooksLoading, setMonitoredBooksLoading] = useState(false);
  const [monitoredBooksEverLoaded, setMonitoredBooksEverLoaded] = useState(false);
  const [monitoredBooksLoadError, setMonitoredBooksLoadError] = useState<string | null>(null);
  const [activeBookEntityId, setActiveBookEntityId] = useState<number | null>(null);
  const [activeBookSourceRow, setActiveBookSourceRow] = useState<MonitoredBookListRow | null>(null);
  const [releaseDateBook, setReleaseDateBook] = useState<{
    row: MonitoredBookListRow;
    entityId: number;
  } | null>(null);
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
  const [monitoredBooksSearchResults, setMonitoredBooksSearchResults] = useState<
    MonitoredAuthorBookSearchRow[]
  >([]);
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
  const [selectedMonitoredBookKeys, setSelectedMonitoredBookKeys] = useState<
    Record<string, boolean>
  >({});
  const [selectedMonitoredAuthorKeys, setSelectedMonitoredAuthorKeys] = useState<
    Record<string, boolean>
  >({});
  const [bulkUnmonitorRunning, setBulkUnmonitorRunning] = useState(false);
  const [bulkBookDownloadRunning, setBulkBookDownloadRunning] = useState<
    Record<ContentType, boolean>
  >({ ebook: false, audiobook: false });
  const [bulkDeleteAuthorsRunning, setBulkDeleteAuthorsRunning] = useState(false);
  const [bulkDeleteAuthorsConfirmOpen, setBulkDeleteAuthorsConfirmOpen] = useState(false);
  const [bulkSyncAuthorsRunning, setBulkSyncAuthorsRunning] = useState(false);
  const [cachedMonitoredCounts, setCachedMonitoredCounts] =
    useState<MonitoredCountsSnapshot | null>(() => readMonitoredCountsSnapshot());

  const [editAuthorModalState, setEditAuthorModalState] = useState<{
    open: boolean;
    entityId: number | null;
    authorName: string;
  }>({
    open: false,
    entityId: null,
    authorName: '',
  });

  // Author currently being configured in <AuthorMonitorModal>; null when closed.
  const [monitorAuthorTarget, setMonitorAuthorTarget] = useState<AuthorMonitorTarget | null>(null);

  const [bookMonitorModalState, setBookMonitorModalState] = useState<{
    book: Book | null;
  }>({
    book: null,
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

    const upsert = (
      entityId: number,
      patch: Partial<ActivityItem>,
      name?: string,
      photoUrl?: string | null,
    ) => {
      const id = `sync:${entityId}`;
      setTransientActivityItems((prev) => {
        const exists = prev.some((item) => item.id === id);
        if (exists) {
          return prev.map((item) => (item.id === id ? { ...item, ...patch } : item));
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
      setSyncingEntityId(data.entity_id);
      upsert(
        data.entity_id,
        {
          visualStatus: 'resolving',
          statusLabel: 'Syncing',
          statusDetail: 'Fetching book data…',
          progressAnimated: true,
          timestamp: Date.now(),
          preview: photoUrl ?? undefined,
        },
        data.name,
        photoUrl,
      );
    };

    const onProgress = (data: { entity_id: number; phase: string }) => {
      upsert(data.entity_id, { statusDetail: phaseDetail[data.phase] ?? 'Syncing…' });
    };

    const onComplete = (data: { entity_id: number; name: string; books_count: number }) => {
      upsert(
        data.entity_id,
        {
          visualStatus: 'complete',
          statusLabel: 'Synced',
          statusDetail: `${data.books_count} books synced`,
          progressAnimated: false,
        },
        data.name,
      );
      scheduleRemoval(data.entity_id, 12000);
      setSyncingEntityId((cur) => (cur === data.entity_id ? null : cur));
      setMonitoredBooksReloadTick((t) => t + 1);
    };

    const onError = (data: { entity_id: number; error: string }) => {
      upsert(data.entity_id, {
        visualStatus: 'error',
        statusLabel: 'Sync failed',
        statusDetail: data.error,
        progressAnimated: false,
      });
      scheduleRemoval(data.entity_id, 20000);
      setSyncingEntityId((cur) => (cur === data.entity_id ? null : cur));
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
      for (const tid of syncActivityTimeoutsRef.current.values()) clearTimeout(tid);
      syncActivityTimeoutsRef.current.clear();
    };
  }, [socket, setTransientActivityItems, onShowToast]);

  // Batch sync notifications (scheduled + manual sync-all)
  const batchSyncTimeoutsRef = useRef<Map<string, number>>(new Map());
  const [syncAllRunning, setSyncAllRunning] = useState(false);
  const [syncingEntityId, setSyncingEntityId] = useState<number | null>(null);

  useEffect(() => {
    if (!socket || !setTransientActivityItems) return;

    const batchUpsert = (batchId: string, patch: Partial<ActivityItem>) => {
      const id = `batch-sync:${batchId}`;
      setTransientActivityItems((prev) => {
        const exists = prev.some((item) => item.id === id);
        if (exists) return prev.map((item) => (item.id === id ? { ...item, ...patch } : item));
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
        setTransientActivityItems((prev) =>
          prev.filter((item) => item.id !== `batch-sync:${batchId}`),
        );
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

    const onBatchProgress = (data: {
      batch_id: string;
      index: number;
      total: number;
      entity_name: string;
      entity_cover?: string;
    }) => {
      batchUpsert(data.batch_id, {
        statusDetail: `${data.entity_name} (${data.index}/${data.total})`,
        progress: Math.round((data.index / data.total) * 100),
        ...(data.entity_cover ? { preview: data.entity_cover } : {}),
      });
    };

    const onBatchComplete = (data: {
      batch_id: string;
      total: number;
      successful: number;
      failed: number;
      info?: { entity_name?: string; message?: string; is_error?: boolean }[];
      retried?: number;
      retry_succeeded?: number;
    }) => {
      setSyncAllRunning(false);
      const errors = (data.info ?? []).filter((i) => i.is_error);
      const notices = (data.info ?? []).filter((i) => !i.is_error);
      let statusDetail = `${data.successful}/${data.total} synced`;
      if (data.failed > 0) statusDetail += ` · ${data.failed} failed`;
      if (notices.length > 0) statusDetail += ` · ${notices.length} info`;
      if (data.retried && data.retried > 0)
        statusDetail += ` · ${data.retry_succeeded ?? 0}/${data.retried} retried`;

      const hasFailed = data.failed > 0;
      batchUpsert(data.batch_id, {
        visualStatus: hasFailed ? 'error' : 'complete',
        statusLabel: hasFailed ? 'Errors' : 'Complete',
        statusDetail,
        progressAnimated: false,
        progress: 100,
      });

      if (hasFailed && errors.length > 0) {
        const failedNames = errors
          .slice(0, 3)
          .map((e) => e.entity_name)
          .join(', ');
        const suffix = errors.length > 3 ? ` +${errors.length - 3} more` : '';
        onShowToast?.(`Sync failed for: ${failedNames}${suffix}`, 'error', false);
      } else {
        const toastType = hasFailed ? 'error' : 'success';
        onShowToast?.(`Batch sync: ${statusDetail}`, toastType, false);
      }

      if (!hasFailed) {
        scheduleBatchRemoval(data.batch_id, notices.length > 0 ? 20000 : 12000);
      }
      setMonitoredBooksReloadTick((t) => t + 1);
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

      const settings =
        entity.settings && typeof entity.settings === 'object' ? entity.settings : {};
      const photo_url =
        (typeof (settings as Record<string, unknown>).photo_url === 'string'
          ? ((settings as Record<string, unknown>).photo_url as string)
          : undefined) ||
        entity.best_book_cover_url ||
        undefined;
      const books_count =
        typeof (settings as Record<string, unknown>).books_count === 'number'
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
          if (
            Date.now() - ts < MONITORED_ENTITY_CACHE_MAX_AGE &&
            Array.isArray(authors) &&
            authors.length > 0
          ) {
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
            const settings =
              entity.settings && typeof entity.settings === 'object'
                ? (entity.settings as Record<string, unknown>)
                : undefined;
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
          localStorage.setItem(
            MONITORED_ENTITY_CACHE_KEY,
            JSON.stringify({ ts: Date.now(), authors: next, sources: nextSources }),
          );
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

  const sortBooksForDisplay = useCallback(
    (books: MonitoredBookListRow[]) => {
      const getReleaseSortKey = (book: MonitoredBookListRow): number => {
        if (typeof book.release_date === 'string' && book.release_date.trim()) {
          const parsed = Date.parse(book.release_date);
          if (Number.isFinite(parsed)) return parsed;
        }
        if (typeof book.publish_year === 'number')
          return new Date(book.publish_year, 0, 1).getTime();
        return Number.POSITIVE_INFINITY;
      };

      const dir = monitoredBooksSortAsc ? 1 : -1;

      return [...books].sort((a, b) => {
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

        const titleCompare = (a.title || '').localeCompare(b.title || '', undefined, {
          sensitivity: 'base',
        });
        if (titleCompare !== 0) return titleCompare * dir;
        return (
          (a.author_name || '').localeCompare(b.author_name || '', undefined, {
            sensitivity: 'base',
          }) * dir
        );
      });
    },
    [monitoredBooksSortBy, monitoredBooksSortAsc],
  );

  const monitoredBooksForTable = useMemo(() => {
    const trackedOrFulfilled = monitoredBooksRows.filter(
      (book) =>
        !isEnabledMonitoredFlag(book.hidden) &&
        (monitoredBookTracksEbook(book) ||
          monitoredBookTracksAudiobook(book) ||
          monitoredBookHasAnyAvailable(book)),
    );
    return sortBooksForDisplay(trackedOrFulfilled);
  }, [monitoredBooksRows, sortBooksForDisplay]);

  // Releases-tab base set: same as monitoredBooksForTable, but when the
  // "Show unmonitored books" overflow toggle is on, the tracks/available
  // requirement is dropped — only `hidden` still excludes a book.
  const releasesBaseRows = useMemo(() => {
    if (!showUnmonitoredInReleases) return monitoredBooksForTable;
    const visible = monitoredBooksRows.filter((book) => !isEnabledMonitoredFlag(book.hidden));
    return sortBooksForDisplay(visible);
  }, [monitoredBooksForTable, monitoredBooksRows, showUnmonitoredInReleases, sortBooksForDisplay]);

  const upcomingMonitoredBooksForTable = useMemo(() => {
    const { todayStartMs } = _getDateConstants();
    return releasesBaseRows.filter((book) => isMonitoredBookUpcoming(book, todayStartMs));
  }, [releasesBaseRows]);

  const recentlyReleasedBooksForTable = useMemo(() => {
    const { todayStartMs } = _getDateConstants();
    return releasesBaseRows.filter((book) => isMonitoredBookRecentlyReleased(book, todayStartMs));
  }, [releasesBaseRows]);

  const filteredUpcomingByTime = useMemo(() => {
    if (upcomingTimeFilter === 'all' || upcomingTimeFilter === 'recent')
      return upcomingMonitoredBooksForTable;
    const { threeMonthsMs, currentYear } = _getDateConstants();
    return upcomingMonitoredBooksForTable.filter(
      (book) => getUpcomingTimeCategory(book, threeMonthsMs, currentYear) === upcomingTimeFilter,
    );
  }, [upcomingMonitoredBooksForTable, upcomingTimeFilter]);

  const regularMonitoredBooksForTable = useMemo(() => {
    const { todayStartMs } = _getDateConstants();
    return monitoredBooksForTable.filter((book) => !isMonitoredBookUpcoming(book, todayStartMs));
  }, [monitoredBooksForTable]);

  const filteredRegularMonitoredBooksByAvailability = useMemo(() => {
    if (monitoredBooksAvailabilityFilter === 'fulfilled') {
      return regularMonitoredBooksForTable.filter(
        (book) => monitoredBookHasAnyAvailable(book) && !monitoredBookHasMissingTrackedFormat(book),
      );
    }
    return regularMonitoredBooksForTable.filter((book) =>
      monitoredBookHasMissingTrackedFormat(book),
    );
  }, [regularMonitoredBooksForTable, monitoredBooksAvailabilityFilter]);

  const normalizedMonitoredBooksFilterQuery = monitoredBooksSearchQuery.trim().toLowerCase();

  const matchesMonitoredBooksFilter = useCallback(
    (book: MonitoredBookListRow): boolean => {
      if (!normalizedMonitoredBooksFilterQuery) return true;
      const fields = [
        book.title || '',
        book.author_name || '',
        book.series_name || '',
        book.provider || '',
        book.provider_book_id || '',
        typeof book.publish_year === 'number' ? String(book.publish_year) : '',
      ];
      return fields.some((field) =>
        field.toLowerCase().includes(normalizedMonitoredBooksFilterQuery),
      );
    },
    [normalizedMonitoredBooksFilterQuery],
  );

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

  const filteredRecentlyReleasedBooksForTable = useMemo(() => {
    if (!normalizedMonitoredBooksFilterQuery || landingTab === 'authors') {
      return recentlyReleasedBooksForTable;
    }
    return recentlyReleasedBooksForTable.filter(matchesMonitoredBooksFilter);
  }, [
    normalizedMonitoredBooksFilterQuery,
    landingTab,
    recentlyReleasedBooksForTable,
    matchesMonitoredBooksFilter,
  ]);

  const monitoredBookGroups = useMemo<MonitoredBooksGroup[]>(() => {
    return groupMonitoredBooks(
      filteredRegularMonitoredBooksForTable,
      monitoredBooksGroupBy,
      'All monitored books',
      false,
    );
  }, [filteredRegularMonitoredBooksForTable, monitoredBooksGroupBy]);

  const upcomingBookGroups = useMemo<MonitoredBooksGroup[]>(() => {
    return groupMonitoredBooks(
      filteredUpcomingMonitoredBooksForTable,
      monitoredBooksGroupBy,
      'All upcoming releases',
      true,
    );
  }, [filteredUpcomingMonitoredBooksForTable, monitoredBooksGroupBy]);

  const recentlyReleasedBookGroups = useMemo<MonitoredBooksGroup[]>(() => {
    return groupMonitoredBooks(
      filteredRecentlyReleasedBooksForTable,
      monitoredBooksGroupBy,
      'All recently released books',
      true,
    );
  }, [filteredRecentlyReleasedBooksForTable, monitoredBooksGroupBy]);

  useEffect(() => {
    try {
      sessionStorage.setItem(MONITORED_BOOKS_SEARCH_QUERY_KEY, monitoredBooksSearchQuery);
    } catch {
      // ignore
    }
  }, [monitoredBooksSearchQuery]);

  useEffect(() => {
    try {
      sessionStorage.setItem(
        MONITORED_BOOKS_SEARCH_EXPANDED_KEY,
        monitoredBooksSearchExpanded ? '1' : '0',
      );
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
      upcoming: upcomingMonitoredBooksForTable.length + recentlyReleasedBooksForTable.length,
      // runAuthorSearch clears every scope's results before each run, so at
      // most one term is non-zero. Summing keeps the badge stable when the
      // header scope selector flips without a new search having run.
      search: bookSearchResults.length + authorResults.length,
    };
    setCachedMonitoredCounts(snapshot);
    try {
      sessionStorage.setItem(MONITORED_COUNTS_CACHE_KEY, JSON.stringify(snapshot));
    } catch {
      // ignore
    }
  }, [
    monitoredLoaded,
    monitoredAuthorsForCards.length,
    filteredRegularMonitoredBooksForTable.length,
    upcomingMonitoredBooksForTable.length,
    recentlyReleasedBooksForTable.length,
    searchScope,
    bookSearchResults.length,
    authorResults.length,
  ]);

  useEffect(() => {
    try {
      localStorage.setItem('authorViewMode', authorViewMode);
      localStorage.setItem('monitoredAuthorViewMode', monitoredViewMode);
      localStorage.setItem('monitoredBooksViewMode', monitoredBooksViewMode);
      localStorage.setItem('monitoredBooksSortBy', monitoredBooksSortBy);
      localStorage.setItem('monitoredBooksSortAsc', String(monitoredBooksSortAsc));
      localStorage.setItem('monitoredBooksGroupBy', monitoredBooksGroupBy);
      localStorage.setItem(
        MONITORED_BOOKS_AVAILABILITY_FILTER_KEY,
        monitoredBooksAvailabilityFilter,
      );
      localStorage.setItem(MONITORED_UPCOMING_TIME_FILTER_KEY, upcomingTimeFilter);
      localStorage.setItem(
        MONITORED_RELEASES_SHOW_UNMONITORED_KEY,
        String(showUnmonitoredInReleases),
      );
      if (landingTab !== 'author-detail') localStorage.setItem('monitoredLandingTab', landingTab);
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
    showUnmonitoredInReleases,
    landingTab,
    monitoredSortBy,
    monitoredSortAsc,
    monitoredCompactMinWidth,
  ]);

  // Clear selections and haptic feedback when switching tabs
  const hasTabSwitched = useRef(false);
  useEffect(() => {
    setSelectedMonitoredAuthorKeys({});
    setSelectedMonitoredBookKeys({});
    if (hasTabSwitched.current) hapticTap();
    hasTabSwitched.current = true;
  }, [landingTab]);

  const monitoredNames = useMemo(
    () => new Set(monitored.map((a) => a.name.toLowerCase())),
    [monitored],
  );

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
        }),
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
        const bookSettingsAuthorName =
          typeof settings.book_author === 'string' ? settings.book_author.trim() : '';
        const bookSettingsSourceUrl =
          typeof settings.book_source_url === 'string' ? settings.book_source_url.trim() : '';

        for (const book of books || []) {
          const displayAuthor =
            entity.kind === 'book'
              ? extractPrimaryAuthorName(book.authors || '') ||
                bookSettingsAuthorName ||
                entity.name ||
                'Unknown author'
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
      setMonitoredBooksLoadError(
        failedCount > 0 ? 'Some monitored books could not be loaded.' : null,
      );
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
    const minWidth = isDesktop
      ? monitoredCompactMinWidth
      : Math.max(80, monitoredCompactMinWidth - 30);
    return { gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))` };
  }, [isDesktop, monitoredViewMode, monitoredCompactMinWidth]);

  const monitoredBooksGridStyle = useMemo(() => {
    if (monitoredBooksViewMode !== 'compact') return undefined;
    const minWidth = isDesktop
      ? monitoredCompactMinWidth
      : Math.max(80, monitoredCompactMinWidth - 30);
    return { gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))` };
  }, [isDesktop, monitoredBooksViewMode, monitoredCompactMinWidth]);

  const searchCompactGridStyle = useMemo(() => {
    if (authorViewMode !== 'compact') return undefined;
    const minWidth = isDesktop
      ? monitoredCompactMinWidth
      : Math.max(80, monitoredCompactMinWidth - 30);
    return { gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))` };
  }, [isDesktop, authorViewMode, monitoredCompactMinWidth]);

  const isUpcomingTab = landingTab === 'upcoming';
  const showOnlyRecent = isUpcomingTab && upcomingTimeFilter === 'recent';
  const showBothSections =
    isUpcomingTab &&
    upcomingTimeFilter === 'all' &&
    filteredRecentlyReleasedBooksForTable.length > 0;
  const activeBookGroups = isUpcomingTab
    ? showOnlyRecent
      ? recentlyReleasedBookGroups
      : showBothSections
        ? [...recentlyReleasedBookGroups, ...upcomingBookGroups]
        : upcomingBookGroups
    : monitoredBookGroups;
  const activeBooksCount = isUpcomingTab
    ? showOnlyRecent
      ? filteredRecentlyReleasedBooksForTable.length
      : filteredUpcomingMonitoredBooksForTable.length +
        (upcomingTimeFilter === 'all' ? filteredRecentlyReleasedBooksForTable.length : 0)
    : filteredRegularMonitoredBooksForTable.length;
  const monitoredBooksCountsReady =
    monitoredLoaded &&
    (monitored.length === 0 || (monitoredBooksEverLoaded && !monitoredBooksLoading));
  const displayAuthorsCount = monitoredLoaded
    ? monitored.length
    : (cachedMonitoredCounts?.authors ?? '–');
  const displayBooksCount = monitoredBooksCountsReady
    ? filteredRegularMonitoredBooksForTable.length
    : (cachedMonitoredCounts?.books ?? '–');
  const displayUpcomingCount = monitoredBooksCountsReady
    ? upcomingMonitoredBooksForTable.length + recentlyReleasedBooksForTable.length
    : (cachedMonitoredCounts?.upcoming ?? '–');
  const displaySearchCount = monitoredLoaded
    ? // At most one scope's results are populated (see the counts snapshot
      // effect), so the sum is scope-flip-stable.
      bookSearchResults.length + authorResults.length
    : (cachedMonitoredCounts?.search ?? '–');
  const monitoredSearchSortOptions =
    metadataSortOptions && metadataSortOptions.length > 0
      ? metadataSortOptions
      : [{ value: 'relevance', label: 'Most relevant' }];
  const authorSearchViewOptions = useMemo<ViewModeToggleOption[]>(
    () => [
      { value: 'compact', label: 'Compact view', icon: SEARCH_VIEW_ICON_GRID },
      { value: 'list', label: 'List view', icon: SEARCH_VIEW_ICON_LIST },
    ],
    [],
  );
  const bookSearchViewOptions = useMemo<ViewModeToggleOption[]>(
    () => [
      { value: 'compact', label: 'Compact view', icon: SEARCH_VIEW_ICON_COMPACT_LINES },
      { value: 'list', label: 'List view', icon: SEARCH_VIEW_ICON_LIST },
    ],
    [],
  );

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
    const allowedKeys =
      landingTab === 'upcoming' ? upcomingBookSearchKeySet : monitoredBookSearchKeySet;
    return monitoredBooksSearchResults.filter((row) => allowedKeys.has(getSearchRowKey(row)));
  }, [
    landingTab,
    monitoredBooksSearchResults,
    monitoredBookSearchKeySet,
    upcomingBookSearchKeySet,
    getSearchRowKey,
  ]);

  const groupedAuthorsTabSearchResults = useMemo(() => {
    const q = monitoredBooksSearchQuery.trim().toLowerCase();
    if (!q || landingTab !== 'authors') {
      return { authors: [], books: [] };
    }
    const authorsByEntity = new Map<
      number,
      {
        entityId: number;
        name: string;
        provider: string | null;
        providerId: string | null;
        photoUrl: string | null;
        bookCount: number;
        sourceRow: MonitoredAuthorBookSearchRow;
      }
    >();
    const books: MonitoredAuthorBookSearchRow[] = [];
    for (const row of scopedMonitoredBooksSearchResults) {
      const authorName = (row.author_name || '').toLowerCase();
      const bookTitle = (row.book_title || '').toLowerCase();
      const seriesName = (row.series_name || '').toLowerCase();
      const authorHit = authorName.includes(q);
      const bookHit = bookTitle.includes(q) || seriesName.includes(q);
      if (authorHit) {
        const existing = authorsByEntity.get(row.entity_id);
        if (existing) {
          existing.bookCount += 1;
        } else {
          authorsByEntity.set(row.entity_id, {
            entityId: row.entity_id,
            name: row.author_name,
            provider: row.author_provider || null,
            providerId: row.author_provider_id || null,
            photoUrl: row.author_photo_url || null,
            bookCount: 1,
            sourceRow: row,
          });
        }
      }
      if (bookHit) {
        books.push(row);
      }
    }
    return {
      authors: Array.from(authorsByEntity.values()),
      books,
    };
  }, [landingTab, monitoredBooksSearchQuery, scopedMonitoredBooksSearchResults]);

  const activeBookMonitorState = useMemo(() => {
    if (!activeBookSourceRow) return { monitorEbook: false, monitorAudiobook: false, row: null };
    const provider = (activeBookSourceRow.provider || '').trim();
    const providerId = (activeBookSourceRow.provider_book_id || '').trim();
    const entityId = activeBookSourceRow.author_entity_id;
    const currentRow = monitoredBooksRows.find(
      (r) =>
        r.author_entity_id === entityId &&
        r.provider === provider &&
        r.provider_book_id === providerId,
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
  const allMonitoredAuthorsSelected =
    monitored.length > 0 && selectedMonitoredAuthorCount === monitored.length;
  const selectedSingleMonitoredAuthorName =
    selectedMonitoredAuthors.length === 1
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

  const toggleMonitoredBookSelection = useCallback(
    (book: MonitoredBookListRow) => {
      const key = getMonitoredBookSelectionKey(book);
      setSelectedMonitoredBookKeys((prev) => ({
        ...prev,
        [key]: !prev[key],
      }));
    },
    [getMonitoredBookSelectionKey],
  );

  const selectAllVisibleMonitoredBooks = useCallback(() => {
    const visibleBooks = activeBookGroups.flatMap((g) => g.rows);
    const all: Record<string, boolean> = {};
    for (const book of visibleBooks) all[getMonitoredBookSelectionKey(book)] = true;
    setSelectedMonitoredBookKeys(all);
  }, [activeBookGroups, getMonitoredBookSelectionKey]);

  const clearMonitoredBookSelection = useCallback(() => setSelectedMonitoredBookKeys({}), []);

  const allVisibleMonitoredBooksSelected = useMemo(() => {
    const visibleBooks = activeBookGroups.flatMap((g) => g.rows);
    return (
      visibleBooks.length > 0 &&
      visibleBooks.every((book) =>
        Boolean(selectedMonitoredBookKeys[getMonitoredBookSelectionKey(book)]),
      )
    );
  }, [activeBookGroups, selectedMonitoredBookKeys, getMonitoredBookSelectionKey]);

  const toggleMonitoredAuthorSelection = useCallback((authorId: number) => {
    const key = String(authorId);
    setSelectedMonitoredAuthorKeys((prev) => ({
      ...prev,
      [key]: !prev[key],
    }));
  }, []);

  const runBulkUnmonitorSelected = useCallback(async () => {
    if (bulkUnmonitorRunning) return;

    const selectedRows = monitoredBooksRows.filter(
      (book) => selectedMonitoredBookKeys[getMonitoredBookSelectionKey(book)],
    );
    if (selectedRows.length === 0) return;

    setBulkUnmonitorRunning(true);
    setMonitoredBooksLoadError(null);
    try {
      const updatesByEntity = new Map<
        number,
        Array<{
          provider: string;
          provider_book_id: string;
          monitor_ebook: boolean;
          monitor_audiobook: boolean;
        }>
      >();
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
      setMonitoredBooksRows((prev) =>
        prev.map((book) =>
          selectedKeys.has(getMonitoredBookSelectionKey(book))
            ? { ...book, monitor_ebook: 0, monitor_audiobook: 0 }
            : book,
        ),
      );
      setSelectedMonitoredBookKeys({});

      if (hasFailure) {
        setMonitoredBooksLoadError(
          'Some books could not be unmonitored, but successful updates were applied.',
        );
      }
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to unmonitor selected books';
      setMonitoredBooksLoadError(message);
    } finally {
      setBulkUnmonitorRunning(false);
    }
  }, [
    bulkUnmonitorRunning,
    monitoredBooksRows,
    selectedMonitoredBookKeys,
    getMonitoredBookSelectionKey,
  ]);

  const monitoredBookToBook = useCallback(
    (row: MonitoredBookListRow): Book => ({
      id: String(row.id),
      title: row.title,
      author: row.authors || row.author_name || '',
      year: row.publish_year ? String(row.publish_year) : undefined,
      provider: row.provider || undefined,
      provider_id: row.provider_book_id || undefined,
      description: row.description || undefined,
      isbn_13: row.isbn_13 || undefined,
      preview: row.cover_url || undefined,
      language: row.language || undefined,
      release_date: row.release_date || undefined,
    }),
    [],
  );

  const selectedMonitoredBooks = useMemo(
    () =>
      monitoredBooksRows.filter(
        (book) => selectedMonitoredBookKeys[getMonitoredBookSelectionKey(book)],
      ),
    [monitoredBooksRows, selectedMonitoredBookKeys, getMonitoredBookSelectionKey],
  );

  const runBulkDownloadForMonitoredBooks = useCallback(
    async (contentType: ContentType) => {
      if (
        !onGetReleases ||
        selectedMonitoredBooks.length === 0 ||
        bulkBookDownloadRunning[contentType]
      )
        return;
      const batchId = `${contentType}:${Date.now()}`;
      const batchTotal = selectedMonitoredBooks.length;
      setBulkBookDownloadRunning((prev) => ({ ...prev, [contentType]: true }));
      try {
        for (let idx = 0; idx < selectedMonitoredBooks.length; idx += 1) {
          const row = selectedMonitoredBooks[idx];
          try {
            const book = monitoredBookToBook(row);
            await onGetReleases(book, contentType, row.author_entity_id, 'auto_search_download', {
              combined: false,
              suppressPerBookAutoSearchToasts: true,
              batchAutoDownload: { batchId, index: idx + 1, total: batchTotal, contentType },
            });
          } catch (err) {
            console.warn(`Bulk download failed for book ${row.title}:`, err);
          }
        }
      } finally {
        setBulkBookDownloadRunning((prev) => ({ ...prev, [contentType]: false }));
      }
    },
    [onGetReleases, selectedMonitoredBooks, bulkBookDownloadRunning, monitoredBookToBook],
  );

  const runBulkInteractiveSearchForMonitoredBooks = useCallback(
    async (contentType: ContentType) => {
      if (!onGetReleases || selectedMonitoredBooks.length === 0) return;
      for (const row of selectedMonitoredBooks) {
        const book = monitoredBookToBook(row);
        await onGetReleases(book, contentType, row.author_entity_id, 'interactive_search', {
          combined: false,
        });
      }
    },
    [onGetReleases, selectedMonitoredBooks, monitoredBookToBook],
  );

  const toggleSingleBookMonitor = useCallback(
    async (
      book: MonitoredBookListRow,
      type: 'ebook' | 'audiobook' | 'both',
      newValue?: boolean,
    ) => {
      const provider = (book.provider || '').trim();
      const providerBookId = (book.provider_book_id || '').trim();
      if (!provider || !providerBookId) return;

      const currentEbook = monitoredBookTracksEbook(book);
      const currentAudiobook = monitoredBookTracksAudiobook(book);

      const patch: {
        provider: string;
        provider_book_id: string;
        monitor_ebook?: boolean;
        monitor_audiobook?: boolean;
      } = {
        provider,
        provider_book_id: providerBookId,
      };

      if (type === 'ebook') {
        patch.monitor_ebook = newValue !== undefined ? newValue : !currentEbook;
      } else if (type === 'audiobook') {
        patch.monitor_audiobook = newValue !== undefined ? newValue : !currentAudiobook;
      } else {
        const targetValue = newValue !== undefined ? newValue : !(currentEbook && currentAudiobook);
        patch.monitor_ebook = targetValue;
        patch.monitor_audiobook = targetValue;
      }

      // Optimistic update
      setMonitoredBooksRows((prev) =>
        prev.map((r) =>
          r.provider === provider &&
          r.provider_book_id === providerBookId &&
          r.author_entity_id === book.author_entity_id
            ? {
                ...r,
                monitor_ebook:
                  patch.monitor_ebook !== undefined ? patch.monitor_ebook : r.monitor_ebook,
                monitor_audiobook:
                  patch.monitor_audiobook !== undefined
                    ? patch.monitor_audiobook
                    : r.monitor_audiobook,
              }
            : r,
        ),
      );

      try {
        await updateMonitoredBooksMonitorFlags(book.author_entity_id, patch);
      } catch (e) {
        // Revert on error
        setMonitoredBooksRows((prev) =>
          prev.map((r) =>
            r.provider === provider &&
            r.provider_book_id === providerBookId &&
            r.author_entity_id === book.author_entity_id
              ? { ...r, monitor_ebook: currentEbook, monitor_audiobook: currentAudiobook }
              : r,
          ),
        );
        console.error('Failed to update monitoring state:', e);
      }
    },
    [],
  );

  const toggleSingleBookHidden = useCallback(async (book: MonitoredBookListRow) => {
    const provider = (book.provider || '').trim();
    const providerBookId = (book.provider_book_id || '').trim();
    if (!provider || !providerBookId) return;
    const wasHidden = isEnabledMonitoredFlag(book.hidden);
    const newHidden = !wasHidden;
    const snapshot = {
      hidden: book.hidden,
      monitor_ebook: book.monitor_ebook,
      monitor_audiobook: book.monitor_audiobook,
      saved_monitor_ebook: book.saved_monitor_ebook,
      saved_monitor_audiobook: book.saved_monitor_audiobook,
    };
    const matchRow = (r: MonitoredBookListRow) =>
      r.provider === provider &&
      r.provider_book_id === providerBookId &&
      r.author_entity_id === book.author_entity_id;
    setMonitoredBooksRows((prev) =>
      prev.map((r) => {
        if (!matchRow(r)) return r;
        if (newHidden) {
          return {
            ...r,
            hidden: true,
            saved_monitor_ebook: isEnabledMonitoredFlag(r.monitor_ebook) ? 1 : 0,
            saved_monitor_audiobook: isEnabledMonitoredFlag(r.monitor_audiobook) ? 1 : 0,
            monitor_ebook: 0,
            monitor_audiobook: 0,
          };
        }
        return {
          ...r,
          hidden: false,
          monitor_ebook: r.saved_monitor_ebook != null ? r.saved_monitor_ebook : 1,
          monitor_audiobook: r.saved_monitor_audiobook != null ? r.saved_monitor_audiobook : 1,
          saved_monitor_ebook: null,
          saved_monitor_audiobook: null,
        };
      }),
    );
    try {
      const resp = await updateMonitoredBooksMonitorFlags(book.author_entity_id, {
        provider,
        provider_book_id: providerBookId,
        hidden: newHidden,
      });
      if (resp.results?.length) {
        const result = resp.results.find(
          (r) => r.provider === provider && r.provider_book_id === providerBookId,
        );
        if (result) {
          setMonitoredBooksRows((prev) =>
            prev.map((r) =>
              matchRow(r)
                ? {
                    ...r,
                    monitor_ebook: result.monitor_ebook,
                    monitor_audiobook: result.monitor_audiobook,
                  }
                : r,
            ),
          );
        }
      }
    } catch (e) {
      setMonitoredBooksRows((prev) => prev.map((r) => (matchRow(r) ? { ...r, ...snapshot } : r)));
      console.error('Failed to update hidden state:', e);
    }
  }, []);

  const bulkToggleMonitorForMonitoredBooks = useCallback(async () => {
    if (selectedMonitoredBooks.length === 0) return;
    await Promise.allSettled(
      selectedMonitoredBooks.map((book) => toggleSingleBookMonitor(book, 'both')),
    );
  }, [selectedMonitoredBooks, toggleSingleBookMonitor]);

  const bulkHideMonitoredBooks = useCallback(async () => {
    if (selectedMonitoredBooks.length === 0) return;
    await Promise.allSettled(selectedMonitoredBooks.map((book) => toggleSingleBookHidden(book)));
    setSelectedMonitoredBookKeys({});
  }, [selectedMonitoredBooks, toggleSingleBookHidden]);

  const runBulkDeleteSelectedAuthors = useCallback(async () => {
    if (bulkDeleteAuthorsRunning) return;

    const selectedAuthors = monitored.filter(
      (author) => selectedMonitoredAuthorKeys[String(author.id)],
    );
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
        setMonitoredBooksSources((prev) =>
          prev.filter((entity) => !successfulIdSet.has(entity.id)),
        );
        setMonitoredBooksRows((prev) =>
          prev.filter((book) => !successfulIdSet.has(book.author_entity_id)),
        );
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
        setMonitoredError(
          'Some authors could not be deleted, but successful deletions were applied.',
        );
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
    const selectedAuthors = monitored.filter(
      (author) => selectedMonitoredAuthorKeys[String(author.id)],
    );
    if (selectedAuthors.length === 0) return;
    setBulkSyncAuthorsRunning(true);
    try {
      await Promise.all(
        selectedAuthors.map((author) => syncMonitoredEntity(author.id).catch(() => null)),
      );
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
    if (!monitoredBooksSearchExpanded) {
      return;
    }

    const handlePointerDown = (event: Event) => {
      const target = event.target as Node | null;
      if (!target) return;
      if (monitoredBooksSearchRef.current && !monitoredBooksSearchRef.current.contains(target)) {
        setMonitoredBooksSearchOpen(false);
        setMonitoredBooksSearchExpanded(false);
      }
    };

    document.addEventListener('pointerdown', handlePointerDown, true);
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown, true);
    };
  }, [monitoredBooksSearchExpanded]);

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
      const panelWidth = Math.min(560, window.innerWidth * 0.92);
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
    setView('landing');
    try {
      if (searchScope === 'books') {
        const result = await searchMetadata(
          q,
          40,
          bookSearchSortValue,
          {},
          1,
          defaultReleaseContentType,
        );
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
      const message =
        e instanceof Error
          ? e.message
          : searchScope === 'books'
            ? 'Failed to search books'
            : 'Failed to search authors';
      setSearchError(message);
    } finally {
      setIsSearching(false);
    }
  }, [
    authorQuery,
    authorSearchSortValue,
    bookSearchSortValue,
    defaultReleaseContentType,
    searchScope,
  ]);

  useEffect(() => {
    // Only auto-rerun while the Search tab is showing. The header pill shares
    // this scope/query state from every tab; without the guard, flipping scope
    // (or typing) in the header would yank the user into the Search tab and
    // fire searches they never submitted.
    if (landingTab !== 'search') {
      return;
    }
    if (searchScope !== 'books') {
      return;
    }
    if (!normalizeAuthor(authorQuery)) {
      return;
    }
    void runAuthorSearch();
  }, [authorQuery, bookSearchSortValue, landingTab, runAuthorSearch, searchScope]);

  useEffect(() => {
    if (!monitoredSearchSortOptions.some((option) => option.value === bookSearchSortValue)) {
      setBookSearchSortValue(monitoredSearchSortOptions[0]?.value || 'relevance');
    }
  }, [bookSearchSortValue, monitoredSearchSortOptions]);

  useEffect(() => {
    // Same guard as the books-scope effect above: header-driven scope/query
    // changes must not auto-search from other tabs.
    if (landingTab !== 'search') {
      return;
    }
    if (searchScope !== 'authors') {
      return;
    }
    if (!normalizeAuthor(authorQuery)) {
      return;
    }
    void runAuthorSearch();
  }, [authorSearchSortValue, landingTab, runAuthorSearch, searchScope, authorQuery]);

  const deferredAuthorCleanupRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (deferredAuthorCleanupRef.current) clearTimeout(deferredAuthorCleanupRef.current);
    },
    [],
  );

  const openMonitoredTab = useCallback(
    (tab: 'authors' | 'books' | 'upcoming' | 'search' | 'history' | 'author-detail') => {
      if (tab !== 'author-detail' && activeAuthorDetail) {
        // Animate indicator to target tab first, then remove the author tab after animation
        setLandingTab(tab);
        if (deferredAuthorCleanupRef.current) clearTimeout(deferredAuthorCleanupRef.current);
        deferredAuthorCleanupRef.current = setTimeout(() => {
          setActiveAuthorDetail(null);
          setAuthorBooksControls(null);
          deferredAuthorCleanupRef.current = null;
        }, 320);
        if (location.pathname === '/monitored/author') {
          navigate('/monitored');
        }
      } else {
        setLandingTab(tab);
      }
      setView('landing');
    },
    [location.pathname, navigate, activeAuthorDetail],
  );

  const closeAuthorDetailTab = useCallback(() => {
    setLandingTab('authors');
    if (deferredAuthorCleanupRef.current) clearTimeout(deferredAuthorCleanupRef.current);
    deferredAuthorCleanupRef.current = setTimeout(() => {
      setActiveAuthorDetail(null);
      setAuthorBooksControls(null);
      deferredAuthorCleanupRef.current = null;
    }, 320);
    if (location.pathname === '/monitored/author') {
      navigate('/monitored');
    }
  }, [location.pathname, navigate]);

  const goNextLandingTab = useCallback(
    () =>
      setLandingTab((prev) => {
        const i = LANDING_TAB_ORDER.indexOf(prev as (typeof LANDING_TAB_ORDER)[number]);
        return i >= 0 && i < LANDING_TAB_ORDER.length - 1 ? LANDING_TAB_ORDER[i + 1] : prev;
      }),
    [],
  );
  const goPrevLandingTab = useCallback(
    () =>
      setLandingTab((prev) => {
        const i = LANDING_TAB_ORDER.indexOf(prev as (typeof LANDING_TAB_ORDER)[number]);
        return i > 0 ? LANDING_TAB_ORDER[i - 1] : prev;
      }),
    [],
  );
  const landingSwipeHandlers = useSwipe({
    onSwipeLeft: goNextLandingTab,
    onSwipeRight: goPrevLandingTab,
  });

  const closeBookMonitorModal = useCallback(() => {
    setBookMonitorModalState({ book: null });
  }, []);

  const openBookMonitorModal = useCallback((book: Book) => {
    setBookMonitorModalState({ book });
  }, []);

  // Wrap onGetReleases to inject combined flag from monitored settings.
  // Skip when: batch auto-downloads, or caller explicitly set combined to false.
  const onGetReleasesWithCombined = useCallback(
    (
      book: Book,
      ct: ContentType,
      entityId?: number | null,
      action?: ReleasePrimaryAction,
      opts?: OpenReleasesOptions,
    ) => {
      if (!onGetReleases) return Promise.resolve();
      const useCombined =
        releaseCombinedMode && !opts?.batchAutoDownload && opts?.combined !== false;
      return onGetReleases(
        book,
        ct,
        entityId,
        action,
        useCombined ? { ...opts, combined: true } : { ...opts, combined: opts?.combined ?? false },
      );
    },
    [onGetReleases, releaseCombinedMode],
  );

  const runBookResultInteractiveSearch = useCallback(
    (book: Book, contentType: ContentType) => {
      if (!onGetReleasesWithCombined) {
        return;
      }
      const actionOverride =
        contentType === 'ebook' ? defaultReleaseActionEbook : defaultReleaseActionAudiobook;
      void onGetReleasesWithCombined(book, contentType, null, actionOverride);
    },
    [defaultReleaseActionAudiobook, defaultReleaseActionEbook, onGetReleasesWithCombined],
  );

  const isBookSearchResultMonitored = useCallback(
    (book: Book): boolean => {
      const provider = (book.provider || '').trim().toLowerCase();
      const providerId = (book.provider_id || '').trim().toLowerCase();
      if (!provider || !providerId) return false;
      const key = `${provider}:${providerId}`;
      return monitoredSingleBookKeySet.has(key) || monitoredBooksKeySet.has(key);
    },
    [monitoredSingleBookKeySet, monitoredBooksKeySet],
  );

  const findMonitoredBookRow = useCallback(
    (book: Book): MonitoredBookListRow | undefined => {
      const provider = (book.provider || '').trim();
      const providerId = (book.provider_id || '').trim();
      if (!provider || !providerId) return undefined;
      return monitoredBooksRows.find(
        (r) => r.provider === provider && r.provider_book_id === providerId,
      );
    },
    [monitoredBooksRows],
  );

  const handleBookSearchResultMonitorAction = useCallback(
    (book: Book) => {
      const existingRow = findMonitoredBookRow(book);
      if (existingRow) {
        // Book is monitored - toggle to unmonitor both formats
        void toggleSingleBookMonitor(existingRow, 'both');
      } else {
        // Book is not monitored - open monitor modal
        openBookMonitorModal(book);
      }
    },
    [findMonitoredBookRow, toggleSingleBookMonitor, openBookMonitorModal],
  );

  const getMonitorResultButtonState = useCallback(
    (_bookId: string): ButtonStateInfo => ({
      text: 'Monitor',
      state: 'download',
    }),
    [],
  );

  const handleBookSearchResultDetails = useCallback(
    async (bookId: string) => {
      const selected = bookSearchResults.find((book) => book.id === bookId);
      if (!selected) {
        return;
      }
      runBookResultInteractiveSearch(selected, defaultReleaseContentType);
    },
    [bookSearchResults, defaultReleaseContentType, runBookResultInteractiveSearch],
  );

  const noopDownload = useCallback(async (_book: Book) => {
    return;
  }, []);

  const handleBookSearchResultGet = useCallback(
    async (book: Book) => {
      runBookResultInteractiveSearch(book, defaultReleaseContentType);
    },
    [defaultReleaseContentType, runBookResultInteractiveSearch],
  );

  const openMonitorModal = useCallback(
    (payload: {
      name: string;
      provider?: string;
      provider_id?: string;
      photo_url?: string;
      books_count?: number;
    }) => {
      const normalized = normalizeAuthor(payload.name);
      if (!normalized) return;

      // Folder prefill, path browsing and creation all live in <AuthorMonitorModal>.
      setMonitorAuthorTarget({ ...payload, name: normalized });
    },
    [],
  );

  const closeMonitorModal = useCallback(() => {
    setMonitorAuthorTarget(null);
  }, []);

  /**
   * Page-local bookkeeping after <AuthorMonitorModal> creates the entity.
   * (The create call, folder learning and error reporting live in the modal.)
   */
  const handleAuthorMonitored = useCallback(
    (created: MonitoredEntity) => {
      const payload = monitorAuthorTarget;
      const normalized = normalizeAuthor(payload?.name || created.name || '');
      const provider = created.provider || payload?.provider || undefined;
      const providerId = created.provider_id || payload?.provider_id || undefined;
      const photoUrl = payload?.photo_url ?? undefined;
      const booksCount = payload?.books_count ?? undefined;

      setMonitored((prev) => {
        const next = prev.filter((item) => item.id !== created.id);
        next.unshift({
          id: created.id,
          name: normalized,
          provider,
          provider_id: providerId,
          photo_url: photoUrl,
          books_count: booksCount,
        });
        return next;
      });
      setMonitoredBooksSources((prev) => {
        const next = prev.filter((entity) => entity.id !== created.id);
        next.unshift({
          id: created.id,
          kind: 'author',
          name: normalized,
          provider,
          provider_id: providerId,
          cached_source_url: created.cached_source_url || undefined,
          settings: created.settings,
        });
        return next;
      });
      // Keep search results visible so user can monitor more authors from the same results.
      // monitoredNames auto-updates from setMonitored above, flipping the button to "Monitored".
    },
    [monitorAuthorTarget],
  );

  const navigateToAuthorPage = useCallback(
    (payload: {
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
      if (
        typeof payload.monitoredEntityId === 'number' &&
        Number.isFinite(payload.monitoredEntityId)
      ) {
        params.set('entity_id', String(payload.monitoredEntityId));
      }

      const initialBookQuery = (payload.initialBookQuery || '').trim();
      const initialBookProvider = (payload.initialBookProvider || '').trim();
      const initialBookProviderId = (payload.initialBookProviderId || '').trim();
      if (initialBookQuery) params.set('initial_query', initialBookQuery);
      if (initialBookProvider) params.set('initial_provider', initialBookProvider);
      if (initialBookProviderId) params.set('initial_provider_id', initialBookProviderId);
      if (payload.initialContentType)
        params.set('initial_content_type', payload.initialContentType);
      if (payload.initialAction) params.set('initial_action', payload.initialAction);
      if (payload.openEdit) params.set('open_edit', '1');

      // Cancel any pending cleanup from a previous author close animation
      if (deferredAuthorCleanupRef.current) {
        clearTimeout(deferredAuthorCleanupRef.current);
        deferredAuthorCleanupRef.current = null;
      }

      // Set component state for the author-detail tab
      setActiveAuthorDetail({
        author: {
          name: normalized,
          provider: payload.provider || null,
          provider_id: payload.provider_id || null,
          source_url: payload.source_url || null,
          photo_url: payload.photo_url || null,
        },
        monitoredEntityId:
          typeof payload.monitoredEntityId === 'number' &&
          Number.isFinite(payload.monitoredEntityId)
            ? payload.monitoredEntityId
            : null,
        initialBooksQuery: initialBookQuery || undefined,
        initialBookProvider: initialBookProvider || null,
        initialBookProviderId: initialBookProviderId || null,
        openEdit: payload.openEdit,
      });
      setAuthorDetailBooksQuery(initialBookQuery);
      setLandingTab('author-detail');

      navigate(`/monitored/author?${params.toString()}`);
    },
    [navigate],
  );

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

  const handleMonitoredBookResultSelect = useCallback(
    (row: MonitoredAuthorBookSearchRow) => {
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
    },
    [monitored, navigateToAuthorPage],
  );

  const openMonitoredBookDetails = useCallback((book: MonitoredBookListRow) => {
    setActiveBookSourceRow(book);
    setActiveBookEntityId(book.author_entity_id);
  }, []);

  const openMonitoredBookInAuthorPage = useCallback(
    (
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
    },
    [navigateToAuthorPage],
  );

  const renderMonitoredBookActions = useCallback(
    (book: MonitoredBookListRow, compact = false) => {
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
            className="hover-surface block px-3 py-2 text-left text-sm whitespace-nowrap"
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
            className="hover-surface block px-3 py-2 text-left text-sm whitespace-nowrap"
          >
            Search eBooks
          </button>
          <button
            type="button"
            onClick={() => {
              close();
              openMonitoredBookInAuthorPage(book, 'audiobook', 'interactive_search');
            }}
            className="hover-surface block px-3 py-2 text-left text-sm whitespace-nowrap"
          >
            Search audiobooks
          </button>
          <div className="my-1 border-t border-[var(--border-muted)]" />
          <div className="px-3 py-1.5 text-[10px] tracking-wide text-gray-500 uppercase dark:text-gray-400">
            Monitoring
          </div>
          <button
            type="button"
            onClick={() => {
              void toggleSingleBookMonitor(book, 'both');
            }}
            className="hover-surface block px-3 py-2 text-left text-sm"
          >
            <span className="flex w-full items-center justify-between gap-6">
              <span>Monitor Both</span>
              {isFullyMonitored ? (
                <svg
                  className="h-4 w-4 text-emerald-500"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              ) : null}
            </span>
          </button>
          <button
            type="button"
            onClick={() => {
              void toggleSingleBookMonitor(book, 'ebook');
            }}
            className="hover-surface block px-3 py-2 text-left text-sm"
          >
            <span className="flex w-full items-center justify-between gap-6">
              <span>Monitor eBook</span>
              {tracksEbook ? (
                <svg
                  className="h-4 w-4 text-emerald-500"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              ) : null}
            </span>
          </button>
          <button
            type="button"
            onClick={() => {
              void toggleSingleBookMonitor(book, 'audiobook');
            }}
            className="hover-surface block px-3 py-2 text-left text-sm"
          >
            <span className="flex w-full items-center justify-between gap-6">
              <span>Monitor Audiobook</span>
              {tracksAudiobook ? (
                <svg
                  className="h-4 w-4 text-emerald-500"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                </svg>
              ) : null}
            </span>
          </button>
        </div>
      );

      if (compact) {
        return (
          <Dropdown
            widthClassName="w-auto"
            align="right"
            panelClassName="z-[2200] rounded-xl border border-[var(--border-muted)] shadow-2xl"
            noScrollLimit={true}
            usePortal={true}
            renderTrigger={({ isOpen, toggle }) => (
              <button
                type="button"
                onClick={toggle}
                className={`hover-action inline-flex h-6 w-6 items-center justify-center rounded-full text-gray-600 transition-colors dark:text-gray-200 ${isOpen ? 'text-gray-900 dark:text-gray-100' : ''}`}
                aria-label={`Book actions for ${book.title || 'this book'}`}
                title="Book actions"
              >
                <svg
                  className="h-4 w-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth={1.8}
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    d="M12 6.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM12 12.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5ZM12 18.75a.75.75 0 1 0 0-1.5.75.75 0 0 0 0 1.5Z"
                  />
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
            className="hover-action inline-flex h-8 w-8 items-center justify-center text-gray-600 dark:text-gray-200"
            aria-label={`Open default action for ${book.title || 'this book'}`}
            title="Open details"
          >
            <svg
              className="h-4 w-4"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={1.8}
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M4.5 6.75A2.25 2.25 0 0 1 6.75 4.5h4.5A2.25 2.25 0 0 1 13.5 6.75v12A2.25 2.25 0 0 0 11.25 16.5h-4.5A2.25 2.25 0 0 0 4.5 18.75v-12Zm9 0A2.25 2.25 0 0 1 15.75 4.5h1.5A2.25 2.25 0 0 1 19.5 6.75v12a2.25 2.25 0 0 0-2.25-2.25h-1.5A2.25 2.25 0 0 0 13.5 18.75v-12Z"
              />
            </svg>
          </button>

          <Dropdown
            widthClassName="w-auto"
            align="right"
            panelClassName="z-[2200] rounded-xl border border-[var(--border-muted)] shadow-2xl"
            noScrollLimit={true}
            usePortal={true}
            renderTrigger={({ isOpen, toggle }) => (
              <button
                type="button"
                onClick={toggle}
                className={`hover-action inline-flex h-8 w-7 items-center justify-center border-l border-[var(--border-muted)] text-gray-600 dark:text-gray-200 ${isOpen ? 'bg-black/5 dark:bg-white/10' : ''}`}
                aria-label={`More actions for ${book.title || 'this book'}`}
                title="More actions"
              >
                <svg
                  className="h-3.5 w-3.5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth={1.8}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 9l6 6 6-6" />
                </svg>
              </button>
            )}
          >
            {menuContent}
          </Dropdown>
        </div>
      );
    },
    [openMonitoredBookDetails, openMonitoredBookInAuthorPage, toggleSingleBookMonitor],
  );

  const isAuthorDetailsRoute = location.pathname === '/monitored/author';
  const authorDetailsSearchParams = useMemo(
    () => new URLSearchParams(location.search),
    [location.search],
  );

  const monitoredHeader = (
    <Header
      showSearch={false}
      logoUrl={logoUrl}
      onDownloadsClick={onActivityClick}
      isActivityOpen={isActivityOpen}
      onLogoClick={onBack}
      debug={debug}
      onMonitoredClick={() => navigate('/search')}
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
      showMobileSearchToggle={!isDesktop}
      mobileSearchOpen={false}
      onMobileSearchToggle={() => openMonitoredTab('search')}
      mobileSearchPlaceholder="Search authors..."
      headerExtra={
        isDesktop ? (
          <form
            className={`mr-2 hidden items-center rounded-full border transition-colors sm:flex ${
              headerScopeOpen ? 'border-emerald-500' : 'border-[var(--border-muted)]'
            }`}
            style={{ background: 'var(--surface)' }}
            onSubmit={(e) => {
              e.preventDefault();
              openMonitoredTab('search');
              void runAuthorSearch();
            }}
          >
            <SearchScopeDropdown
              compact
              scope={searchScope}
              onScopeChange={setSearchScope}
              onOpenChange={setHeaderScopeOpen}
            />
            <input
              type="text"
              value={authorQuery}
              onChange={(e) => setAuthorQuery(e.target.value)}
              placeholder={searchScope === 'authors' ? 'Search Authors...' : 'Search Books...'}
              className="w-32 bg-transparent px-3 py-1.5 text-sm text-[var(--text)] placeholder-gray-400 transition-all duration-200 focus:w-52 focus:outline-none"
              style={{ textAlign: 'left' }}
            />
            <button
              type="submit"
              className="shrink-0 rounded-r-full px-3 py-1.5 text-gray-400 transition-colors hover:text-emerald-600"
            >
              <svg
                className="h-4 w-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                strokeWidth={2}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="m21 21-4.35-4.35m1.35-5.15a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z"
                />
              </svg>
            </button>
          </form>
        ) : undefined
      }
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

  const authorDetailsInitialBooksQuery = (
    authorDetailsSearchParams.get('initial_query') || ''
  ).trim();
  const authorDetailsInitialBookProvider =
    (authorDetailsSearchParams.get('initial_provider') || '').trim() || undefined;
  const authorDetailsInitialBookProviderId =
    (authorDetailsSearchParams.get('initial_provider_id') || '').trim() || undefined;
  const authorDetailsInitialContentTypeParam = (
    authorDetailsSearchParams.get('initial_content_type') || ''
  ).trim();
  const authorDetailsInitialActionParam = (
    authorDetailsSearchParams.get('initial_action') || ''
  ).trim();
  const authorDetailsOpenEdit = authorDetailsSearchParams.get('open_edit') === '1';
  const authorDetailsInitialContentTypeOverride: ContentType | undefined =
    authorDetailsInitialContentTypeParam === 'audiobook'
      ? 'audiobook'
      : authorDetailsInitialContentTypeParam === 'ebook'
        ? 'ebook'
        : undefined;
  const authorDetailsInitialActionOverride: ReleasePrimaryAction | undefined =
    authorDetailsInitialActionParam === 'auto_search_download'
      ? 'auto_search_download'
      : authorDetailsInitialActionParam === 'interactive_search'
        ? 'interactive_search'
        : undefined;
  const authorDetailsEffectiveDefaultContentType =
    authorDetailsInitialContentTypeOverride ?? defaultReleaseContentType;
  const authorDetailsEffectiveDefaultActionEbook: ReleasePrimaryAction =
    authorDetailsEffectiveDefaultContentType === 'ebook' && authorDetailsInitialActionOverride
      ? authorDetailsInitialActionOverride
      : defaultReleaseActionEbook;
  const authorDetailsEffectiveDefaultActionAudiobook: ReleasePrimaryAction =
    authorDetailsEffectiveDefaultContentType === 'audiobook' && authorDetailsInitialActionOverride
      ? authorDetailsInitialActionOverride
      : defaultReleaseActionAudiobook;

  // Deep-link support: when page loads at /monitored/author, populate activeAuthorDetail from URL
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (isAuthorDetailsRoute && !activeAuthorDetail && authorDetailsAuthor) {
      setActiveAuthorDetail({
        author: authorDetailsAuthor,
        monitoredEntityId: authorDetailsMonitoredEntityId,
        initialBooksQuery: authorDetailsInitialBooksQuery || undefined,
        initialBookProvider: authorDetailsInitialBookProvider || null,
        initialBookProviderId: authorDetailsInitialBookProviderId || null,
        openEdit: authorDetailsOpenEdit,
      });
      setAuthorDetailBooksQuery(authorDetailsInitialBooksQuery);
      setLandingTab('author-detail');
    }
  }, [isAuthorDetailsRoute]); // intentionally narrow deps — only run on route entry

  // Handle browser back/forward: if URL leaves /monitored/author while on author-detail tab, clear state
  // Only react to isAuthorDetailsRoute changes (not landingTab) to avoid race with navigate()
  const prevIsAuthorDetailsRoute = useRef(isAuthorDetailsRoute);
  useEffect(() => {
    // Only trigger when route actually changes from author to non-author (browser back)
    if (
      prevIsAuthorDetailsRoute.current &&
      !isAuthorDetailsRoute &&
      landingTab === 'author-detail'
    ) {
      setActiveAuthorDetail(null);
      setAuthorBooksControls(null);
      setLandingTab('authors');
    }
    prevIsAuthorDetailsRoute.current = isAuthorDetailsRoute;
  }, [isAuthorDetailsRoute, landingTab]);

  return (
    <div
      className="min-h-screen overflow-x-clip"
      style={{ backgroundColor: 'var(--background-color)', color: 'var(--text-color)' }}
    >
      <div className="relative z-40 sm:fixed sm:top-0 sm:right-0 sm:left-0">{monitoredHeader}</div>

      <main
        className="relative mx-auto min-h-screen w-full max-w-7xl px-0 py-2 pt-0 sm:px-6 sm:py-2 sm:pt-20 lg:px-8"
        {...landingSwipeHandlers}
      >
        <div className="flex flex-col gap-2">
          {searchError || monitoredError ? (
            <div className="flex flex-col gap-3">
              {searchError && <div className="text-sm text-red-500">{searchError}</div>}

              {monitoredError && <div className="text-sm text-red-500">{monitoredError}</div>}
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

          {view === 'landing' ? (
            !monitoredLoaded && monitored.length === 0 ? (
              <div className="rounded-2xl bg-white/0 py-10 dark:bg-white/0">
                <div className="mx-auto max-w-md text-center">
                  <div className="inline-flex items-center gap-2 text-sm text-gray-500 dark:text-gray-400">
                    <span
                      className="inline-block h-2 w-2 animate-pulse rounded-full bg-emerald-500"
                      aria-hidden="true"
                    />
                    Loading monitored authors…
                  </div>
                </div>
              </div>
            ) : monitored.length === 0 && landingTab === 'authors' ? (
              <div className="rounded-2xl bg-white/0 py-10 dark:bg-white/0">
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
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                    No monitored authors
                  </div>
                  <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">
                    Search for an author above to start monitoring.
                  </div>
                </div>
              </div>
            ) : (
              <section className="flex max-h-none flex-col rounded-none border-0 border-black/10 bg-transparent sm:max-h-[calc(100dvh-8rem)] sm:overflow-hidden sm:rounded-2xl sm:border sm:bg-white/80 sm:shadow-xl dark:border-white/10 sm:dark:bg-white/5">
                <div className="relative sticky top-0 z-10 flex shrink-0 flex-wrap items-center gap-2 gap-y-1 border-b border-black/10 bg-[var(--background-color)] px-3 pt-2 pb-2 sm:static sm:gap-3 sm:gap-y-2 sm:bg-transparent sm:px-4 dark:border-white/10">
                  <div className="flex min-w-0 items-center gap-2">
                    <button
                      type="button"
                      onClick={() => {
                        if (onBack) {
                          onBack();
                          return;
                        }
                        navigate('/');
                      }}
                      className="hover-action hidden rounded-full p-1.5 text-gray-500 transition-colors hover:text-gray-900 sm:block dark:hover:text-gray-100"
                      aria-label="Back to home"
                      title="Back"
                    >
                      <svg
                        className="h-4 w-4"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                        strokeWidth={1.8}
                      >
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="M15 19.5 7.5 12 15 4.5"
                        />
                      </svg>
                    </button>
                    {/* Mobile: horizontal scrollable tab bar with sliding indicator */}
                    <div className="scrollbar-hide relative -mx-1 flex items-center gap-0 overflow-x-auto sm:hidden">
                      <div
                        ref={mobileTabIndicatorRef}
                        className="absolute bottom-0 h-0.5 rounded-full bg-emerald-600 transition-all duration-300 ease-out"
                      />
                      {(['authors', 'books', 'upcoming', 'search', 'history'] as const)
                        .filter((key) => !activeAuthorDetail || key === 'authors')
                        .map((key) => {
                          const label =
                            key === 'authors'
                              ? 'Authors'
                              : key === 'books'
                                ? 'Monitored'
                                : key === 'upcoming'
                                  ? 'Releases'
                                  : key === 'search'
                                    ? 'Search'
                                    : 'History';
                          const count =
                            key !== 'search' && key !== 'history'
                              ? key === 'authors'
                                ? displayAuthorsCount
                                : key === 'books'
                                  ? displayBooksCount
                                  : displayUpcomingCount
                              : null;
                          const isActive = landingTab === key;
                          return (
                            <button
                              key={key}
                              ref={(el) => {
                                mobileTabRefs.current[key] = el;
                                if (el && isActive) syncMobileTabIndicator();
                              }}
                              type="button"
                              onClick={() => openMonitoredTab(key)}
                              className={`relative px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors ${isActive ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-600 dark:text-gray-400'}`}
                              aria-pressed={isActive}
                            >
                              <span className="flex items-center gap-1.5">
                                {label}
                                {count != null && (
                                  <span
                                    className={`inline-flex min-w-[1.25rem] items-center justify-center rounded-full px-1.5 py-0.5 text-[10px] leading-none font-semibold ${isActive ? 'bg-emerald-600 text-white' : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-400'}`}
                                  >
                                    {count}
                                  </span>
                                )}
                              </span>
                            </button>
                          );
                        })}
                      {activeAuthorDetail && (
                        <div
                          key="author-detail"
                          ref={(el) => {
                            mobileTabRefs.current['author-detail'] = el;
                            if (el && landingTab === 'author-detail') syncMobileTabIndicator();
                          }}
                          className={`relative flex items-center gap-1.5 px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors ${landingTab === 'author-detail' ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-600 dark:text-gray-400'}`}
                          role="tab"
                          aria-selected={landingTab === 'author-detail'}
                          onClick={() => openMonitoredTab('author-detail')}
                        >
                          {activeAuthorDetail.author.name.length > 16
                            ? `${activeAuthorDetail.author.name.slice(0, 14)}…`
                            : activeAuthorDetail.author.name}
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              closeAuthorDetailTab();
                            }}
                            className="rounded-full p-0.5 hover:bg-black/10 dark:hover:bg-white/10"
                            aria-label="Close author tab"
                          >
                            <svg
                              className="h-3 w-3"
                              fill="none"
                              stroke="currentColor"
                              viewBox="0 0 24 24"
                              strokeWidth={2}
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="M6 18 18 6M6 6l12 12"
                              />
                            </svg>
                          </button>
                        </div>
                      )}
                    </div>
                    {/* Desktop: text tabs with sliding underline (matches mobile style) */}
                    <div className="relative hidden items-center gap-0 sm:flex">
                      <div
                        ref={desktopTabIndicatorRef}
                        className="absolute bottom-0 h-0.5 rounded-full bg-emerald-600 transition-all duration-300 ease-out"
                      />
                      {(['authors', 'books', 'upcoming', 'search', 'history'] as const)
                        .filter((key) => !activeAuthorDetail || key === 'authors')
                        .map((key) => {
                          const label =
                            key === 'authors'
                              ? 'Monitored Authors'
                              : key === 'books'
                                ? 'Monitored Books'
                                : key === 'upcoming'
                                  ? 'Releases'
                                  : key === 'search'
                                    ? 'Search'
                                    : 'History';
                          const count =
                            key !== 'search' && key !== 'history'
                              ? key === 'authors'
                                ? displayAuthorsCount
                                : key === 'books'
                                  ? displayBooksCount
                                  : displayUpcomingCount
                              : null;
                          const isActive = landingTab === key;
                          return (
                            <button
                              key={key}
                              ref={(el) => {
                                desktopTabRefs.current[key] = el;
                                if (el && isActive) syncDesktopTabIndicator();
                              }}
                              type="button"
                              onClick={() => openMonitoredTab(key)}
                              className={`relative px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors ${isActive ? 'text-emerald-600 dark:text-emerald-400' : 'hover-action text-gray-600 dark:text-gray-400'}`}
                              aria-pressed={isActive}
                            >
                              <span className="flex items-center gap-1.5">
                                {label}
                                {count != null && (
                                  <span
                                    className={`inline-flex min-w-[1.25rem] items-center justify-center rounded-full px-1.5 py-0.5 text-[10px] leading-none font-semibold ${isActive ? 'bg-emerald-600 text-white' : 'bg-emerald-100 text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-400'}`}
                                  >
                                    {count}
                                  </span>
                                )}
                              </span>
                            </button>
                          );
                        })}
                      {activeAuthorDetail && (
                        <div
                          key="author-detail"
                          ref={(el) => {
                            desktopTabRefs.current['author-detail'] = el;
                            if (el && landingTab === 'author-detail') syncDesktopTabIndicator();
                          }}
                          className={`relative flex cursor-pointer items-center gap-1 px-3 py-2 text-sm font-medium whitespace-nowrap transition-colors ${landingTab === 'author-detail' ? 'text-emerald-600 dark:text-emerald-400' : 'hover-action text-gray-600 dark:text-gray-400'}`}
                          role="tab"
                          aria-selected={landingTab === 'author-detail'}
                          onClick={() => openMonitoredTab('author-detail')}
                        >
                          {activeAuthorDetail.author.name.length > 20
                            ? `${activeAuthorDetail.author.name.slice(0, 18)}…`
                            : activeAuthorDetail.author.name}
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              closeAuthorDetailTab();
                            }}
                            className="rounded-full p-0.5 hover:bg-black/10 dark:hover:bg-white/10"
                            aria-label="Close author tab"
                          >
                            <svg
                              className="h-3 w-3"
                              fill="none"
                              stroke="currentColor"
                              viewBox="0 0 24 24"
                              strokeWidth={2}
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="M6 18 18 6M6 6l12 12"
                              />
                            </svg>
                          </button>
                        </div>
                      )}
                    </div>
                  </div>
                  <div
                    className={`ml-auto flex flex-wrap items-center justify-end gap-2 ${landingTab === 'search' || landingTab === 'history' ? 'hidden' : ''}`}
                  >
                    {landingTab === 'author-detail' && activeAuthorDetail ? (
                      <div className="flex shrink-0 items-center gap-1">
                        {/* Sync this author */}
                        {activeAuthorDetail.monitoredEntityId ? (
                          <button
                            type="button"
                            onClick={() => {
                              const eid = activeAuthorDetail.monitoredEntityId!;
                              setSyncingEntityId(eid);
                              syncMonitoredEntity(eid).catch(() =>
                                setSyncingEntityId((cur) => (cur === eid ? null : cur)),
                              );
                            }}
                            disabled={syncingEntityId === activeAuthorDetail.monitoredEntityId}
                            className="hover-action flex h-8 w-8 items-center justify-center rounded-full text-gray-600 disabled:opacity-50 dark:text-gray-300"
                            title="Sync this author"
                            aria-label="Sync this author"
                          >
                            <svg
                              className={`w-5 h-5${syncingEntityId === activeAuthorDetail.monitoredEntityId ? ' animate-spin' : ''}`}
                              fill="none"
                              stroke="currentColor"
                              viewBox="0 0 24 24"
                              strokeWidth="1.8"
                              aria-hidden="true"
                            >
                              <path
                                strokeLinecap="round"
                                strokeLinejoin="round"
                                d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99"
                              />
                            </svg>
                          </button>
                        ) : null}
                      </div>
                    ) : landingTab === 'authors' && monitored.length > 0 ? (
                      <div className="flex shrink-0 items-center gap-1">
                        {/* Sync All */}
                        <button
                          type="button"
                          onClick={runSyncAll}
                          disabled={syncAllRunning}
                          className="hover-action flex h-8 w-8 items-center justify-center rounded-full text-gray-600 disabled:opacity-50 dark:text-gray-300"
                          title="Sync all authors"
                          aria-label="Sync all authors"
                        >
                          <svg
                            className={`w-5 h-5${syncAllRunning ? ' animate-spin' : ''}`}
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                            strokeWidth="1.8"
                            aria-hidden="true"
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99"
                            />
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
                        className={`rounded-full p-2 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${monitoredBooksSearchQuery.trim() || (landingTab === 'author-detail' && authorDetailBooksQuery.trim()) || monitoredBooksSearchExpanded ? 'bg-emerald-600 text-white hover:bg-emerald-700' : 'hover-action text-gray-900 dark:text-gray-100'}`}
                        title="Search monitored books"
                        aria-label="Search monitored books"
                      >
                        <svg
                          className="h-5 w-5"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          strokeWidth="1.8"
                          aria-hidden="true"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="m21 21-4.35-4.35m1.35-5.15a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z"
                          />
                        </svg>
                      </button>
                      {monitoredBooksSearchExpanded ? (
                        <div
                          className="absolute top-full z-[120] mt-2"
                          style={{
                            width: `min(${window.innerWidth * 0.92}px, 560px)`,
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
                              borderTop: '2px solid rgb(16 185 129 / 0.45)',
                              borderLeft: '2px solid rgb(16 185 129 / 0.45)',
                            }}
                          />
                          <div
                            className="overflow-hidden rounded-xl border-2 border-emerald-500/40 shadow-2xl ring-1 ring-emerald-500/20"
                            style={{ background: 'var(--bg)' }}
                          >
                            <div className="flex items-center gap-2 border-b border-black/10 px-3 py-2 dark:border-white/10">
                              <svg
                                className="h-4 w-4 flex-shrink-0 text-gray-500"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                                strokeWidth="1.8"
                                aria-hidden="true"
                              >
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="m21 21-4.35-4.35m1.35-5.15a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z"
                                />
                              </svg>
                              <input
                                ref={monitoredBooksSearchInputRef}
                                value={
                                  landingTab === 'author-detail'
                                    ? authorDetailBooksQuery
                                    : monitoredBooksSearchQuery
                                }
                                onChange={(e) => {
                                  if (landingTab === 'author-detail') {
                                    setAuthorDetailBooksQuery(e.target.value);
                                  } else {
                                    setMonitoredBooksSearchQuery(e.target.value);
                                    if (landingTab === 'authors') {
                                      setMonitoredBooksSearchOpen(true);
                                    }
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
                                  if (landingTab === 'authors' && e.key === 'Enter') {
                                    const firstAuthor = groupedAuthorsTabSearchResults.authors[0];
                                    const firstBook = groupedAuthorsTabSearchResults.books[0];
                                    if (firstAuthor) {
                                      e.preventDefault();
                                      const matchingAuthor = monitored.find(
                                        (item) => item.id === firstAuthor.entityId,
                                      );
                                      navigateToAuthorPage({
                                        name: matchingAuthor?.name || firstAuthor.name,
                                        provider: matchingAuthor?.provider || firstAuthor.provider,
                                        provider_id:
                                          matchingAuthor?.provider_id || firstAuthor.providerId,
                                        source_url: matchingAuthor?.cached_source_url || null,
                                        photo_url:
                                          matchingAuthor?.photo_url || firstAuthor.photoUrl,
                                        monitoredEntityId:
                                          matchingAuthor?.id ?? firstAuthor.entityId,
                                      });
                                      setMonitoredBooksSearchQuery('');
                                      setMonitoredBooksSearchOpen(false);
                                      setMonitoredBooksSearchExpanded(false);
                                    } else if (firstBook) {
                                      e.preventDefault();
                                      handleMonitoredBookResultSelect(firstBook);
                                    }
                                  }
                                }}
                                placeholder={
                                  landingTab === 'author-detail'
                                    ? 'Filter books'
                                    : landingTab === 'authors'
                                      ? 'Search monitored books'
                                      : 'Filter visible books'
                                }
                                className="w-full bg-transparent text-sm text-gray-700 outline-none placeholder:text-gray-500 dark:text-gray-200"
                                aria-label="Search monitored books"
                                autoFocus
                              />
                              {(
                                landingTab === 'author-detail'
                                  ? authorDetailBooksQuery
                                  : monitoredBooksSearchQuery
                              ) ? (
                                <button
                                  type="button"
                                  onClick={() => {
                                    if (landingTab === 'author-detail') {
                                      setAuthorDetailBooksQuery('');
                                    } else {
                                      setMonitoredBooksSearchQuery('');
                                    }
                                    setMonitoredBooksSearchOpen(false);
                                  }}
                                  className="hover-action flex-shrink-0 rounded-full p-0.5 text-gray-500 hover:text-gray-900 dark:hover:text-gray-100"
                                  aria-label="Clear monitored books search"
                                  title="Clear"
                                >
                                  <svg
                                    className="h-4 w-4"
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                    strokeWidth={1.8}
                                  >
                                    <path
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                      d="M6 18 18 6M6 6l12 12"
                                    />
                                  </svg>
                                </button>
                              ) : null}
                            </div>

                            {landingTab === 'authors' &&
                            monitoredBooksSearchOpen &&
                            monitoredBooksSearchQuery.trim() ? (
                              <div
                                className="max-h-[480px] overflow-y-auto"
                                style={{ background: 'var(--bg-soft)' }}
                              >
                                {monitoredBooksSearchLoading ? (
                                  <div className="px-4 py-3 text-xs text-gray-500 dark:text-gray-400">
                                    Searching…
                                  </div>
                                ) : monitoredBooksSearchError ? (
                                  <div className="px-4 py-3 text-xs text-red-500">
                                    {monitoredBooksSearchError}
                                  </div>
                                ) : groupedAuthorsTabSearchResults.authors.length === 0 &&
                                  groupedAuthorsTabSearchResults.books.length === 0 ? (
                                  <div className="px-4 py-3 text-xs text-gray-500 dark:text-gray-400">
                                    No monitored database matches.
                                  </div>
                                ) : (
                                  <div>
                                    {groupedAuthorsTabSearchResults.authors.length > 0 ? (
                                      <>
                                        <div className="px-4 pt-3 pb-1.5 text-xs font-semibold tracking-[0.12em] text-gray-500 uppercase dark:text-gray-400">
                                          Authors
                                        </div>
                                        {groupedAuthorsTabSearchResults.authors.map((author) => {
                                          const initials = author.name
                                            .split(/\s+/)
                                            .filter(Boolean)
                                            .slice(0, 2)
                                            .map((p) => p[0]?.toUpperCase() || '')
                                            .join('');
                                          const stats = authorAvailabilityStats.get(
                                            author.entityId,
                                          );
                                          const totalBooks = stats?.booksTotal ?? 0;
                                          return (
                                            <button
                                              key={`author:${author.entityId}`}
                                              type="button"
                                              onClick={() => {
                                                const matchingAuthor = monitored.find(
                                                  (item) => item.id === author.entityId,
                                                );
                                                navigateToAuthorPage({
                                                  name: matchingAuthor?.name || author.name,
                                                  provider:
                                                    matchingAuthor?.provider || author.provider,
                                                  provider_id:
                                                    matchingAuthor?.provider_id ||
                                                    author.providerId,
                                                  source_url:
                                                    matchingAuthor?.cached_source_url || null,
                                                  photo_url:
                                                    matchingAuthor?.photo_url || author.photoUrl,
                                                  monitoredEntityId:
                                                    matchingAuthor?.id ?? author.entityId,
                                                });
                                                setMonitoredBooksSearchQuery('');
                                                setMonitoredBooksSearchOpen(false);
                                                setMonitoredBooksSearchExpanded(false);
                                              }}
                                              className="hover-surface flex w-full items-center gap-3 px-4 py-2.5 text-left"
                                            >
                                              <div className="flex h-12 w-12 flex-shrink-0 items-center justify-center overflow-hidden rounded-full bg-gray-300 text-gray-700 dark:bg-gray-700 dark:text-gray-200">
                                                {author.photoUrl ? (
                                                  <img
                                                    src={author.photoUrl}
                                                    alt=""
                                                    className="h-full w-full object-cover"
                                                  />
                                                ) : (
                                                  <span className="text-sm font-semibold">
                                                    {initials || '?'}
                                                  </span>
                                                )}
                                              </div>
                                              <div className="min-w-0 flex-1">
                                                <div className="truncate text-[15px] leading-tight font-semibold text-gray-900 dark:text-gray-100">
                                                  {author.name}
                                                </div>
                                                <div className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                                                  {totalBooks} {totalBooks === 1 ? 'book' : 'books'}
                                                </div>
                                              </div>
                                            </button>
                                          );
                                        })}
                                      </>
                                    ) : null}
                                    {groupedAuthorsTabSearchResults.books.length > 0 ? (
                                      <>
                                        <div className="px-4 pt-3 pb-1.5 text-xs font-semibold tracking-[0.12em] text-gray-500 uppercase dark:text-gray-400">
                                          Books
                                        </div>
                                        {groupedAuthorsTabSearchResults.books.map((row) => {
                                          const hasEbookAvailable = isEnabledMonitoredFlag(
                                            row.has_ebook_available,
                                          );
                                          const hasAudiobookAvailable = isEnabledMonitoredFlag(
                                            row.has_audiobook_available,
                                          );
                                          const hasSeries = Boolean(row.series_name);
                                          const seriesLabel = hasSeries
                                            ? `${row.series_name}${row.series_position != null ? ` #${row.series_position}` : ''}${row.series_count != null ? `/${row.series_count}` : ''}`
                                            : '';
                                          const subtitle = row.publish_year
                                            ? `${row.author_name} • ${row.publish_year}`
                                            : row.author_name;
                                          return (
                                            <button
                                              key={`book:${row.entity_id}:${row.book_provider || 'unknown'}:${row.book_provider_id || row.book_title}:${row.publish_year ?? 'na'}:${row.series_position ?? 'na'}`}
                                              type="button"
                                              onClick={() => handleMonitoredBookResultSelect(row)}
                                              className="hover-surface flex w-full items-center gap-3 px-4 py-2.5 text-left"
                                            >
                                              <div className="flex h-14 w-10 flex-shrink-0 items-center justify-center overflow-hidden rounded-md bg-gray-300 dark:bg-gray-700">
                                                {row.cover_url ? (
                                                  <img
                                                    src={row.cover_url}
                                                    alt=""
                                                    className="h-full w-full object-cover"
                                                  />
                                                ) : (
                                                  <svg
                                                    className="h-5 w-5 text-gray-500"
                                                    fill="none"
                                                    stroke="currentColor"
                                                    viewBox="0 0 24 24"
                                                    strokeWidth="1.5"
                                                    aria-hidden="true"
                                                  >
                                                    <path
                                                      strokeLinecap="round"
                                                      strokeLinejoin="round"
                                                      d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
                                                    />
                                                  </svg>
                                                )}
                                              </div>
                                              <div className="min-w-0 flex-1">
                                                <div className="truncate text-[15px] leading-tight font-semibold text-gray-900 dark:text-gray-100">
                                                  {row.book_title}
                                                </div>
                                                <div className="mt-0.5 truncate text-xs text-gray-500 dark:text-gray-400">
                                                  {subtitle}
                                                </div>
                                                {hasSeries ? (
                                                  <div
                                                    className="mt-0.5 truncate text-[11px] text-sky-600 dark:text-sky-400"
                                                    title={seriesLabel}
                                                  >
                                                    {seriesLabel}
                                                  </div>
                                                ) : null}
                                              </div>
                                              <div className="flex shrink-0 items-center gap-1">
                                                {hasEbookAvailable ? (
                                                  <span className="inline-flex items-center justify-center rounded-md bg-emerald-500/20 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-emerald-700 uppercase dark:text-emerald-300">
                                                    {(
                                                      row.ebook_available_format || 'ebook'
                                                    ).toUpperCase()}
                                                  </span>
                                                ) : null}
                                                {hasAudiobookAvailable ? (
                                                  <span className="inline-flex items-center justify-center rounded-md bg-violet-500/20 px-1.5 py-0.5 text-[10px] font-semibold tracking-wide text-violet-700 uppercase dark:text-violet-300">
                                                    {(
                                                      row.audiobook_available_format || 'audio'
                                                    ).toUpperCase()}
                                                  </span>
                                                ) : null}
                                              </div>
                                            </button>
                                          );
                                        })}
                                      </>
                                    ) : null}
                                  </div>
                                )}
                              </div>
                            ) : null}
                          </div>
                        </div>
                      ) : null}
                    </div>
                    {landingTab === 'author-detail' && authorBooksControls ? (
                      <>
                        <ViewModeToggle
                          value={authorBooksControls.booksViewMode}
                          onChange={(next) =>
                            authorBooksControls.setBooksViewMode(next as 'table' | 'compact')
                          }
                          options={[
                            {
                              value: 'table',
                              label: 'Table view',
                              icon: (
                                <svg
                                  className="h-4 w-4"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                  strokeWidth="1.8"
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    d="M4.5 6.75h15m-15 5.25h15m-15 5.25h15"
                                  />
                                </svg>
                              ),
                            },
                            {
                              value: 'compact',
                              label: 'Compact view',
                              icon: (
                                <svg
                                  className="h-4 w-4"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                  strokeWidth="1.8"
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    d="M4.5 4.5h6.75v6.75H4.5V4.5Zm8.25 0h6.75v6.75h-6.75V4.5ZM4.5 12.75h6.75v6.75H4.5v-6.75Zm8.25 0h6.75v6.75h-6.75v-6.75Z"
                                  />
                                </svg>
                              ),
                            },
                          ]}
                        />
                        <Dropdown
                          align="right"
                          widthClassName="w-auto"
                          panelClassName="z-[2200] min-w-[220px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
                          renderTrigger={({ isOpen, toggle }) => (
                            <button
                              type="button"
                              onClick={toggle}
                              className={`rounded-full p-2 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${isOpen ? 'bg-emerald-600 text-white hover:bg-emerald-700' : 'hover-action text-gray-900 dark:text-gray-100'}`}
                              title="Books view settings"
                              aria-label="Books view settings"
                              aria-expanded={isOpen}
                            >
                              <svg
                                className="h-5 w-5"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                                strokeWidth={1.8}
                              >
                                <circle cx="12" cy="5" r="1.5" />
                                <circle cx="12" cy="12" r="1.5" />
                                <circle cx="12" cy="19" r="1.5" />
                              </svg>
                            </button>
                          )}
                        >
                          {() => (
                            <div className="space-y-3 px-3 py-3">
                              <div>
                                <div className="mb-1 text-[10px] font-semibold tracking-wider text-gray-400 uppercase dark:text-gray-500">
                                  Group
                                </div>
                                <div className="space-y-0.5">
                                  {[
                                    { key: 'series' as const, label: 'By Series' },
                                    { key: 'year' as const, label: 'By Year' },
                                    { key: 'none' as const, label: 'None' },
                                  ].map(({ key, label }) => {
                                    const active = authorBooksControls.booksGroup === key;
                                    return (
                                      <button
                                        key={key}
                                        type="button"
                                        className={`hover-surface flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-sm ${active ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                        onClick={() => authorBooksControls.setBooksGroup(key)}
                                      >
                                        {label}
                                        {active && (
                                          <svg
                                            className="h-3.5 w-3.5"
                                            fill="none"
                                            stroke="currentColor"
                                            viewBox="0 0 24 24"
                                            strokeWidth={2}
                                          >
                                            <path
                                              strokeLinecap="round"
                                              strokeLinejoin="round"
                                              d="M5 13l4 4L19 7"
                                            />
                                          </svg>
                                        )}
                                      </button>
                                    );
                                  })}
                                </div>
                              </div>
                              <div>
                                <div className="mb-1 text-[10px] font-semibold tracking-wider text-gray-400 uppercase dark:text-gray-500">
                                  Sort
                                </div>
                                <div className="space-y-0.5">
                                  {[
                                    { key: 'series_name' as const, label: 'Series Name' },
                                    { key: 'series' as const, label: 'Series Number' },
                                    { key: 'title' as const, label: 'Title' },
                                    { key: 'date' as const, label: 'Date' },
                                    { key: 'popularity' as const, label: 'Popularity' },
                                    { key: 'rating' as const, label: 'Rating' },
                                  ].map(({ key, label }) => {
                                    const active = authorBooksControls.booksSort === key;
                                    return (
                                      <button
                                        key={key}
                                        type="button"
                                        className={`hover-surface flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-sm ${active ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                        onClick={() => {
                                          if (active) {
                                            authorBooksControls.setBooksSortAsc(
                                              (prev: boolean) => !prev,
                                            );
                                          } else {
                                            authorBooksControls.setBooksSort(key);
                                            authorBooksControls.setBooksSortAsc(
                                              key !== 'popularity' && key !== 'rating',
                                            );
                                          }
                                        }}
                                      >
                                        {label}
                                        {active && (
                                          <svg
                                            className={`h-3.5 w-3.5 shrink-0 transition-transform ${authorBooksControls.booksSortAsc ? '' : 'rotate-180'}`}
                                            fill="none"
                                            stroke="currentColor"
                                            viewBox="0 0 24 24"
                                            strokeWidth={2}
                                          >
                                            <path
                                              strokeLinecap="round"
                                              strokeLinejoin="round"
                                              d="M5 15l7-7 7 7"
                                            />
                                          </svg>
                                        )}
                                      </button>
                                    );
                                  })}
                                </div>
                              </div>
                            </div>
                          )}
                        </Dropdown>
                      </>
                    ) : landingTab === 'authors' ? (
                      <>
                        <ViewModeToggle
                          value={monitoredViewMode}
                          onChange={(next) => setMonitoredViewMode(next as 'compact' | 'table')}
                          options={[
                            {
                              value: 'table',
                              label: 'Table view',
                              icon: (
                                <svg
                                  className="h-4 w-4"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                  strokeWidth="1.8"
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    d="M4.5 6.75h15m-15 5.25h15m-15 5.25h15"
                                  />
                                </svg>
                              ),
                            },
                            {
                              value: 'compact',
                              label: 'Compact view',
                              icon: (
                                <svg
                                  className="h-4 w-4"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                  strokeWidth="1.8"
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    d="M4.5 4.5h6.75v6.75H4.5V4.5Zm8.25 0h6.75v6.75h-6.75V4.5ZM4.5 12.75h6.75v6.75H4.5v-6.75Zm8.25 0h6.75v6.75h-6.75v-6.75Z"
                                  />
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
                              className={`rounded-full p-2 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${isOpen ? 'bg-emerald-600 text-white hover:bg-emerald-700' : 'hover-action text-gray-900 dark:text-gray-100'}`}
                              title="Author view settings"
                              aria-label="Author view settings"
                              aria-expanded={isOpen}
                            >
                              <svg
                                className="h-5 w-5"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                                strokeWidth="1.8"
                              >
                                <circle cx="12" cy="5" r="1.5" />
                                <circle cx="12" cy="12" r="1.5" />
                                <circle cx="12" cy="19" r="1.5" />
                              </svg>
                            </button>
                          )}
                        >
                          {() => (
                            <div className="space-y-3 px-3 py-3">
                              <div>
                                <div className="mb-1 text-[11px] tracking-wide text-gray-500 uppercase dark:text-gray-400">
                                  Sort
                                </div>
                                <div
                                  className="space-y-1"
                                  role="listbox"
                                  aria-label="Sort monitored authors"
                                >
                                  {(
                                    [
                                      { key: 'alphabetical' as const, label: 'Name' },
                                      { key: 'date_added' as const, label: 'Date Added' },
                                      { key: 'books_count' as const, label: 'Number of Books' },
                                    ] as const
                                  ).map(({ key, label }) => {
                                    const active = monitoredSortBy === key;
                                    return (
                                      <button
                                        key={key}
                                        type="button"
                                        className={`hover-surface flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-sm ${active ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
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
                                          <svg
                                            className={`h-3.5 w-3.5 shrink-0 transition-transform ${monitoredSortAsc ? '' : 'rotate-180'}`}
                                            fill="none"
                                            stroke="currentColor"
                                            viewBox="0 0 24 24"
                                            strokeWidth={2}
                                          >
                                            <path
                                              strokeLinecap="round"
                                              strokeLinejoin="round"
                                              d="M5 15l7-7 7 7"
                                            />
                                          </svg>
                                        )}
                                      </button>
                                    );
                                  })}
                                </div>
                              </div>

                              <div>
                                <div className="mb-2 text-[11px] tracking-wide text-gray-500 uppercase dark:text-gray-400">
                                  Size
                                </div>
                                <input
                                  type="range"
                                  min={MONITORED_COMPACT_MIN_WIDTH_MIN}
                                  max={MONITORED_COMPACT_MIN_WIDTH_MAX}
                                  step={5}
                                  value={monitoredCompactMinWidth}
                                  onChange={(e) =>
                                    setMonitoredCompactMinWidth(Number(e.target.value))
                                  }
                                  className="w-full accent-emerald-600"
                                  aria-label="Compact card size"
                                  title="Compact card size"
                                  disabled={monitoredViewMode !== 'compact'}
                                />
                                <div className="mt-1 text-right text-[11px] text-gray-500 tabular-nums dark:text-gray-400">
                                  {monitoredCompactMinWidth}px
                                </div>
                                {monitoredViewMode !== 'compact' ? (
                                  <div className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
                                    Switch to compact view to adjust grid size.
                                  </div>
                                ) : null}
                              </div>
                            </div>
                          )}
                        </Dropdown>
                      </>
                    ) : (
                      <>
                        {/* Unmonitor button moved to floating selection bar */}
                        {landingTab === 'books' ? (
                          <div className="inline-flex items-center rounded-full border border-[var(--border-muted)] bg-transparent p-0.5">
                            <button
                              type="button"
                              onClick={() => setMonitoredBooksAvailabilityFilter('missing')}
                              className={`rounded-full px-2.5 py-1.5 text-[11px] font-medium transition-colors ${monitoredBooksAvailabilityFilter === 'missing' ? 'bg-emerald-600 text-white shadow-sm' : 'hover-action text-gray-700 dark:text-gray-200'}`}
                              aria-pressed={monitoredBooksAvailabilityFilter === 'missing'}
                              title="Show missing monitored books"
                            >
                              Missing
                            </button>
                            <button
                              type="button"
                              onClick={() => setMonitoredBooksAvailabilityFilter('fulfilled')}
                              className={`rounded-full px-2.5 py-1.5 text-[11px] font-medium transition-colors ${monitoredBooksAvailabilityFilter === 'fulfilled' ? 'bg-emerald-600 text-white shadow-sm' : 'hover-action text-gray-700 dark:text-gray-200'}`}
                              aria-pressed={monitoredBooksAvailabilityFilter === 'fulfilled'}
                              title="Show fulfilled monitored books"
                            >
                              Fulfilled
                            </button>
                          </div>
                        ) : landingTab === 'upcoming' ? (
                          <div className="inline-flex items-center rounded-full border border-[var(--border-muted)] bg-transparent p-0.5">
                            {(
                              [
                                ['all', 'All'],
                                ['recent', 'Recent'],
                                ['3months', 'Soon'],
                                ['this_year', 'This Year'],
                                ['tba', 'TBA'],
                              ] as const
                            ).map(([value, label]) => (
                              <button
                                key={value}
                                type="button"
                                onClick={() => setUpcomingTimeFilter(value)}
                                className={`rounded-full px-2.5 py-1.5 text-[11px] font-medium transition-colors ${upcomingTimeFilter === value ? 'bg-emerald-600 text-white shadow-sm' : 'hover-action text-gray-700 dark:text-gray-200'}`}
                                aria-pressed={upcomingTimeFilter === value}
                              >
                                {label}
                              </button>
                            ))}
                          </div>
                        ) : null}
                        <ViewModeToggle
                          value={monitoredBooksViewMode}
                          onChange={(next) =>
                            setMonitoredBooksViewMode(next as 'table' | 'compact')
                          }
                          options={[
                            {
                              value: 'table',
                              label: 'Table view',
                              icon: (
                                <svg
                                  className="h-4 w-4"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                  strokeWidth={1.8}
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    d="M4.5 6.75h15m-15 5.25h15m-15 5.25h15"
                                  />
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
                              className={`rounded-full p-2 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${isOpen ? 'bg-emerald-600 text-white hover:bg-emerald-700' : 'hover-action text-gray-900 dark:text-gray-100'}`}
                              title="Books view settings"
                              aria-label="Books view settings"
                              aria-expanded={isOpen}
                            >
                              <svg
                                className="h-5 w-5"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                                strokeWidth={1.8}
                              >
                                <circle cx="12" cy="5" r="1.5" />
                                <circle cx="12" cy="12" r="1.5" />
                                <circle cx="12" cy="19" r="1.5" />
                              </svg>
                            </button>
                          )}
                        >
                          {() => (
                            <div className="space-y-3 px-3 py-3">
                              <div>
                                <div className="mb-1 text-[11px] tracking-wide text-gray-500 uppercase dark:text-gray-400">
                                  Sort
                                </div>
                                <div
                                  className="space-y-1"
                                  role="listbox"
                                  aria-label="Sort monitored books"
                                >
                                  {(
                                    [
                                      { key: 'title' as const, label: 'Title' },
                                      { key: 'date' as const, label: 'Date' },
                                      { key: 'recently_added' as const, label: 'Recently Added' },
                                      { key: 'popularity' as const, label: 'Popularity' },
                                    ] as const
                                  ).map(({ key, label }) => {
                                    const active = monitoredBooksSortBy === key;
                                    return (
                                      <button
                                        key={key}
                                        type="button"
                                        className={`hover-surface flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-sm ${active ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
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
                                          <svg
                                            className={`h-3.5 w-3.5 shrink-0 transition-transform ${monitoredBooksSortAsc ? '' : 'rotate-180'}`}
                                            fill="none"
                                            stroke="currentColor"
                                            viewBox="0 0 24 24"
                                            strokeWidth={2}
                                          >
                                            <path
                                              strokeLinecap="round"
                                              strokeLinejoin="round"
                                              d="M5 15l7-7 7 7"
                                            />
                                          </svg>
                                        )}
                                      </button>
                                    );
                                  })}
                                </div>
                              </div>

                              <div>
                                <div className="mb-1 text-[11px] tracking-wide text-gray-500 uppercase dark:text-gray-400">
                                  Group
                                </div>
                                <div
                                  className="space-y-1"
                                  role="listbox"
                                  aria-label="Group monitored books"
                                >
                                  <button
                                    type="button"
                                    className={`hover-surface w-full rounded-lg px-2.5 py-1.5 text-left text-sm ${monitoredBooksGroupBy === 'none' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setMonitoredBooksGroupBy('none')}
                                    role="option"
                                    aria-selected={monitoredBooksGroupBy === 'none'}
                                  >
                                    No grouping
                                  </button>
                                  <button
                                    type="button"
                                    className={`hover-surface w-full rounded-lg px-2.5 py-1.5 text-left text-sm ${monitoredBooksGroupBy === 'author' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setMonitoredBooksGroupBy('author')}
                                    role="option"
                                    aria-selected={monitoredBooksGroupBy === 'author'}
                                  >
                                    Group by author
                                  </button>
                                  <button
                                    type="button"
                                    className={`hover-surface w-full rounded-lg px-2.5 py-1.5 text-left text-sm ${monitoredBooksGroupBy === 'year' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setMonitoredBooksGroupBy('year')}
                                    role="option"
                                    aria-selected={monitoredBooksGroupBy === 'year'}
                                  >
                                    Group by year
                                  </button>
                                </div>
                              </div>

                              <div>
                                <div className="mb-2 text-[11px] tracking-wide text-gray-500 uppercase dark:text-gray-400">
                                  Size
                                </div>
                                <input
                                  type="range"
                                  min={MONITORED_COMPACT_MIN_WIDTH_MIN}
                                  max={MONITORED_COMPACT_MIN_WIDTH_MAX}
                                  step={5}
                                  value={monitoredCompactMinWidth}
                                  onChange={(e) =>
                                    setMonitoredCompactMinWidth(Number(e.target.value))
                                  }
                                  className="w-full accent-emerald-600"
                                  aria-label="Books compact size"
                                  title="Books compact size"
                                  disabled={monitoredBooksViewMode !== 'compact'}
                                />
                                <div className="mt-1 text-right text-[11px] text-gray-500 tabular-nums dark:text-gray-400">
                                  {monitoredCompactMinWidth}px
                                </div>
                                {monitoredBooksViewMode !== 'compact' ? (
                                  <div className="mt-1 text-[11px] text-gray-500 dark:text-gray-400">
                                    Switch to compact view to adjust size.
                                  </div>
                                ) : null}
                              </div>

                              {landingTab === 'upcoming' ? (
                                <div>
                                  <div className="mb-1 text-[11px] tracking-wide text-gray-500 uppercase dark:text-gray-400">
                                    Display
                                  </div>
                                  <button
                                    type="button"
                                    className={`hover-surface flex w-full items-center justify-between rounded-lg px-2.5 py-1.5 text-left text-sm ${showUnmonitoredInReleases ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                    onClick={() => setShowUnmonitoredInReleases((v) => !v)}
                                    role="option"
                                    aria-selected={showUnmonitoredInReleases}
                                    title="Include books you've unmonitored (hidden books always excluded)"
                                  >
                                    <span>Show unmonitored books</span>
                                    {showUnmonitoredInReleases && (
                                      <svg
                                        className="h-3.5 w-3.5 shrink-0"
                                        fill="none"
                                        stroke="currentColor"
                                        viewBox="0 0 24 24"
                                        strokeWidth={2}
                                      >
                                        <path
                                          strokeLinecap="round"
                                          strokeLinejoin="round"
                                          d="M5 13l4 4L19 7"
                                        />
                                      </svg>
                                    )}
                                  </button>
                                </div>
                              ) : null}
                            </div>
                          )}
                        </Dropdown>
                      </>
                    )}
                  </div>
                  {/* History tab: date filter + overflow in header */}
                  {landingTab === 'history' ? (
                    <div className="ml-auto flex items-center gap-2">
                      <select
                        value={historyDateRange}
                        onChange={(e) => setHistoryDateRange(e.target.value)}
                        className="rounded-lg border border-[var(--border-muted)] bg-[var(--bg)] px-2 py-1.5 text-xs text-[var(--text)]"
                      >
                        <option value="">All time</option>
                        <option value="today">Today</option>
                        <option value="7d">7 days</option>
                        <option value="30d">30 days</option>
                      </select>
                      <Dropdown
                        align="right"
                        widthClassName="w-auto"
                        panelClassName="z-[2200] min-w-[180px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
                        renderTrigger={({ isOpen, toggle }) => (
                          <button
                            type="button"
                            onClick={toggle}
                            className={`rounded-full p-2 transition-all duration-200 focus:outline-none focus-visible:ring-2 focus-visible:ring-emerald-500/50 ${isOpen ? 'bg-emerald-600 text-white hover:bg-emerald-700' : 'hover-action text-gray-900 dark:text-gray-100'}`}
                            title="History options"
                            aria-label="History options"
                            aria-expanded={isOpen}
                          >
                            <svg
                              className="h-5 w-5"
                              fill="none"
                              stroke="currentColor"
                              viewBox="0 0 24 24"
                              strokeWidth={1.8}
                            >
                              <circle cx="12" cy="5" r="1.5" />
                              <circle cx="12" cy="12" r="1.5" />
                              <circle cx="12" cy="19" r="1.5" />
                            </svg>
                          </button>
                        )}
                      >
                        {({ close }) => (
                          <div className="space-y-1 px-3 py-3">
                            <button
                              type="button"
                              className="hover-surface flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm"
                              onClick={() => {
                                historyExportRef.current?.();
                                close();
                              }}
                            >
                              <svg
                                className="h-4 w-4"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                                strokeWidth={1.5}
                              >
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3"
                                />
                              </svg>
                              Export CSV
                            </button>
                            <button
                              type="button"
                              className="hover-surface flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm text-red-600 dark:text-red-400"
                              onClick={() => {
                                historyClearRef.current?.();
                                close();
                              }}
                            >
                              <svg
                                className="h-4 w-4"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                                strokeWidth={1.5}
                              >
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
                                />
                              </svg>
                              Clear History
                            </button>
                          </div>
                        )}
                      </Dropdown>
                    </div>
                  ) : null}
                </div>

                <div
                  className={`min-h-0 flex-1 overflow-visible px-4 pt-3 pb-4 sm:overflow-y-auto ${swipeDirection === 'left' ? 'animate-tab-slide-right' : swipeDirection === 'right' ? 'animate-tab-slide-left' : ''}`}
                >
                  {landingTab === 'author-detail' && activeAuthorDetail ? (
                    <AuthorModal
                      author={activeAuthorDetail.author}
                      displayMode="page"
                      hideHeader
                      onBooksControlsReady={setAuthorBooksControls}
                      onClose={closeAuthorDetailTab}
                      onGetReleases={onGetReleasesWithCombined}
                      defaultReleaseContentType={authorDetailsEffectiveDefaultContentType}
                      defaultReleaseActionEbook={authorDetailsEffectiveDefaultActionEbook}
                      defaultReleaseActionAudiobook={authorDetailsEffectiveDefaultActionAudiobook}
                      releaseCombinedMode={releaseCombinedMode}
                      booksSearchQuery={authorDetailBooksQuery}
                      onBooksSearchQueryChange={setAuthorDetailBooksQuery}
                      initialBooksQuery={activeAuthorDetail.initialBooksQuery}
                      initialBookProvider={activeAuthorDetail.initialBookProvider}
                      initialBookProviderId={activeAuthorDetail.initialBookProviderId}
                      monitoredEntityId={activeAuthorDetail.monitoredEntityId}
                      status={status}
                      openEditOnMount={activeAuthorDetail.openEdit}
                      renderEmbeddedSearch={renderEmbeddedSearch}
                      showBooksInMultipleSeries={showBooksInMultipleSeries}
                      onMonitorBook={openBookMonitorModal}
                    />
                  ) : landingTab === 'search' ? (
                    <>
                      {/* Inline search bar with scope dropdown */}
                      <form
                        className="mb-3 flex items-center gap-2"
                        onSubmit={(e) => {
                          e.preventDefault();
                          void runAuthorSearch();
                        }}
                      >
                        <div className="flex min-w-0 flex-1 items-center rounded-full border border-[var(--border-muted)] bg-[var(--bg)] focus-within:ring-2 focus-within:ring-emerald-500/50">
                          <Dropdown
                            align="left"
                            widthClassName=""
                            usePortal
                            panelClassName="min-w-[140px]"
                            renderTrigger={({ isOpen, toggle }) => (
                              <button
                                type="button"
                                onClick={toggle}
                                className="flex shrink-0 items-center gap-1 border-r border-[var(--border-muted)] py-2.5 pr-2 pl-3 text-gray-500 transition-colors hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200"
                                aria-expanded={isOpen}
                                aria-label="Search scope"
                              >
                                {searchScope === 'authors' ? (
                                  <svg
                                    className="h-5 w-5"
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                    strokeWidth={1.5}
                                  >
                                    <path
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                      d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z"
                                    />
                                  </svg>
                                ) : (
                                  <svg
                                    className="h-5 w-5"
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                    strokeWidth={1.5}
                                  >
                                    <path
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                      d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
                                    />
                                  </svg>
                                )}
                                <svg
                                  className="h-3 w-3"
                                  fill="none"
                                  stroke="currentColor"
                                  viewBox="0 0 24 24"
                                  strokeWidth={2}
                                >
                                  <path
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    d="m19.5 8.25-7.5 7.5-7.5-7.5"
                                  />
                                </svg>
                              </button>
                            )}
                          >
                            {({ close }) => (
                              <div className="py-1">
                                <button
                                  type="button"
                                  onClick={() => {
                                    setSearchScope('authors');
                                    close();
                                  }}
                                  className={`hover-surface flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${searchScope === 'authors' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                >
                                  <svg
                                    className="h-4 w-4"
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                    strokeWidth={1.5}
                                  >
                                    <path
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                      d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z"
                                    />
                                  </svg>
                                  Authors
                                </button>
                                <button
                                  type="button"
                                  onClick={() => {
                                    setSearchScope('books');
                                    close();
                                  }}
                                  className={`hover-surface flex w-full items-center gap-2 px-3 py-2 text-left text-sm ${searchScope === 'books' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                                >
                                  <svg
                                    className="h-4 w-4"
                                    fill="none"
                                    stroke="currentColor"
                                    viewBox="0 0 24 24"
                                    strokeWidth={1.5}
                                  >
                                    <path
                                      strokeLinecap="round"
                                      strokeLinejoin="round"
                                      d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
                                    />
                                  </svg>
                                  Books
                                </button>
                              </div>
                            )}
                          </Dropdown>
                          <input
                            type="text"
                            value={authorQuery}
                            onChange={(e) => setAuthorQuery(e.target.value)}
                            placeholder={
                              searchScope === 'authors' ? 'Search Authors' : 'Search Books'
                            }
                            className="min-w-0 flex-1 bg-transparent px-3 py-2.5 text-sm text-[var(--text)] placeholder-gray-400 focus:outline-none"
                            style={{ textAlign: 'left' }}
                            autoFocus
                          />
                        </div>
                        <button
                          type="submit"
                          className="shrink-0 rounded-full bg-emerald-600 p-3 text-white transition-colors hover:bg-emerald-700"
                          aria-label="Search"
                        >
                          <svg
                            className="h-5 w-5"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                            strokeWidth={2}
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="m21 21-4.35-4.35m1.35-5.15a7 7 0 1 1-14 0 7 7 0 0 1 14 0Z"
                            />
                          </svg>
                        </button>
                      </form>
                      {authorQuery.trim() ? (
                        <div className="mb-3 flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => setSearchScope('authors')}
                            aria-pressed={searchScope === 'authors'}
                            className={`rounded-full px-4 py-1.5 text-sm font-semibold transition-colors ${
                              searchScope === 'authors'
                                ? 'bg-emerald-600 text-white shadow-sm'
                                : 'hover-action border border-[var(--border-muted)] text-gray-700 dark:text-gray-200'
                            }`}
                          >
                            Authors
                            {authorCards.length || authorResults.length
                              ? ` (${authorCards.length || authorResults.length})`
                              : ''}
                          </button>
                          <button
                            type="button"
                            onClick={() => setSearchScope('books')}
                            aria-pressed={searchScope === 'books'}
                            className={`rounded-full px-4 py-1.5 text-sm font-semibold transition-colors ${
                              searchScope === 'books'
                                ? 'bg-emerald-600 text-white shadow-sm'
                                : 'hover-action border border-[var(--border-muted)] text-gray-700 dark:text-gray-200'
                            }`}
                          >
                            Books{bookSearchResults.length ? ` (${bookSearchResults.length})` : ''}
                          </button>
                        </div>
                      ) : null}
                      <MonitoredSearchView
                        hideHeader
                        searchScope={searchScope}
                        authorViewMode={authorViewMode}
                        bookSearchViewMode={bookSearchViewMode}
                        authorSearchViewOptions={authorSearchViewOptions}
                        bookSearchViewOptions={bookSearchViewOptions}
                        onAuthorViewModeChange={(next) =>
                          setAuthorViewMode(next as 'compact' | 'list')
                        }
                        onBookSearchViewModeChange={(next) =>
                          setBookSearchViewMode(next as 'compact' | 'list')
                        }
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
                        onBack={() => openMonitoredTab('authors')}
                        displayAuthorsCount={displayAuthorsCount}
                        displayBooksCount={displayBooksCount}
                        displayUpcomingCount={displayUpcomingCount}
                        displaySearchCount={displaySearchCount}
                      />
                    </>
                  ) : landingTab === 'history' ? (
                    <MonitoredHistoryTab
                      onShowToast={onShowToast}
                      exportRef={historyExportRef}
                      clearRef={historyClearRef}
                      dateRange={historyDateRange}
                      onNavigate={navigateToAuthorPage}
                    />
                  ) : landingTab === 'authors' ? (
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
                  ) : isUpcomingTab &&
                    (upcomingTimeFilter === 'all' || upcomingTimeFilter === 'recent') &&
                    filteredRecentlyReleasedBooksForTable.length > 0 ? (
                    <div className="flex flex-col gap-6">
                      {/* Recently Released section */}
                      <div className="flex flex-col gap-2">
                        {upcomingTimeFilter === 'all' && (
                          <h3 className="text-xs font-semibold tracking-wide text-blue-600 uppercase dark:text-blue-400">
                            Recently Released
                          </h3>
                        )}
                        <MonitoredBooksView
                          isLoading={monitoredBooksLoading}
                          isUpcomingTab
                          dateMode="recent"
                          activeBooksCount={filteredRecentlyReleasedBooksForTable.length}
                          viewMode={monitoredBooksViewMode}
                          bookGroups={recentlyReleasedBookGroups}
                          groupBy={monitoredBooksGroupBy}
                          selectedBookKeys={selectedMonitoredBookKeys}
                          booksGridStyle={monitoredBooksGridStyle}
                          compactMinWidth={monitoredCompactMinWidth}
                          loadError={monitoredBooksLoadError}
                          showLoadError={false}
                          onOpenDetails={openMonitoredBookDetails}
                          onToggleSelect={toggleMonitoredBookSelection}
                          getSelectionKey={getMonitoredBookSelectionKey}
                          renderBookActions={renderMonitoredBookActions}
                        />
                      </div>
                      {/* Upcoming section (only when showing all) */}
                      {upcomingTimeFilter === 'all' &&
                        filteredUpcomingMonitoredBooksForTable.length > 0 && (
                          <div className="flex flex-col gap-2">
                            <h3 className="text-xs font-semibold tracking-wide text-amber-600 uppercase dark:text-amber-400">
                              Upcoming
                            </h3>
                            <MonitoredBooksView
                              isLoading={monitoredBooksLoading}
                              isUpcomingTab
                              activeBooksCount={filteredUpcomingMonitoredBooksForTable.length}
                              viewMode={monitoredBooksViewMode}
                              bookGroups={upcomingBookGroups}
                              groupBy={monitoredBooksGroupBy}
                              selectedBookKeys={selectedMonitoredBookKeys}
                              booksGridStyle={monitoredBooksGridStyle}
                              compactMinWidth={monitoredCompactMinWidth}
                              loadError={monitoredBooksLoadError}
                              showLoadError
                              onOpenDetails={openMonitoredBookDetails}
                              onToggleSelect={toggleMonitoredBookSelection}
                              getSelectionKey={getMonitoredBookSelectionKey}
                              renderBookActions={renderMonitoredBookActions}
                            />
                          </div>
                        )}
                    </div>
                  ) : (
                    <MonitoredBooksView
                      isLoading={monitoredBooksLoading}
                      isUpcomingTab={isUpcomingTab}
                      dateMode={showOnlyRecent ? 'recent' : 'upcoming'}
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
          ) : null}
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
            className="details-container settings-modal-enter h-auto w-full max-w-md"
            role="dialog"
            aria-modal="true"
            aria-label="Confirm delete monitored authors"
          >
            <div className="overflow-hidden rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] text-[var(--text)] shadow-2xl">
              <header className="flex items-start justify-between gap-3 border-b border-[var(--border-muted)] px-5 py-4">
                <div className="min-w-0">
                  <div className="text-base font-semibold">
                    Delete monitored {selectedMonitoredAuthorCount === 1 ? 'author' : 'authors'}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => setBulkDeleteAuthorsConfirmOpen(false)}
                  disabled={bulkDeleteAuthorsRunning}
                  className="hover-action rounded-full p-2 text-gray-500 transition-colors hover:text-gray-900 disabled:opacity-50 dark:hover:text-gray-100"
                  aria-label="Close"
                >
                  <svg
                    className="h-5 w-5"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={1.5}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </header>

              <div className="space-y-3 px-5 py-4">
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
                  className="hover-action rounded-full px-3 py-1.5 text-sm font-medium disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  onClick={() => void runBulkDeleteSelectedAuthors()}
                  disabled={bulkDeleteAuthorsRunning}
                  className="rounded-full bg-red-600 px-3 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-50"
                >
                  {bulkDeleteAuthorsRunning ? 'Deleting…' : 'Delete'}
                </button>
              </footer>
            </div>
          </div>
        </div>
      ) : null}

      <AuthorMonitorModal
        author={monitorAuthorTarget}
        authRequired={authRequired}
        onClose={closeMonitorModal}
        onMonitored={handleAuthorMonitored}
      />

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
        renderEmbeddedSearch={(book, contentType, mEntityId) => {
          if (renderEmbeddedSearch) {
            return renderEmbeddedSearch(book, contentType, mEntityId);
          }
          return (
            <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] p-4 sm:bg-[var(--bg-soft)]">
              <div className="text-sm text-gray-600 dark:text-gray-300">
                Embedded search is unavailable.
              </div>
            </div>
          );
        }}
        onToggleMonitor={
          activeBookMonitorState.row
            ? (type) => void toggleSingleBookMonitor(activeBookMonitorState.row!, type)
            : undefined
        }
        hidden={
          activeBookMonitorState.row
            ? isEnabledMonitoredFlag(activeBookMonitorState.row.hidden)
            : false
        }
        onToggleHidden={
          activeBookMonitorState.row
            ? () => void toggleSingleBookHidden(activeBookMonitorState.row!)
            : undefined
        }
        onBookModified={() => setMonitoredBooksReloadTick((t) => t + 1)}
        onAuthorClick={(authorName) => {
          const entity = monitored.find((e) => e.id === activeBookEntityId);
          navigateToAuthorPage({
            name: authorName,
            provider: entity?.provider || null,
            provider_id: entity?.provider_id || null,
            photo_url: entity?.photo_url || null,
            source_url: entity?.cached_source_url || null,
            monitoredEntityId: entity?.id ?? null,
          });
        }}
        onSetReleaseDate={
          activeBookEntityId != null && activeBookSourceRow
            ? (_row) => {
                setActiveBookEntityId(null);
                setActiveBookSourceRow(null);
                setReleaseDateBook({ row: activeBookSourceRow, entityId: activeBookEntityId });
              }
            : undefined
        }
      />

      {releaseDateBook && (
        <ReleaseDateSearchModal
          book={releaseDateBook.row}
          entityId={releaseDateBook.entityId}
          onClose={() => setReleaseDateBook(null)}
          onMatched={(releaseDate) => {
            setMonitoredBooksRows((prev) =>
              prev.map((r) =>
                r.provider === releaseDateBook.row.provider &&
                r.provider_book_id === releaseDateBook.row.provider_book_id &&
                r.entity_id === releaseDateBook.row.entity_id
                  ? {
                      ...r,
                      release_date: releaseDate,
                      publish_year: releaseDate
                        ? parseInt(releaseDate.slice(0, 4), 10)
                        : r.publish_year,
                    }
                  : r,
              ),
            );
          }}
        />
      )}

      <EditAuthorModal
        open={editAuthorModalState.open}
        entityId={editAuthorModalState.entityId}
        authorName={editAuthorModalState.authorName}
        onClose={closeEditAuthorModal}
        onDeleted={handleEditAuthorDeleted}
        onSaved={handleEditAuthorSaved}
      />

      {/* ── Floating selection bars ── */}
      {landingTab === 'authors' && hasActiveMonitoredAuthorSelection ? (
        <FloatingSelectionBar
          count={selectedMonitoredAuthorCount}
          actions={
            [
              {
                key: 'sync',
                icon: (
                  <svg
                    className={`w-4 h-4${bulkSyncAuthorsRunning ? ' animate-spin' : ''}`}
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={1.8}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99"
                    />
                  </svg>
                ),
                title: `Refresh selected (${selectedMonitoredAuthorCount})`,
                onClick: () => void runBulkSyncSelectedAuthors(),
                disabled: bulkSyncAuthorsRunning,
                borderColor: 'teal' as const,
              },
              {
                key: 'delete',
                icon: (
                  <svg
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={1.8}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
                    />
                  </svg>
                ),
                title: `Delete selected (${selectedMonitoredAuthorCount})`,
                onClick: () => setBulkDeleteAuthorsConfirmOpen(true),
                borderColor: 'red' as const,
                dividerBefore: true,
              },
            ] satisfies FloatingSelectionBarAction[]
          }
          onSelectAll={
            allMonitoredAuthorsSelected ? clearMonitoredAuthorSelection : selectAllMonitoredAuthors
          }
          allSelected={allMonitoredAuthorsSelected}
          onDeselectAll={clearMonitoredAuthorSelection}
        />
      ) : null}

      {(landingTab === 'books' || landingTab === 'upcoming') && selectedMonitoredBookCount > 0 ? (
        <FloatingSelectionBar
          count={selectedMonitoredBookCount}
          actions={
            [
              ...(onGetReleases
                ? [
                    {
                      key: 'dl-ebook',
                      icon: (
                        <svg
                          className="h-4 w-4"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          strokeWidth={1.5}
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
                          />
                        </svg>
                      ),
                      title: 'eBooks',
                      onClick: () => void runBulkDownloadForMonitoredBooks('ebook'),
                      disabled: bulkBookDownloadRunning.ebook,
                      borderColor: 'teal' as const,
                      menuItems: [
                        {
                          label: 'Auto download eBooks',
                          onClick: () => void runBulkDownloadForMonitoredBooks('ebook'),
                        },
                        {
                          label: 'Interactive search eBooks',
                          onClick: () => void runBulkInteractiveSearchForMonitoredBooks('ebook'),
                        },
                      ],
                    },
                    {
                      key: 'dl-audiobook',
                      icon: (
                        <svg
                          className="h-4 w-4"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                          strokeWidth={1.5}
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z"
                          />
                        </svg>
                      ),
                      title: 'Audiobooks',
                      onClick: () => void runBulkDownloadForMonitoredBooks('audiobook'),
                      disabled: bulkBookDownloadRunning.audiobook,
                      borderColor: 'teal' as const,
                      menuItems: [
                        {
                          label: 'Auto download audiobooks',
                          onClick: () => void runBulkDownloadForMonitoredBooks('audiobook'),
                        },
                        {
                          label: 'Interactive search audiobooks',
                          onClick: () =>
                            void runBulkInteractiveSearchForMonitoredBooks('audiobook'),
                        },
                      ],
                    },
                  ]
                : []),
              {
                key: 'monitor',
                icon: (
                  <svg
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={1.8}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M17.593 3.322c1.1.128 1.907 1.077 1.907 2.185V21L12 17.25 4.5 21V5.507c0-1.108.806-2.057 1.907-2.185a48.507 48.507 0 0 1 11.186 0Z"
                    />
                  </svg>
                ),
                title: 'Monitoring',
                onClick: () => void bulkToggleMonitorForMonitoredBooks(),
                borderColor: 'teal' as const,
                menuItems: [
                  {
                    label: 'Monitor eBook',
                    onClick: () =>
                      void Promise.allSettled(
                        selectedMonitoredBooks.map((b) =>
                          toggleSingleBookMonitor(b, 'ebook', true),
                        ),
                      ),
                  },
                  {
                    label: 'Monitor audiobook',
                    onClick: () =>
                      void Promise.allSettled(
                        selectedMonitoredBooks.map((b) =>
                          toggleSingleBookMonitor(b, 'audiobook', true),
                        ),
                      ),
                  },
                  {
                    label: 'Unmonitor eBook',
                    onClick: () =>
                      void Promise.allSettled(
                        selectedMonitoredBooks.map((b) =>
                          toggleSingleBookMonitor(b, 'ebook', false),
                        ),
                      ),
                  },
                  {
                    label: 'Unmonitor audiobook',
                    onClick: () =>
                      void Promise.allSettled(
                        selectedMonitoredBooks.map((b) =>
                          toggleSingleBookMonitor(b, 'audiobook', false),
                        ),
                      ),
                  },
                ],
              },
              {
                key: 'hide',
                icon: (
                  <svg
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={1.8}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M3.98 8.223A10.477 10.477 0 0 0 1.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.451 10.451 0 0 1 12 4.5c4.756 0 8.773 3.162 10.065 7.498a10.522 10.522 0 0 1-4.293 5.774M6.228 6.228 3 3m3.228 3.228 3.65 3.65m7.894 7.894L21 21m-3.228-3.228-3.65-3.65m0 0a3 3 0 1 0-4.243-4.243m4.242 4.242L9.88 9.88"
                    />
                  </svg>
                ),
                title: 'Hide selected',
                onClick: () => void bulkHideMonitoredBooks(),
                borderColor: 'teal' as const,
              },
              {
                key: 'unmonitor',
                icon: (
                  <svg
                    className="h-4 w-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    strokeWidth={1.8}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0"
                    />
                  </svg>
                ),
                title: 'Unmonitor selected',
                onClick: () => void runBulkUnmonitorSelected(),
                disabled: bulkUnmonitorRunning,
                borderColor: 'red' as const,
                dividerBefore: true,
              },
            ] satisfies FloatingSelectionBarAction[]
          }
          onSelectAll={selectAllVisibleMonitoredBooks}
          allSelected={allVisibleMonitoredBooksSelected}
          onDeselectAll={clearMonitoredBookSelection}
        />
      ) : null}
    </div>
  );
};
