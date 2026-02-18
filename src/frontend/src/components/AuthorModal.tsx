import { useCallback, useEffect, useMemo, useState } from 'react';
import { Book, ContentType, OpenReleasesOptions, ReleasePrimaryAction, StatusData } from '../types';
import { getMetadataAuthorInfo, getMetadataBookInfo, listMonitoredBooks, MonitoredBookRow, MonitoredBooksResponse, syncMonitoredEntity, updateMonitoredBooksSeries, MetadataAuthor, MetadataAuthorDetailsResult, searchMetadata, getMonitoredEntity, patchMonitoredEntity, MonitoredEntity, listMonitoredBookFiles, MonitoredBookFileRow, scanMonitoredEntityFiles, deleteMonitoredEntity } from '../services/api';
import { withBasePath } from '../utils/basePath';
import { getFormatColor } from '../utils/colorMaps';
import { Dropdown } from './Dropdown';
import { FolderBrowserModal } from './FolderBrowserModal';
import { BookDetailsModal } from './BookDetailsModal';
import { getProgressConfig } from './activity/activityStyles';

const BooksListThumbnail = ({ preview, title }: { preview?: string; title?: string }) => {
  const [imageLoaded, setImageLoaded] = useState(false);
  const [imageError, setImageError] = useState(false);

  if (!preview || imageError) {
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
}

type DownloadStatusBucket = 'queued' | 'resolving' | 'locating' | 'downloading' | 'complete' | 'error' | 'cancelled';

const isDownloadStatusBucket = (value: string | undefined): value is DownloadStatusBucket => (
  value === 'queued'
  || value === 'resolving'
  || value === 'locating'
  || value === 'downloading'
  || value === 'complete'
  || value === 'error'
  || value === 'cancelled'
);

const progressColorToBorderColor = (progressColorClass: string): string => {
  if (!progressColorClass) return 'border-sky-600';
  return progressColorClass.replace(/^bg-/, 'border-');
};

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

const getContentTypeLabel = (contentType: ContentType): string => (contentType === 'audiobook' ? 'Audiobook' : 'eBook');
const getPrimaryActionLabel = (action: ReleasePrimaryAction): string => (
  action === 'auto_search_download' ? 'Auto Search + Download' : 'Interactive Search'
);

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
}: AuthorModalProps) => {
  const [isClosing, setIsClosing] = useState(false);
  const [details, setDetails] = useState<MetadataAuthor | null>(null);
  const [supportsDetails, setSupportsDetails] = useState<boolean | null>(null);
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [showMoreDetails, setShowMoreDetails] = useState(false);
  const [booksSort, setBooksSort] = useState<'year_desc' | 'year_asc' | 'title_asc' | 'series_asc' | 'series_desc'>(() => {
    const saved = localStorage.getItem('authorBooksSort');
    return saved === 'year_desc' || saved === 'year_asc' || saved === 'title_asc' || saved === 'series_asc' || saved === 'series_desc' ? saved : 'year_desc';
  });

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

  const [pathsEntity, setPathsEntity] = useState<MonitoredEntity | null>(null);
  const [pathsLoading, setPathsLoading] = useState(false);
  const [pathsError, setPathsError] = useState<string | null>(null);
  const [ebookAuthorDir, setEbookAuthorDir] = useState('');
  const [audiobookAuthorDir, setAudiobookAuthorDir] = useState('');
  const [pathsSaving, setPathsSaving] = useState(false);
  const [authorDeleting, setAuthorDeleting] = useState(false);
  const [pathsBrowserState, setPathsBrowserState] = useState<{ open: boolean; kind: 'ebook' | 'audiobook' | null; initialPath: string | null }>({
    open: false,
    kind: null,
    initialPath: null,
  });
  const [isEditModalOpen, setIsEditModalOpen] = useState(false);

  const [filesLoading, setFilesLoading] = useState(false);
  const [filesError, setFilesError] = useState<string | null>(null);
  const [files, setFiles] = useState<MonitoredBookFileRow[]>([]);
  const [autoRefreshBusy, setAutoRefreshBusy] = useState(false);
  const [activeBookDetails, setActiveBookDetails] = useState<Book | null>(null);
  const [pendingAutoSearchByKey, setPendingAutoSearchByKey] = useState<Record<string, boolean>>({});
  const [hasAppliedInitialBookSelection, setHasAppliedInitialBookSelection] = useState(false);
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

  const buildReleaseActionKey = (b: Book, contentType: ContentType): string => {
    const provider = b.provider || '';
    const providerId = b.provider_id || '';
    const fallbackId = b.id != null ? String(b.id) : '';
    return `${contentType}:${provider}:${providerId}:${fallbackId}`;
  };

  const resolvePrimaryActionForContentType = useCallback(
    (contentType: ContentType): ReleasePrimaryAction => {
      return contentType === 'audiobook' ? defaultReleaseActionAudiobook : defaultReleaseActionEbook;
    },
    [defaultReleaseActionAudiobook, defaultReleaseActionEbook]
  );

  const triggerReleaseSearch = useCallback(
    async (
      book: Book,
      contentType: ContentType,
      actionOverride?: ReleasePrimaryAction,
      options?: OpenReleasesOptions,
    ) => {
      if (!onGetReleases) return;
      const effectiveAction = actionOverride || resolvePrimaryActionForContentType(contentType);
      const actionKey = buildReleaseActionKey(book, contentType);
      const shouldShowImmediateSpinner = effectiveAction === 'auto_search_download';

      if (shouldShowImmediateSpinner) {
        setPendingAutoSearchByKey((prev) => ({ ...prev, [actionKey]: true }));
      }

      try {
        await onGetReleases(book, contentType, monitoredEntityId, actionOverride, options);
      } finally {
        if (shouldShowImmediateSpinner) {
          setPendingAutoSearchByKey((prev) => {
            if (!prev[actionKey]) return prev;
            const next = { ...prev };
            delete next[actionKey];
            return next;
          });
        }
      }
    },
    [onGetReleases, monitoredEntityId, resolvePrimaryActionForContentType]
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
    if (!author || !monitoredEntityId) {
      setPathsEntity(null);
      setPathsError(null);
      setPathsLoading(false);
      setEbookAuthorDir('');
      setAudiobookAuthorDir('');
      setIsEditModalOpen(false);

      setFilesLoading(false);
      setFilesError(null);
      setFiles([]);
      return;
    }

    let alive = true;
    const load = async () => {
      setPathsLoading(true);
      setPathsError(null);
      try {
        const entity = await getMonitoredEntity(monitoredEntityId);
        if (!alive) return;
        setPathsEntity(entity);
        const settings = entity.settings || {};
        setEbookAuthorDir(typeof settings.ebook_author_dir === 'string' ? settings.ebook_author_dir : '');
        setAudiobookAuthorDir(typeof settings.audiobook_author_dir === 'string' ? settings.audiobook_author_dir : '');
      } catch (e) {
        if (!alive) return;
        const message = e instanceof Error ? e.message : 'Failed to load monitored paths';
        setPathsError(message);
        setPathsEntity(null);
      } finally {
        if (alive) setPathsLoading(false);
      }
    };

    void load();
    return () => {
      alive = false;
    };
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
      const resp = await listMonitoredBookFiles(monitoredEntityId);
      setFiles(resp.files || []);
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Refresh & scan failed';
      console.warn('AuthorModal: Refresh & scan failed', message);
      if (!String(message).toLowerCase().includes('directory not found')) {
        setFilesError(message);
      }
    } finally {
      setIsRefreshing(false);
    }
  }, [monitoredEntityId]);

  const statusByBookKey = useMemo(() => {
    const map = new Map<string, { bucket: string; progress?: number }>();
    if (!status) return map;

    const addBucket = (bucketName: string, bucket: Record<string, Book> | undefined) => {
      if (!bucket) return;
      for (const [recordKey, b] of Object.entries(bucket)) {
        const progress = typeof b.progress === 'number' ? b.progress : undefined;
        const normalizedRecordKey = normalizeStatusKeyPart(recordKey);
        if (normalizedRecordKey && !map.has(`rk:${normalizedRecordKey}`)) {
          map.set(`rk:${normalizedRecordKey}`, { bucket: bucketName, progress });
        }

        for (const key of buildBookStatusKeys(b)) {
          if (!map.has(key)) {
            map.set(key, { bucket: bucketName, progress });
          }
        }
      }
    };

    addBucket('queued', status.queued);
    addBucket('resolving', status.resolving);
    addBucket('locating', status.locating);
    addBucket('downloading', status.downloading);
    addBucket('complete', status.complete);
    addBucket('error', status.error);
    addBucket('cancelled', status.cancelled);

    return map;
  }, [status]);

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
    if (!author) {
      setDetails(null);
      setSupportsDetails(null);
      setDetailsError(null);
      setIsLoadingDetails(false);
      setShowMoreDetails(false);
      setBooks([]);
      setBooksError(null);
      setIsLoadingBooks(false);

      setPathsEntity(null);
      setPathsError(null);
      setPathsLoading(false);
      setEbookAuthorDir('');
      setAudiobookAuthorDir('');
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
      preview: row.cover_url || undefined,
      isbn_13: row.isbn_13 || undefined,
      provider: row.provider || undefined,
      provider_id: row.provider_book_id || undefined,
      series_name: row.series_name || undefined,
      series_position: row.series_position != null ? row.series_position : undefined,
      series_count: row.series_count != null ? row.series_count : undefined,
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

    const REFRESH_TTL_MS = 24 * 60 * 60 * 1000; // 24 hours
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
            merged.push({
              ...book,
              series_name: book.series_name || cached?.series_name,
              series_position: book.series_position ?? cached?.series_position,
              series_count: book.series_count ?? cached?.series_count,
            });
          }

          for (const book of cachedBooks) {
            if (!seen.has(book.id)) {
              const cur = currentById.get(book.id);
              merged.push(cur || book);
            }
          }

          return merged;
        });
        setIsRefreshing(false);
      }

      if (monitoredEntityId) {
        try {
          await syncMonitoredEntity(monitoredEntityId);
          if (seriesUpdates.length > 0) {
            await updateMonitoredBooksSeries(monitoredEntityId, seriesUpdates);
          }
        } catch {
          // Best-effort sync, don't block UI
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
              cachedBooks = resp.books.map(monitoredBookToBook);
              setBooks(cachedBooks);
              setIsLoadingBooks(false);

              // Skip provider refresh if last sync was <24hrs ago and not a forced refresh
              if (!forceRefresh && resp.last_checked_at) {
                const lastChecked = new Date(resp.last_checked_at + 'Z').getTime();
                if (Date.now() - lastChecked < REFRESH_TTL_MS) {
                  skipProviderRefresh = true;
                }
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

    const withinGroupSort = (a: Book, b: Book) => {
      if (booksSort === 'title_asc') {
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
  }, [books, booksSort]);

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
    if (!q) return groupedBooks;

    return groupedBooks
      .map((g) => {
        const titleMatch = (g.title || '').toLowerCase().includes(q);
        if (titleMatch) return g;
        const matching = g.books.filter((b) => (b.title || '').toLowerCase().includes(q));
        if (matching.length === 0) return null;
        return { ...g, books: matching };
      })
      .filter((g): g is { key: string; title: string; books: Book[] } => g != null);
  }, [groupedBooks, activeBooksQuery]);

  const visibleBooks = useMemo(() => {
    return filteredGroupedBooks.flatMap((group) => group.books);
  }, [filteredGroupedBooks]);

  const allVisibleBooksSelected = useMemo(() => {
    return visibleBooks.length > 0 && visibleBooks.every((book) => Boolean(selectedBookIds[book.id]));
  }, [visibleBooks, selectedBookIds]);

  const selectedBooks = useMemo(() => {
    return books.filter((book) => Boolean(selectedBookIds[book.id]));
  }, [books, selectedBookIds]);

  const showIndividualBookSelectors = selectedBooks.length > 0;

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

  const renderBookSearchActionMenu = (book: Book) => {
    if (!onGetReleases) return null;

    const prov = book.provider || '';
    const bid = book.provider_id || '';
    const key = prov && bid ? `${prov}:${bid}` : '';
    const types = key ? matchedFileTypesByBookKey.get(key) : undefined;
    const hasEbookMatch = Boolean(types && EBOOK_MATCH_FORMATS.some((format) => types.has(format)));
    const hasAudioMatch = Boolean(types && AUDIOBOOK_MATCH_FORMATS.some((format) => types.has(format)));

    const primaryContentType: ContentType = defaultReleaseContentType === 'audiobook' ? 'audiobook' : 'ebook';
    const primaryAction = resolvePrimaryActionForContentType(primaryContentType);
    const primaryActionLabel = getPrimaryActionLabel(primaryAction);
    const primaryContentLabel = getContentTypeLabel(primaryContentType);
    const primaryTitle = `Primary action: ${primaryContentLabel} · ${primaryActionLabel}`;

    const primaryActionKey = buildReleaseActionKey(book, primaryContentType);
    const primaryPendingAuto = Boolean(pendingAutoSearchByKey[primaryActionKey]);
    const primaryHasMatch = primaryContentType === 'audiobook' ? hasAudioMatch : hasEbookMatch;
    const primaryColor = primaryHasMatch
      ? (primaryContentType === 'audiobook' ? 'text-violet-600 dark:text-violet-400' : 'text-emerald-600 dark:text-emerald-400')
      : 'text-gray-600 dark:text-gray-200';

    const activity = buildBookStatusKeys(book)
      .map((statusKey) => statusByBookKey.get(statusKey))
      .find((item) => Boolean(item));
    const bucket = activity?.bucket;
    const showSpinner = primaryPendingAuto || bucket === 'queued' || bucket === 'resolving' || bucket === 'locating' || bucket === 'downloading';
    const ringColor = isDownloadStatusBucket(bucket)
      ? progressColorToBorderColor(getProgressConfig(bucket, activity?.progress).color)
      : 'border-sky-600';

    const isDefault = (contentType: ContentType, action: ReleasePrimaryAction): boolean => {
      return primaryContentType === contentType && resolvePrimaryActionForContentType(contentType) === action;
    };

    return (
      <div className="inline-flex items-stretch rounded-lg border border-[var(--border-muted)]" style={{ background: 'var(--bg-soft)' }}>
        <button
          type="button"
          className={`flex h-9 w-9 items-center justify-center transition-colors duration-200 hover-surface ${primaryColor}`}
          onClick={() => void triggerReleaseSearch(book, primaryContentType)}
          aria-label={`Run primary search action for ${book.title || 'this book'}`}
          title={primaryTitle}
        >
          <span className="relative inline-flex items-center justify-center" title={bucket ? `Status: ${bucket}` : primaryPendingAuto ? 'Status: auto searching' : undefined}>
            {primaryContentType === 'audiobook' ? <AudiobookIcon className="w-4 h-4" /> : <BookIcon className="w-4 h-4" />}
            {showSpinner ? (
              <span
                className={`pointer-events-none absolute -inset-1 rounded-full border-[3px] border-t-transparent ${ringColor} animate-spin`}
                aria-hidden="true"
              />
            ) : null}
          </span>
        </button>
        <Dropdown
          widthClassName="w-auto"
          align="right"
          panelClassName="z-[2200] min-w-[240px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
          renderTrigger={({ isOpen, toggle }) => (
            <button
              type="button"
              onClick={toggle}
              className={`h-9 w-8 border-l border-[var(--border-muted)] inline-flex items-center justify-center transition-colors duration-200 hover-surface ${primaryColor}`}
              aria-label="Choose search mode and content type"
              title={`Choose mode (current: ${primaryContentLabel} · ${primaryActionLabel})`}
            >
              <svg className={`w-3.5 h-3.5 transition-transform ${isOpen ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.8">
                <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
              </svg>
            </button>
          )}
        >
          {({ close }) => (
            <div className="py-1">
              {SEARCH_DROPDOWN_OPTIONS.map((option) => {
                const optionIsDefault = isDefault(option.contentType, option.action);
                return (
                  <button
                    type="button"
                    key={`${option.contentType}:${option.action}`}
                    onClick={() => {
                      close();
                      void triggerReleaseSearch(book, option.contentType, option.action);
                    }}
                    className={`w-full px-3 py-2 text-left text-sm hover-surface flex items-center justify-between ${optionIsDefault ? 'text-sky-600 dark:text-sky-400 font-medium' : ''}`}
                  >
                    <span>{option.label}</span>
                    {optionIsDefault ? <span className="text-[10px] uppercase tracking-wide opacity-80">Default</span> : null}
                  </button>
                );
              })}
            </div>
          )}
        </Dropdown>
      </div>
    );
  };

  if (!author && !isClosing) return null;
  if (!author) return null;

  const resolvedName = details?.name || author.name;
  const resolvedPhoto = details?.photo_url || author.photo_url || null;
  const resolvedBio = details?.bio || null;
  const resolvedUrl = details?.source_url || author.source_url || null;
  const providerLabel = details?.provider || author.provider || null;
  const booksCount = details?.stats?.books_count ?? null;

  const normalizePath = (value: string): string => {
    const v = (value || '').trim();
    if (!v) return '';
    return v.replace(/\/+$/g, '');
  };

  const handleSavePaths = async () => {
    if (!monitoredEntityId) return;
    const ebook = normalizePath(ebookAuthorDir);
    const audio = normalizePath(audiobookAuthorDir);
    if (!ebook && !audio) {
      setPathsError('Please set an Ebook folder or Audiobook folder.');
      return;
    }

    setPathsSaving(true);
    setPathsError(null);
    try {
      const updated = await patchMonitoredEntity(monitoredEntityId, {
        settings: {
          ebook_author_dir: ebook || undefined,
          audiobook_author_dir: audio || undefined,
        },
      });
      setPathsEntity(updated);
      const settings = updated.settings || {};
      setEbookAuthorDir(typeof settings.ebook_author_dir === 'string' ? settings.ebook_author_dir : ebook);
      setAudiobookAuthorDir(typeof settings.audiobook_author_dir === 'string' ? settings.audiobook_author_dir : audio);
      setIsEditModalOpen(false);
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to save paths';
      setPathsError(message);
    } finally {
      setPathsSaving(false);
    }
  };

  const handleDeleteAuthor = async () => {
    if (!monitoredEntityId || authorDeleting) return;

    const confirmed = window.confirm(
      `Delete monitored author "${resolvedName || 'Unknown author'}"?\n\n` +
      'This removes monitored author data from Shelfmark database only (books, file matches, and settings for this monitored author).\n' +
      'Files on disk will NOT be deleted.'
    );
    if (!confirmed) return;

    setAuthorDeleting(true);
    setPathsError(null);
    try {
      await deleteMonitoredEntity(monitoredEntityId);
      setIsEditModalOpen(false);
      handleClose();
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to delete monitored author';
      setPathsError(message);
    } finally {
      setAuthorDeleting(false);
    }
  };

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
            ? 'flex flex-col overflow-hidden rounded-2xl border border-black/10 dark:border-white/10 bg-white/80 dark:bg-white/5 text-[var(--text)] shadow-xl'
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
                        onClick={() => {
                          setPathsError(null);
                          setIsEditModalOpen(true);
                        }}
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
                            onClick={() => {
                              setPathsError(null);
                              setIsEditModalOpen(true);
                            }}
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

              <div className="mt-4 rounded-2xl border border-[var(--border-muted)] bg-[var(--bg-soft)] sm:bg-[var(--bg)] overflow-hidden">
                <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-[var(--border-muted)]">
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
                    <button
                      type="button"
                      onClick={toggleSelectAllVisibleBooks}
                      className="p-1.5 rounded-full text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action transition-all duration-200"
                      aria-label={allVisibleBooksSelected ? 'Unselect all visible books' : 'Select all visible books'}
                      title={allVisibleBooksSelected ? 'Unselect all books' : 'Select all books'}
                    >
                      {allVisibleBooksSelected ? (
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
                  </div>
                </div>

                {monitoredEntityId ? (
                  <div className="px-4 pb-3">
                    {filesError ? <div className="text-sm text-red-500">{filesError}</div> : null}
                    {filesLoading ? <div className="text-sm text-gray-600 dark:text-gray-300">Loading files…</div> : null}
                  </div>
                ) : null}

                <div className="px-4 py-3">
                  {booksError && <div className="text-sm text-red-500">{booksError}</div>}

                  {books.length === 0 && isLoadingBooks ? (
                    <div className="text-sm text-gray-600 dark:text-gray-300">Loading…</div>
                  ) : books.length === 0 && !isLoadingBooks ? (
                    <div className="text-sm text-gray-600 dark:text-gray-300">No books found.</div>
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
                              <div className="w-full px-3 sm:px-4 py-2 border-t border-b border-gray-200/60 dark:border-gray-800/60 bg-black/5 dark:bg-white/5 flex items-center justify-between gap-3">
                                <button
                                  type="button"
                                  onClick={() => toggleGroupCollapsed(group.key)}
                                  className="flex items-center gap-2 min-w-0 hover-action"
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
                                    <p className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 truncate">{group.title}</p>
                                    <span className={`text-[11px] tabular-nums ${booksOnDisk > 0 ? 'text-emerald-500 dark:text-emerald-400' : 'text-gray-500 dark:text-gray-400'}`}>
                                      ({booksOnDisk}/{booksInSeries})
                                    </span>
                                  </div>
                                </button>
                                <button
                                  type="button"
                                  onClick={() => toggleSelectAllInGroup(group.books)}
                                  className="text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 transition-colors"
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
                              </div>

                              {!isCollapsed ? (
                                <div className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
                                  <div className="hidden sm:grid items-center px-1.5 sm:px-2 pt-1 pb-2 grid-cols-[auto_auto_minmax(0,2fr)_minmax(164px,164px)_minmax(64px,64px)]">
                                    <div />
                                    <div />
                                    <div />
                                    <div className="flex justify-center">
                                      <span className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400">Available</span>
                                    </div>
                                    <div />
                                  </div>
                                  {group.books.map((book) => (
                                    <div
                                      key={book.id}
                                      className="px-1.5 sm:px-2 py-1.5 sm:py-2 transition-colors duration-200 hover-row w-full"
                                    >
                                      <div className="grid items-center gap-2 sm:gap-y-1 sm:gap-x-2 w-full grid-cols-[auto_auto_minmax(0,1fr)_auto] sm:grid-cols-[auto_auto_minmax(0,2fr)_minmax(164px,164px)_minmax(64px,64px)]">
                                        <div className="flex items-center justify-center pl-0.5 sm:pl-1">
                                          {showIndividualBookSelectors ? (
                                            <input
                                              type="checkbox"
                                              checked={Boolean(selectedBookIds[book.id])}
                                              onChange={() => toggleBookSelection(book.id)}
                                              className="h-4 w-4 rounded border-gray-400 text-emerald-600 focus:ring-emerald-500"
                                              aria-label={`Select ${book.title || 'book'}`}
                                            />
                                          ) : (
                                            <span className="inline-block h-4 w-4" aria-hidden="true" />
                                          )}
                                        </div>

                                        <div className="flex items-center pl-1 sm:pl-3">
                                          <BooksListThumbnail preview={book.preview} title={book.title} />
                                        </div>

                                        <button
                                          type="button"
                                          className="min-w-0 flex flex-col justify-center sm:pl-3 text-left"
                                          onClick={() => setActiveBookDetails(book)}
                                        >
                                          <h3 className="font-semibold text-xs min-[400px]:text-sm sm:text-base leading-tight line-clamp-1 sm:line-clamp-2" title={book.title || 'Untitled'}>
                                            <span className="truncate">{book.title || 'Untitled'}</span>
                                          </h3>
                                          <p className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-600 dark:text-gray-300 truncate">
                                            {book.author || resolvedName || 'Unknown author'}
                                            {book.year ? <span> • {book.year}</span> : null}
                                          </p>
                                          {group.key !== '__standalone__' && book.series_position != null ? (
                                            <div className="text-[10px] min-[400px]:text-xs text-gray-500 dark:text-gray-400 truncate flex items-center gap-2">
                                              <span
                                                className="inline-flex px-1 py-0 text-[9px] sm:text-[10px] font-bold text-white bg-emerald-600 rounded flex-shrink-0"
                                                style={{
                                                  boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)',
                                                  textShadow: '0 1px 2px rgba(0, 0, 0, 0.3)',
                                                }}
                                                title={`${group.title}${book.series_count ? ` (${book.series_position}/${book.series_count})` : ` (#${book.series_position})`}`}
                                              >
                                                #{book.series_position}
                                                {book.series_count != null ? `/${book.series_count}` : ''}
                                              </span>
                                              <span className="truncate" title={group.title}>{group.title}</span>
                                            </div>
                                          ) : null}
                                        </button>

                                        {(() => {
                                          const prov = book.provider || '';
                                          const bid = book.provider_id || '';
                                          const key = prov && bid ? `${prov}:${bid}` : '';
                                          const types = key ? matchedFileTypesByBookKey.get(key) : undefined;
                                          if (!types || types.size === 0) {
                                            return <div className="hidden sm:flex" />;
                                          }

                                          const sorted = Array.from(types).sort((a, b) => a.localeCompare(b));
                                          return (
                                            <div className="hidden sm:flex items-center justify-center gap-1">
                                              {sorted.slice(0, 2).map((t) => (
                                                <span
                                                  key={t}
                                                  className={`${getFormatColor(t).bg} ${getFormatColor(t).text} inline-flex items-center px-2 py-0.5 rounded-lg text-[10px] font-semibold tracking-wide uppercase`}
                                                  title={`Matched file: ${t.toUpperCase()}`}
                                                >
                                                  {t.toUpperCase()}
                                                </span>
                                              ))}
                                            </div>
                                          );
                                        })()}

                                        <div className="relative flex flex-row justify-end gap-1 sm:gap-1.5 sm:pr-3">
                                          {renderBookSearchActionMenu(book)}
                                          {book.source_url ? (
                                            <a
                                              className="flex items-center justify-center p-1.5 sm:p-2 rounded-full text-gray-600 dark:text-gray-200 hover-action transition-all duration-200"
                                              href={book.source_url}
                                              target="_blank"
                                              rel="noreferrer"
                                              aria-label={`View source for ${book.title || 'this book'}`}
                                            >
                                              <svg className="w-4 h-4 sm:w-5 sm:h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth="1.5">
                                                <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 6H18a.75.75 0 0 1 .75.75V18A.75.75 0 0 1 18 18.75H6A.75.75 0 0 1 5.25 18V6.75A.75.75 0 0 1 6 6h4.5" />
                                                <path strokeLinecap="round" strokeLinejoin="round" d="M12 12.75 18.75 6M15 6h3.75v3.75" />
                                              </svg>
                                            </a>
                                          ) : null}
                                        </div>
                                      </div>
                                    </div>
                                  ))}
                                </div>
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
      <FolderBrowserModal
        open={pathsBrowserState.open}
        title={pathsBrowserState.kind === 'audiobook' ? 'Select audiobook folder' : 'Select ebook folder'}
        initialPath={pathsBrowserState.initialPath}
        overlayZIndex={2100}
        onClose={() => setPathsBrowserState({ open: false, kind: null, initialPath: null })}
        onSelect={(path) => {
          const authorName = (resolvedName || author?.name || '').trim();
          const suggested = authorName ? `${normalizePath(path)}/${authorName}` : path;
          if (pathsBrowserState.kind === 'audiobook') {
            setAudiobookAuthorDir(suggested);
          } else {
            setEbookAuthorDir(suggested);
          }
        }}
      />

      <BookDetailsModal
        book={activeBookDetails}
        files={activeBookFiles}
        monitoredEntityId={monitoredEntityId}
        onClose={() => setActiveBookDetails(null)}
        onOpenSearch={(contentType) => {
          if (!activeBookDetails || !onGetReleases) return;
          void triggerReleaseSearch(activeBookDetails, contentType);
        }}
      />

      {isEditModalOpen ? (
        <div
          className="modal-overlay active sm:px-6 sm:py-6"
          style={{ zIndex: 2000, pointerEvents: pathsBrowserState.open ? 'none' : 'auto' }}
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              const settings = pathsEntity?.settings || {};
              setEbookAuthorDir(typeof settings.ebook_author_dir === 'string' ? settings.ebook_author_dir : '');
              setAudiobookAuthorDir(typeof settings.audiobook_author_dir === 'string' ? settings.audiobook_author_dir : '');
              setPathsError(null);
              setIsEditModalOpen(false);
            }
          }}
        >
          <div className="details-container w-full max-w-2xl h-auto settings-modal-enter" role="dialog" aria-modal="true" aria-label="Edit monitored author">
            <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-2xl overflow-hidden">
              <header className="flex items-start justify-between gap-3 border-b border-[var(--border-muted)] px-5 py-4">
                <div className="min-w-0">
                  <div className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Edit</div>
                  <div className="mt-1 text-base font-semibold truncate">{resolvedName || 'Unknown author'}</div>
                </div>
                <button
                  type="button"
                  onClick={() => {
                    const settings = pathsEntity?.settings || {};
                    setEbookAuthorDir(typeof settings.ebook_author_dir === 'string' ? settings.ebook_author_dir : '');
                    setAudiobookAuthorDir(typeof settings.audiobook_author_dir === 'string' ? settings.audiobook_author_dir : '');
                    setPathsError(null);
                    setIsEditModalOpen(false);
                  }}
                  className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
                  aria-label="Close"
                >
                  <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </header>

              <div className="px-5 py-4 space-y-4">
                {pathsLoading ? <div className="text-sm text-gray-500 dark:text-gray-400">Loading…</div> : null}
                {pathsError ? <div className="text-sm text-red-500">{pathsError}</div> : null}

                <div className="space-y-2">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">eBooks Path</div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setPathsBrowserState({ open: true, kind: 'ebook', initialPath: ebookAuthorDir || null })}
                      className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                    >
                      Browse
                    </button>
                    <input
                      value={ebookAuthorDir}
                      onChange={(e) => setEbookAuthorDir(e.target.value)}
                      placeholder="/books/ebooks/Author Name"
                      className="flex-1 px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm"
                    />
                  </div>
                </div>

                <div className="space-y-2">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Audiobooks Path</div>
                  <div className="flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => setPathsBrowserState({ open: true, kind: 'audiobook', initialPath: audiobookAuthorDir || null })}
                      className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                    >
                      Browse
                    </button>
                    <input
                      value={audiobookAuthorDir}
                      onChange={(e) => setAudiobookAuthorDir(e.target.value)}
                      placeholder="/books/audiobooks/Author Name"
                      className="flex-1 px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm"
                    />
                  </div>
                </div>
              </div>

              <footer className="flex items-center justify-between gap-2 border-t border-[var(--border-muted)] px-5 py-4 bg-[var(--bg)] sm:bg-[var(--bg-soft)]">
                <button
                  type="button"
                  onClick={() => void handleDeleteAuthor()}
                  disabled={authorDeleting || pathsSaving || !monitoredEntityId}
                  className="px-4 py-2 rounded-full bg-red-600 hover:bg-red-700 disabled:opacity-60 text-white font-medium"
                  title="Deletes monitored author records from database only. Files on disk are not deleted."
                >
                  {authorDeleting ? 'Deleting…' : 'Delete Author'}
                </button>
                <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => {
                    const settings = pathsEntity?.settings || {};
                    setEbookAuthorDir(typeof settings.ebook_author_dir === 'string' ? settings.ebook_author_dir : '');
                    setAudiobookAuthorDir(typeof settings.audiobook_author_dir === 'string' ? settings.audiobook_author_dir : '');
                    setPathsError(null);
                    setIsEditModalOpen(false);
                  }}
                  className="px-4 py-2 rounded-full bg-white/70 hover:bg-white text-gray-900 font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={pathsSaving || authorDeleting}
                  onClick={() => void handleSavePaths()}
                  className="px-4 py-2 rounded-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white font-medium"
                >
                  {pathsSaving ? 'Saving…' : 'Save'}
                </button>
                </div>
              </footer>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
};
