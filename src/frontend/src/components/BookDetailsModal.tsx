import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';

import { useSwipe } from '../hooks/useSwipe';
import {
  listMonitoredBookFiles,
  listMonitoredBookEvents,
  listMonitoredBooks,
  promoteCandidate,
  rejectCandidate,
  MonitoredBookRow,
  MonitoredBookFileRow,
  MonitoredEvent,
} from '../services/monitoredApi';
import { Book, ContentType } from '../types';
import { getFormatColor } from '../utils/colorMaps';
import { EvidencePanel } from './EvidencePanel';
import { FixMatchModal, type FixMatchTarget } from './FixMatchModal';
import { MonitoredEventRow, parseEventMeta } from './MonitoredEventRow';

interface BookDetailsModalProps {
  entityId: number | null;
  provider: string | null;
  providerBookId: string | null;
  monitorEbook?: boolean;
  monitorAudiobook?: boolean;
  onClose: () => void;
  onToggleMonitor?: (type: 'ebook' | 'audiobook' | 'both') => void;
  onNavigateToSeries?: (seriesName: string) => void;
  onAuthorClick?: (authorName: string) => void;
  renderEmbeddedSearch: (
    book: Book,
    contentType: ContentType,
    monitoredEntityId?: number | null,
  ) => ReactNode;
  previewBook?: Book | null;
  onMonitorBook?: (book: Book) => void;
  onSetReleaseDate?: (book: MonitoredBookRow) => void;
  hidden?: boolean;
  onToggleHidden?: () => void;
  /** Called after a manual file attribution change so the parent can
   *  refresh its books list (the has_*_available chips on book cards). */
  onBookModified?: () => void;
}

type TabKey = 'details' | 'files' | 'history' | 'ebooks' | 'audiobooks';
const TAB_ORDER: readonly TabKey[] = ['details', 'files', 'history', 'ebooks', 'audiobooks'];

const isEnabledFlag = (value: unknown): boolean => value === true || value === 1;

export const BookDetailsModal = ({
  entityId,
  provider,
  providerBookId,
  monitorEbook,
  monitorAudiobook,
  onClose,
  onToggleMonitor,
  onNavigateToSeries,
  onAuthorClick,
  renderEmbeddedSearch,
  previewBook,
  onMonitorBook,
  onSetReleaseDate,
  hidden,
  onToggleHidden,
  onBookModified,
}: BookDetailsModalProps) => {
  const [isClosing, setIsClosing] = useState(false);
  const [tab, setTab] = useState<TabKey>('details');
  const [showHeaderThumb, setShowHeaderThumb] = useState(false);
  const scrollContainerRef = useRef<HTMLDivElement | null>(null);
  const coverSentinelRef = useRef<HTMLDivElement | null>(null);
  const indicatorRef = useRef<HTMLDivElement | null>(null);
  const tabRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const [bookRow, setBookRow] = useState<MonitoredBookRow | null>(null);
  const [bookLoading, setBookLoading] = useState(false);
  const [bookError, setBookError] = useState<string | null>(null);

  const [files, setFiles] = useState<MonitoredBookFileRow[]>([]);
  const [expandedEvidenceIds, setExpandedEvidenceIds] = useState<Set<number>>(new Set());
  const [fixMatchTarget, setFixMatchTarget] = useState<FixMatchTarget | null>(null);
  const [pendingCandidateAction, setPendingCandidateAction] = useState<Set<number>>(new Set());
  const [candidateActionError, setCandidateActionError] = useState<{
    id: number;
    message: string;
  } | null>(null);

  // Split rows by `status`. Absent → 'matched' (back-compat with rows written
  // before the three-tier migration).
  const matchedFiles = useMemo(
    () => files.filter((f) => (f.status ?? 'matched') === 'matched'),
    [files],
  );
  const candidateFiles = useMemo(() => files.filter((f) => f.status === 'candidate'), [files]);
  // Bumped after a successful Fix-match apply to retrigger BOTH the files-fetch
  // AND the book-row-fetch effects, since manual attribution changes the book's
  // has_*_available flags too.
  const [filesReloadKey, setFilesReloadKey] = useState(0);

  // Reset evidence panel state when the modal targets a different book — file
  // row ids are entity-scoped and can collide across books.
  useEffect(() => {
    setExpandedEvidenceIds(new Set());
  }, [entityId, provider, providerBookId]);

  const [eventRows, setEventRows] = useState<MonitoredEvent[]>([]);
  const [eventsLoading, setEventsLoading] = useState(false);
  const [eventsError, setEventsError] = useState<string | null>(null);

  const [descriptionExpanded, setDescriptionExpanded] = useState(false);
  const [descriptionOverflows, setDescriptionOverflows] = useState(false);
  const [descriptionEl, setDescriptionEl] = useState<HTMLParagraphElement | null>(null);

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [onClose]);

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') handleClose();
    };
    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [handleClose]);

  const syncIndicator = useCallback(() => {
    const el = indicatorRef.current;
    const activeButton = tabRefs.current[tab];
    if (!el || !activeButton) return;
    const containerRect = activeButton.parentElement?.getBoundingClientRect();
    const buttonRect = activeButton.getBoundingClientRect();
    if (containerRect) {
      el.style.left = `${buttonRect.left - containerRect.left}px`;
      el.style.width = `${buttonRect.width}px`;
    }
  }, [tab]);

  // Sync on tab change
  useEffect(() => {
    syncIndicator();
  }, [syncIndicator]);

  const goNextTab = useCallback(
    () =>
      setTab((prev) => {
        const i = TAB_ORDER.indexOf(prev);
        return i < TAB_ORDER.length - 1 ? TAB_ORDER[i + 1] : prev;
      }),
    [],
  );
  const goPrevTab = useCallback(
    () =>
      setTab((prev) => {
        const i = TAB_ORDER.indexOf(prev);
        return i > 0 ? TAB_ORDER[i - 1] : prev;
      }),
    [],
  );
  const swipeHandlers = useSwipe({ onSwipeLeft: goNextTab, onSwipeRight: goPrevTab });

  useEffect(() => {
    if (entityId != null && provider && providerBookId) {
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = previousOverflow;
      };
    }
  }, [entityId, provider, providerBookId]);

  useEffect(() => {
    if (entityId == null || !provider || !providerBookId) {
      setBookRow(null);
      setBookLoading(false);
      setBookError(null);
      return;
    }

    let cancelled = false;
    setBookLoading(true);
    setBookError(null);
    void (async () => {
      try {
        const resp = await listMonitoredBooks(entityId);
        if (cancelled) return;
        const normalizedProvider = provider.trim();
        const normalizedProviderId = providerBookId.trim();
        const match = (resp.books || []).find((row) => {
          if ((row.provider || '').trim() !== normalizedProvider) return false;
          if ((row.provider_book_id || '').trim() !== normalizedProviderId) return false;
          return true;
        });
        setBookRow(match || null);
        if (!match) {
          setBookError('Book not found in monitored database.');
        }
      } catch (error) {
        if (cancelled) return;
        setBookRow(null);
        setBookError(error instanceof Error ? error.message : 'Failed to load book details');
      } finally {
        if (!cancelled) {
          setBookLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityId, provider, providerBookId, filesReloadKey]);

  useEffect(() => {
    if (entityId == null || !provider || !providerBookId) {
      setFiles([]);
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        const resp = await listMonitoredBookFiles(entityId);
        if (cancelled) return;
        const normalizedProvider = provider.trim();
        const normalizedProviderId = providerBookId.trim();
        const matching = (resp.files || []).filter((file) => {
          if ((file.provider || '').trim() !== normalizedProvider) return false;
          if ((file.provider_book_id || '').trim() !== normalizedProviderId) return false;
          return true;
        });
        setFiles(matching);
      } catch (error) {
        if (cancelled) return;
        setFiles([]);
      } finally {
        // Nothing else to do.
      }
    })();

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [entityId, provider, providerBookId, filesReloadKey]);

  // Fetch unified events for History tab
  useEffect(() => {
    if (entityId == null || !provider || !providerBookId) {
      setEventRows([]);
      setEventsLoading(false);
      setEventsError(null);
      return;
    }
    let cancelled = false;
    setEventsLoading(true);
    setEventsError(null);
    void (async () => {
      try {
        const resp = await listMonitoredBookEvents(
          entityId,
          provider.trim(),
          providerBookId.trim(),
          100,
        );
        if (!cancelled) setEventRows(resp.events || []);
      } catch (error) {
        if (!cancelled) {
          setEventsError(error instanceof Error ? error.message : 'Failed to load events');
          setEventRows([]);
        }
      } finally {
        if (!cancelled) setEventsLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [entityId, provider, providerBookId]);

  useEffect(() => {
    if (entityId != null && provider && providerBookId) {
      setTab('details');
    }
  }, [entityId, provider, providerBookId]);

  // Show cover thumb in header when Details tab cover scrolls out of view or on non-Details tabs
  useEffect(() => {
    if (tab !== 'details') {
      setShowHeaderThumb(true);
      return;
    }
    const sentinel = coverSentinelRef.current;
    const container = scrollContainerRef.current;
    if (!sentinel || !container) {
      setShowHeaderThumb(false);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => setShowHeaderThumb(!entry.isIntersecting),
      { root: container, threshold: 0.1 },
    );
    observer.observe(sentinel);
    return () => observer.disconnect();
  }, [tab]);

  useEffect(() => {
    setDescriptionExpanded(false);
    setDescriptionOverflows(false);
  }, [bookRow?.id]);

  useEffect(() => {
    if (!descriptionEl || descriptionExpanded) return;
    setDescriptionOverflows(descriptionEl.scrollHeight > descriptionEl.clientHeight);
  }, [descriptionEl, descriptionExpanded, bookRow?.description]);

  const matchedFileTypes = useMemo(() => {
    const set = new Set<string>();
    for (const f of files) {
      const label = f.ext || f.file_type || '';
      const t = typeof label === 'string' ? label.trim().toLowerCase() : '';
      if (t) set.add(t);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [files]);

  const hasEbookFile = useMemo(
    () => isEnabledFlag(bookRow?.has_ebook_available),
    [bookRow?.has_ebook_available],
  );

  const hasAudiobookFile = useMemo(
    () => isEnabledFlag(bookRow?.has_audiobook_available),
    [bookRow?.has_audiobook_available],
  );

  const foundEbookPath = useMemo(() => {
    const path = (bookRow?.ebook_path || '').trim();
    return path || null;
  }, [bookRow?.ebook_path]);

  const foundAudiobookPath = useMemo(() => {
    const path = (bookRow?.audiobook_path || '').trim();
    return path || null;
  }, [bookRow?.audiobook_path]);

  const latestDownloaderFinalPath = useMemo(() => {
    for (const ev of eventRows) {
      if (ev.event_type !== 'download_complete') continue;
      const meta = parseEventMeta(ev);
      const path = (meta?.download_path || '').trim();
      if (path) return path;
    }
    return null;
  }, [eventRows]);

  const ebookMonitorLocked = hasEbookFile;
  const audiobookMonitorLocked = hasAudiobookFile;

  const monitorEbookState = useMemo(() => {
    if (typeof monitorEbook === 'boolean') return monitorEbook;
    return isEnabledFlag(bookRow?.monitor_ebook);
  }, [monitorEbook, bookRow?.monitor_ebook]);

  const monitorAudiobookState = useMemo(() => {
    if (typeof monitorAudiobook === 'boolean') return monitorAudiobook;
    return isEnabledFlag(bookRow?.monitor_audiobook);
  }, [monitorAudiobook, bookRow?.monitor_audiobook]);

  const [monitorEbookUiState, setMonitorEbookUiState] = useState(false);
  const [monitorAudiobookUiState, setMonitorAudiobookUiState] = useState(false);

  useEffect(() => {
    setMonitorEbookUiState(monitorEbookState);
  }, [monitorEbookState]);

  useEffect(() => {
    setMonitorAudiobookUiState(monitorAudiobookState);
  }, [monitorAudiobookState]);

  const parsedAdditionalSeries = useMemo(():
    | Array<{ name: string; position?: number; count?: number }>
    | undefined => {
    const primary = (bookRow?.series_name || '').trim();

    const direct = Array.isArray((bookRow as any)?.additional_series)
      ? (bookRow as any).additional_series
      : null;
    if (direct && direct.length > 0) {
      const filtered = direct
        .filter((s: any) => s && typeof s.name === 'string')
        .filter((s: any) => !primary || String(s.name).trim() !== primary);
      return filtered.length > 0 ? filtered : undefined;
    }

    const raw =
      typeof (bookRow as any)?.all_series === 'string' ? (bookRow as any).all_series : null;
    if (!raw) return undefined;
    try {
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return undefined;
      const filtered = parsed
        .filter((s: any) => s && typeof s.name === 'string')
        .map((s: any) => ({ name: String(s.name), position: s.position, count: s.count }))
        .filter((s: any) => !primary || String(s.name).trim() !== primary);
      return filtered.length > 0 ? filtered : undefined;
    } catch {
      return undefined;
    }
  }, [bookRow]);

  const embeddedSearchBook = useMemo<Book | null>(() => {
    if (!bookRow) return null;
    const authors = typeof bookRow.authors === 'string' ? bookRow.authors.trim() : '';
    const seriesName = typeof bookRow.series_name === 'string' ? bookRow.series_name.trim() : '';
    return {
      id: String(bookRow.id),
      title: bookRow.title || 'Unknown title',
      author: authors || 'Unknown author',
      year: typeof bookRow.publish_year === 'number' ? String(bookRow.publish_year) : undefined,
      preview: bookRow.cover_url || undefined,
      provider: bookRow.provider || undefined,
      provider_id: bookRow.provider_book_id || undefined,
      release_date: bookRow.release_date || undefined,
      language: typeof bookRow.language === 'string' ? bookRow.language : undefined,
      description: bookRow.description || undefined,
      isbn_13: bookRow.isbn_13 || undefined,
      isbn_10: (bookRow as any).isbn_10 || undefined,
      series_name: seriesName || undefined,
      series_position: bookRow.series_position ?? undefined,
      series_count: bookRow.series_count ?? undefined,
      additional_series: parsedAdditionalSeries,
      has_ebook_available: isEnabledFlag(bookRow.has_ebook_available),
      has_audiobook_available: isEnabledFlag(bookRow.has_audiobook_available),
      ebook_path: bookRow.ebook_path || undefined,
      audiobook_path: bookRow.audiobook_path || undefined,
      ebook_available_format: bookRow.ebook_available_format || undefined,
      audiobook_available_format: bookRow.audiobook_available_format || undefined,
    };
  }, [bookRow, parsedAdditionalSeries]);

  const genresSummary = useMemo(() => {
    const raw = bookRow?.cached_tags;
    if (raw == null) return null;

    let parsed: unknown = raw;
    if (typeof raw === 'string') {
      const trimmed = raw.trim();
      if (!trimmed) return null;
      try {
        parsed = JSON.parse(trimmed);
      } catch {
        return null;
      }
    }

    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;

    const tagItems: Array<{ item: unknown; categoryHint?: string }> = [];
    for (const [categoryKey, value] of Object.entries(parsed as Record<string, unknown>)) {
      if (Array.isArray(value)) {
        for (const item of value) {
          tagItems.push({ item, categoryHint: categoryKey });
        }
      }
    }

    if (tagItems.length === 0) return null;

    const genres = tagItems
      .map((item) => {
        if (!item.item || typeof item.item !== 'object') return null;
        const tag = item.item as {
          category?: string;
          tag_category?: string;
          tag?: { name?: string; category?: { name?: string } } | string;
          name?: string;
        };

        const categoryName =
          [
            typeof tag.tag_category === 'string' ? tag.tag_category : null,
            typeof tag.category === 'string' ? tag.category : null,
            typeof tag.tag === 'object' && tag.tag && typeof tag.tag.category?.name === 'string'
              ? tag.tag.category.name
              : null,
            typeof item.categoryHint === 'string' ? item.categoryHint : null,
          ].find((value) => typeof value === 'string' && value.trim()) || null;

        const tagName =
          [
            typeof tag.name === 'string' ? tag.name : null,
            typeof tag.tag === 'string' ? tag.tag : null,
            typeof tag.tag === 'object' && tag.tag && typeof tag.tag.name === 'string'
              ? tag.tag.name
              : null,
          ].find((value) => typeof value === 'string' && value.trim()) || null;

        if (!tagName) return null;
        if (categoryName && !categoryName.toLowerCase().includes('genre')) return null;
        return tagName.trim();
      })
      .filter((value): value is string => Boolean(value));

    if (genres.length === 0) return null;
    const uniqueGenres = Array.from(new Set(genres));
    return uniqueGenres.slice(0, 8).join(', ');
  }, [bookRow?.cached_tags]);

  const releaseDateSummary = useMemo(() => {
    if (typeof bookRow?.release_date === 'string' && bookRow.release_date.trim()) {
      return bookRow.release_date.trim();
    }
    if (typeof bookRow?.publish_year === 'number') {
      return String(bookRow.publish_year);
    }
    return null;
  }, [bookRow?.release_date, bookRow?.publish_year]);

  const displayFields = useMemo(() => {
    const fields: Array<{ label: string; value: string }> = [];
    if (typeof bookRow?.language === 'string' && bookRow.language.trim()) {
      fields.push({ label: 'Language', value: bookRow.language.trim() });
    }
    if (typeof bookRow?.readers_count === 'number' && Number.isFinite(bookRow.readers_count)) {
      fields.push({ label: 'Readers', value: bookRow.readers_count.toLocaleString() });
    }
    if (typeof bookRow?.rating === 'number' && Number.isFinite(bookRow.rating)) {
      if (typeof bookRow?.ratings_count === 'number' && Number.isFinite(bookRow.ratings_count)) {
        fields.push({
          label: 'Rating',
          value: `${bookRow.rating.toFixed(1)} (${bookRow.ratings_count.toLocaleString()})`,
        });
      } else {
        fields.push({ label: 'Rating', value: bookRow.rating.toFixed(1) });
      }
    }
    return fields;
  }, [bookRow?.language, bookRow?.readers_count, bookRow?.rating, bookRow?.ratings_count]);

  // Preview mode: render simplified modal when no monitored entity but previewBook is provided
  if (entityId == null || !provider || !providerBookId) {
    if (!previewBook) return null;
    const pb = previewBook;
    return (
      <div
        className="modal-overlay active sm:px-6 sm:py-6"
        onClick={(e) => {
          if (e.target === e.currentTarget) handleClose();
        }}
      >
        <div
          className={`details-container h-full w-full max-w-4xl sm:h-auto ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
          role="dialog"
          aria-modal="true"
        >
          <div className="flex h-full flex-col overflow-hidden rounded-none border-0 border-[var(--border-muted)] bg-[var(--bg)] text-[var(--text)] shadow-none sm:h-[90vh] sm:max-h-[90vh] sm:rounded-2xl sm:border sm:bg-[var(--bg-soft)] sm:shadow-2xl">
            <header className="flex items-start gap-3 border-b border-[var(--border-muted)] px-5 py-4">
              <div className="min-w-0 flex-1 space-y-1">
                <p className="text-xs tracking-wide text-gray-500 uppercase dark:text-gray-400">
                  Book
                </p>
                <h3 className="truncate text-lg leading-snug font-semibold">
                  {pb.title || 'Untitled'}
                </h3>
                <p className="truncate text-sm text-gray-600 dark:text-gray-300">
                  {pb.author || 'Unknown author'}
                </p>
              </div>
              <div className="flex flex-shrink-0 items-center gap-2">
                {onMonitorBook ? (
                  <button
                    type="button"
                    onClick={() => {
                      onMonitorBook(pb);
                      handleClose();
                    }}
                    className="inline-flex items-center gap-1.5 rounded-full bg-emerald-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-700"
                  >
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
                        d="M12 4.5v15m7.5-7.5h-15"
                      />
                    </svg>
                    Monitor
                  </button>
                ) : null}
                <button
                  type="button"
                  onClick={handleClose}
                  className="hover-action rounded-full p-2 text-gray-500 transition-colors hover:text-gray-900 dark:hover:text-gray-100"
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
              </div>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto">
              <div className="flex gap-4 border-b border-[var(--border-muted)] px-5 py-4">
                {pb.preview ? (
                  <img
                    src={pb.preview}
                    alt="Book cover"
                    className="h-[120px] w-20 flex-shrink-0 rounded-lg object-cover object-top shadow-md"
                  />
                ) : (
                  <div className="flex h-[120px] w-20 flex-shrink-0 items-center justify-center rounded-lg border border-dashed border-[var(--border-muted)] bg-[var(--bg)]/60 text-[10px] text-gray-500">
                    No cover
                  </div>
                )}
                <div className="min-w-0 flex-1 space-y-2">
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-600 dark:text-gray-400">
                    {pb.year ? <span>{pb.year}</span> : null}
                    {pb.series_name ? (
                      <span className="truncate">
                        {pb.series_position != null ? (
                          <>
                            #{pb.series_position}
                            {pb.series_count != null ? `/${pb.series_count}` : ''} in{' '}
                            {pb.series_name}
                          </>
                        ) : (
                          <>Part of {pb.series_name}</>
                        )}
                      </span>
                    ) : null}
                  </div>
                  {pb.description ? (
                    <p className="line-clamp-4 text-sm text-gray-600 dark:text-gray-400">
                      {pb.description}
                    </p>
                  ) : null}
                </div>
              </div>
              <div className="space-y-4 px-5 py-4">
                <div className="text-sm font-medium text-gray-700 dark:text-gray-300">Download</div>
                <div className="flex flex-wrap gap-2">
                  {renderEmbeddedSearch(pb, 'ebook', entityId)}
                </div>
                <div className="flex flex-wrap gap-2">
                  {renderEmbeddedSearch(pb, 'audiobook', entityId)}
                </div>
              </div>
            </div>
          </div>
        </div>
      </div>
    );
  }
  if (!bookRow || bookLoading || !embeddedSearchBook) {
    return (
      <div className="modal-overlay active sm:px-6 sm:py-6">
        <div
          className="details-container settings-modal-enter h-full w-full max-w-4xl sm:h-auto"
          role="dialog"
          aria-modal="true"
        >
          <div className="flex h-full flex-col overflow-hidden rounded-none border-0 border-[var(--border-muted)] bg-[var(--bg)] text-[var(--text)] shadow-none sm:h-[90vh] sm:max-h-[90vh] sm:rounded-2xl sm:border sm:bg-[var(--bg-soft)] sm:shadow-2xl">
            <header className="flex items-start gap-3 border-b border-[var(--border-muted)] px-5 py-4">
              <div className="min-w-0 flex-1 space-y-1">
                <p className="text-xs tracking-wide text-gray-500 uppercase dark:text-gray-400">
                  Book
                </p>
                <h3 className="truncate text-lg leading-snug font-semibold">Loading…</h3>
              </div>
              <div className="flex flex-shrink-0 items-center gap-2">
                <button
                  type="button"
                  onClick={handleClose}
                  className="hover-action rounded-full p-2 text-gray-500 transition-colors hover:text-gray-900 dark:hover:text-gray-100"
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
              </div>
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto px-5 py-6">
              {bookError ? (
                <div className="text-sm text-red-500">{bookError}</div>
              ) : (
                <div className="text-sm text-gray-600 dark:text-gray-300">
                  Loading details from monitored database…
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    );
  }

  const titleId = `book-details-modal-title-${bookRow.id}`;

  return (
    <div
      className="modal-overlay active sm:px-6 sm:py-6"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div
        className={`details-container h-full w-full max-w-4xl sm:h-auto ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex h-full flex-col overflow-hidden rounded-none border-0 border-[var(--border-muted)] bg-[var(--bg)] text-[var(--text)] shadow-none sm:h-[90vh] sm:max-h-[90vh] sm:rounded-2xl sm:border sm:bg-[var(--bg-soft)] sm:shadow-2xl">
          {/* ── Header with animated cover thumbnail ── */}
          <header className="flex items-center gap-3 border-b border-[var(--border-muted)] px-5 py-3">
            <div
              className="flex-shrink-0 overflow-hidden transition-all duration-200 ease-out"
              style={{
                width: showHeaderThumb ? 40 : 0,
                opacity: showHeaderThumb ? 1 : 0,
                marginRight: showHeaderThumb ? 0 : -12,
              }}
            >
              {embeddedSearchBook.preview ? (
                <img
                  src={embeddedSearchBook.preview}
                  alt=""
                  className="h-[56px] w-10 rounded-md object-cover object-top shadow-sm"
                />
              ) : (
                <div className="flex h-[56px] w-10 items-center justify-center rounded-md border border-dashed border-[var(--border-muted)] bg-[var(--bg)]/60 text-[8px] text-gray-500">
                  No cover
                </div>
              )}
            </div>
            <div className="min-w-0 flex-1">
              <p className="text-xs tracking-wide text-gray-500 uppercase dark:text-gray-400">
                Book
              </p>
              <h3 id={titleId} className="truncate text-base leading-snug font-semibold">
                {embeddedSearchBook.title || 'Untitled'}
              </h3>
              {bookRow?.state === 'removed_from_provider' && (
                <span className="inline-flex items-center rounded-full bg-red-500/20 px-2 py-0.5 text-[10px] font-semibold tracking-wide text-red-700 uppercase dark:text-red-300">
                  Removed from Hardcover
                </span>
              )}
              {onAuthorClick && embeddedSearchBook.author ? (
                <button
                  type="button"
                  onClick={() => {
                    setIsClosing(true);
                    onAuthorClick(embeddedSearchBook.author!);
                    setTimeout(onClose, 50);
                  }}
                  className="truncate text-left text-sm text-emerald-600 hover:underline dark:text-emerald-400"
                  title={`Go to ${embeddedSearchBook.author}`}
                >
                  {embeddedSearchBook.author}
                </button>
              ) : (
                <p className="truncate text-sm text-gray-600 dark:text-gray-300">
                  {embeddedSearchBook.author || 'Unknown author'}
                </p>
              )}
            </div>
            <button
              type="button"
              onClick={handleClose}
              className="hover-action flex-shrink-0 rounded-full p-2 text-gray-500 transition-colors hover:text-gray-900 dark:hover:text-gray-100"
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

          {/* ── Sticky monitoring strip ── */}
          {entityId != null ? (
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-[var(--border-muted)] bg-[var(--bg)] px-5 py-2 sm:bg-[var(--bg-soft)]">
              <div className="flex items-center gap-2">
                <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
                  Available:
                </span>
                <span
                  className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${hasEbookFile ? 'bg-blue-500/20 text-blue-700 dark:text-blue-300' : 'bg-gray-500/10 text-gray-400 dark:text-gray-500'}`}
                >
                  {hasEbookFile ? (
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
                        d="m4.5 12.75 6 6 9-13.5"
                      />
                    </svg>
                  ) : (
                    <svg
                      className="h-3 w-3"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                      strokeWidth={2}
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                    </svg>
                  )}
                  eBook
                </span>
                <span
                  className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${hasAudiobookFile ? 'bg-purple-500/20 text-purple-700 dark:text-purple-300' : 'bg-gray-500/10 text-gray-400 dark:text-gray-500'}`}
                >
                  {hasAudiobookFile ? (
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
                        d="m4.5 12.75 6 6 9-13.5"
                      />
                    </svg>
                  ) : (
                    <svg
                      className="h-3 w-3"
                      fill="none"
                      stroke="currentColor"
                      viewBox="0 0 24 24"
                      strokeWidth={2}
                    >
                      <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                    </svg>
                  )}
                  Audiobook
                </span>
              </div>
              {onToggleMonitor ? (
                <div className="flex items-center gap-2">
                  <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
                    Monitoring:
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      if (!ebookMonitorLocked) {
                        setMonitorEbookUiState((prev) => !prev);
                        onToggleMonitor('ebook');
                      }
                    }}
                    disabled={ebookMonitorLocked}
                    className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium transition-colors ${ebookMonitorLocked ? 'cursor-not-allowed bg-gray-500/10 text-gray-500 opacity-80 dark:text-gray-400' : monitorEbookUiState ? 'bg-emerald-500/20 text-emerald-700 hover:bg-emerald-500/30 dark:text-emerald-300' : 'bg-gray-500/10 text-gray-500 hover:bg-gray-500/20 dark:text-gray-400'}`}
                    title={
                      ebookMonitorLocked
                        ? 'eBook already available; monitoring auto-paused'
                        : 'Toggle eBook monitoring'
                    }
                  >
                    {ebookMonitorLocked ? (
                      <svg
                        className="h-3.5 w-3.5"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                        strokeWidth={2}
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="m2.5 12.75 4 4 6-9" />
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="m10.5 12.75 4 4 7-10"
                        />
                      </svg>
                    ) : monitorEbookUiState ? (
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
                          d="m4.5 12.75 6 6 9-13.5"
                        />
                      </svg>
                    ) : (
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
                          d="M6 18 18 6M6 6l12 12"
                        />
                      </svg>
                    )}
                    eBook
                  </button>
                  <button
                    type="button"
                    onClick={() => {
                      if (!audiobookMonitorLocked) {
                        setMonitorAudiobookUiState((prev) => !prev);
                        onToggleMonitor('audiobook');
                      }
                    }}
                    disabled={audiobookMonitorLocked}
                    className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium transition-colors ${audiobookMonitorLocked ? 'cursor-not-allowed bg-gray-500/10 text-gray-500 opacity-80 dark:text-gray-400' : monitorAudiobookUiState ? 'bg-emerald-500/20 text-emerald-700 hover:bg-emerald-500/30 dark:text-emerald-300' : 'bg-gray-500/10 text-gray-500 hover:bg-gray-500/20 dark:text-gray-400'}`}
                    title={
                      audiobookMonitorLocked
                        ? 'Audiobook already available; monitoring auto-paused'
                        : 'Toggle audiobook monitoring'
                    }
                  >
                    {audiobookMonitorLocked ? (
                      <svg
                        className="h-3.5 w-3.5"
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                        strokeWidth={2}
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="m2.5 12.75 4 4 6-9" />
                        <path
                          strokeLinecap="round"
                          strokeLinejoin="round"
                          d="m10.5 12.75 4 4 7-10"
                        />
                      </svg>
                    ) : monitorAudiobookUiState ? (
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
                          d="m4.5 12.75 6 6 9-13.5"
                        />
                      </svg>
                    ) : (
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
                          d="M6 18 18 6M6 6l12 12"
                        />
                      </svg>
                    )}
                    Audiobook
                  </button>
                </div>
              ) : null}
              {onToggleHidden ? (
                <button
                  type="button"
                  onClick={onToggleHidden}
                  className={`inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs font-medium transition-colors ${hidden ? 'bg-gray-500/20 text-gray-600 hover:bg-gray-500/30 dark:text-gray-300' : 'bg-gray-500/10 text-gray-500 hover:bg-gray-500/20 dark:text-gray-400'}`}
                  title={hidden ? 'Unhide this book' : 'Hide this book from counts and lists'}
                >
                  {hidden ? (
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
                        d="M3.98 8.223A10.477 10.477 0 0 0 1.934 12C3.226 16.338 7.244 19.5 12 19.5c.993 0 1.953-.138 2.863-.395M6.228 6.228A10.451 10.451 0 0 1 12 4.5c4.756 0 8.773 3.162 10.065 7.498a10.522 10.522 0 0 1-4.293 5.774M6.228 6.228 3 3m3.228 3.228 3.65 3.65m7.894 7.894L21 21m-3.228-3.228-3.65-3.65m0 0a3 3 0 1 0-4.243-4.243m4.242 4.242L9.88 9.88"
                      />
                    </svg>
                  ) : (
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
                        d="M2.036 12.322a1.012 1.012 0 0 1 0-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178Z"
                      />
                      <path
                        strokeLinecap="round"
                        strokeLinejoin="round"
                        d="M15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0Z"
                      />
                    </svg>
                  )}
                  {hidden ? 'Hidden' : 'Hide'}
                </button>
              ) : null}
            </div>
          ) : null}

          {/* ── Tab bar (fixed by flex layout, above scroll container) ── */}
          <div className="border-b border-[var(--border-muted)] bg-[var(--bg)] px-5 sm:bg-[var(--bg-soft)]">
            <div className="relative flex gap-0.5 overflow-x-auto">
              <div
                ref={indicatorRef}
                className="absolute bottom-0 h-0.5 bg-emerald-600 transition-all duration-300 ease-out"
              />
              {(
                [
                  ['details', 'Details'],
                  ['files', 'Files'],
                  ['history', 'History'],
                  ['ebooks', 'Search eBooks'],
                  ['audiobooks', 'Search Audiobooks'],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  ref={(el) => {
                    tabRefs.current[key] = el;
                    if (el && key === tab) syncIndicator();
                  }}
                  type="button"
                  onClick={() => setTab(key)}
                  className={`border-b-2 border-transparent px-3 py-2.5 text-sm font-medium whitespace-nowrap transition-colors ${
                    tab === key
                      ? 'text-emerald-600 dark:text-emerald-400'
                      : 'text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-gray-200'
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
          </div>

          {/* ── Scrollable tab content ── */}
          <div
            ref={scrollContainerRef}
            className="min-h-0 flex-1 overflow-y-auto"
            {...swipeHandlers}
          >
            <div className="px-5 py-4">
              {/* ── Details tab ── */}
              {tab === 'details' ? (
                <div className="space-y-4">
                  {/* Cover + metadata */}
                  <div ref={coverSentinelRef} className="flex gap-4">
                    {embeddedSearchBook.preview ? (
                      <img
                        src={embeddedSearchBook.preview}
                        alt="Book cover"
                        className="h-[140px] w-24 flex-shrink-0 rounded-lg object-cover object-top shadow-md"
                      />
                    ) : (
                      <div className="flex h-[140px] w-24 flex-shrink-0 items-center justify-center rounded-lg border border-dashed border-[var(--border-muted)] bg-[var(--bg)]/60 text-[10px] text-gray-500">
                        No cover
                      </div>
                    )}
                    <div className="min-w-0 flex-1 space-y-2">
                      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-600 dark:text-gray-400">
                        {embeddedSearchBook.year ? <span>{embeddedSearchBook.year}</span> : null}
                        {embeddedSearchBook.series_name ? (
                          onNavigateToSeries ? (
                            <button
                              type="button"
                              onClick={() => onNavigateToSeries(embeddedSearchBook.series_name!)}
                              className="truncate text-left text-emerald-600 hover:underline dark:text-emerald-400"
                              title={`Go to ${embeddedSearchBook.series_name} series`}
                            >
                              {embeddedSearchBook.series_position != null ? (
                                <>
                                  #{embeddedSearchBook.series_position}
                                  {embeddedSearchBook.series_count != null
                                    ? `/${embeddedSearchBook.series_count}`
                                    : ''}{' '}
                                  in {embeddedSearchBook.series_name}
                                </>
                              ) : (
                                <>Part of {embeddedSearchBook.series_name}</>
                              )}
                            </button>
                          ) : (
                            <span className="truncate">
                              {embeddedSearchBook.series_position != null ? (
                                <>
                                  #{embeddedSearchBook.series_position}
                                  {embeddedSearchBook.series_count != null
                                    ? `/${embeddedSearchBook.series_count}`
                                    : ''}{' '}
                                  in {embeddedSearchBook.series_name}
                                </>
                              ) : (
                                <>Part of {embeddedSearchBook.series_name}</>
                              )}
                            </span>
                          )
                        ) : null}
                        {embeddedSearchBook.additional_series &&
                        embeddedSearchBook.additional_series.length > 0
                          ? embeddedSearchBook.additional_series.map(
                              (s: { name: string; position?: number; count?: number }) => {
                                const seriesKey = `${s.name}-${s.position ?? ''}`;
                                const label =
                                  s.position != null ? (
                                    <>
                                      #{s.position}
                                      {s.count != null ? `/${s.count}` : ''} in {s.name}
                                    </>
                                  ) : (
                                    <>Part of {s.name}</>
                                  );
                                return onNavigateToSeries ? (
                                  <button
                                    key={seriesKey}
                                    type="button"
                                    onClick={() => onNavigateToSeries(s.name)}
                                    className="truncate text-left text-sky-600 hover:underline dark:text-sky-400"
                                    title={`Go to ${s.name} series`}
                                  >
                                    {label}
                                  </button>
                                ) : (
                                  <span key={seriesKey} className="truncate">
                                    {label}
                                  </span>
                                );
                              },
                            )
                          : null}
                        {matchedFileTypes.length > 0 ? (
                          <span className="inline-flex items-center gap-1">
                            {matchedFileTypes.slice(0, 3).map((t) => (
                              <span
                                key={t}
                                className={`${getFormatColor(t).bg} ${getFormatColor(t).text} inline-flex items-center rounded-lg px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase`}
                              >
                                {t.toUpperCase()}
                              </span>
                            ))}
                          </span>
                        ) : null}
                      </div>
                      {displayFields.length > 0 ? (
                        <div className="grid grid-cols-1 gap-x-4 gap-y-1 text-xs text-gray-500 sm:grid-cols-2 dark:text-gray-400">
                          {displayFields.slice(0, 8).map((field) => (
                            <div key={`${field.label}:${field.value}`} className="min-w-0 truncate">
                              <span className="font-medium text-gray-600 dark:text-gray-300">
                                {field.label}:
                              </span>{' '}
                              <span>{field.value}</span>
                            </div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </div>

                  {/* Description */}
                  {embeddedSearchBook.description ? (
                    <div className="text-sm text-gray-600 dark:text-gray-400">
                      <p
                        ref={(el) => setDescriptionEl(el)}
                        className={descriptionExpanded ? '' : 'line-clamp-3'}
                      >
                        {embeddedSearchBook.description}
                      </p>
                      {descriptionOverflows ? (
                        <button
                          type="button"
                          onClick={() => setDescriptionExpanded((v) => !v)}
                          className="mt-1 flex items-center gap-1 text-xs font-medium text-emerald-600 hover:underline dark:text-emerald-400"
                        >
                          {descriptionExpanded ? 'Show less' : 'Show more'}
                          <svg
                            className={`h-3 w-3 transition-transform duration-200 ${descriptionExpanded ? '-rotate-90' : 'rotate-90'}`}
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                            strokeWidth={2}
                          >
                            <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                          </svg>
                        </button>
                      ) : null}
                    </div>
                  ) : null}

                  {/* Genres & Release date */}
                  {genresSummary || releaseDateSummary || (onSetReleaseDate && bookRow) ? (
                    <div className="space-y-1 text-xs text-gray-500 dark:text-gray-400">
                      {genresSummary ? (
                        <div className="min-w-0 truncate">
                          <span className="font-medium text-gray-600 dark:text-gray-300">
                            Genres:
                          </span>{' '}
                          <span>{genresSummary}</span>
                        </div>
                      ) : null}
                      {releaseDateSummary ? (
                        <div className="flex min-w-0 items-center gap-1.5 truncate">
                          <span className="font-medium text-gray-600 dark:text-gray-300">
                            Release date:
                          </span>{' '}
                          <span>{releaseDateSummary}</span>
                          {onSetReleaseDate && bookRow ? (
                            <button
                              type="button"
                              onClick={() => onSetReleaseDate(bookRow)}
                              className="ml-0.5 inline-flex items-center text-blue-600 hover:text-blue-700 dark:text-blue-400 dark:hover:text-blue-300"
                              title="Search for release date"
                            >
                              <svg
                                className="h-3.5 w-3.5"
                                fill="none"
                                stroke="currentColor"
                                viewBox="0 0 24 24"
                                strokeWidth={1.5}
                              >
                                <path
                                  strokeLinecap="round"
                                  strokeLinejoin="round"
                                  d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
                                />
                              </svg>
                            </button>
                          ) : null}
                        </div>
                      ) : onSetReleaseDate && bookRow ? (
                        <button
                          type="button"
                          onClick={() => onSetReleaseDate(bookRow)}
                          className="inline-flex items-center gap-1.5 text-xs font-medium text-blue-600 hover:underline dark:text-blue-400"
                        >
                          <svg
                            className="h-3.5 w-3.5"
                            fill="none"
                            stroke="currentColor"
                            viewBox="0 0 24 24"
                            strokeWidth={1.5}
                          >
                            <path
                              strokeLinecap="round"
                              strokeLinejoin="round"
                              d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z"
                            />
                          </svg>
                          Set release date
                        </button>
                      ) : null}
                    </div>
                  ) : null}

                  {/* Paths */}
                  {foundEbookPath || foundAudiobookPath || latestDownloaderFinalPath ? (
                    <div className="space-y-1 text-xs text-gray-500 dark:text-gray-400">
                      <div className="font-medium text-gray-600 dark:text-gray-300">Paths</div>
                      {foundEbookPath ? (
                        <div className="min-w-0 break-all">
                          <span className="font-medium text-gray-600 dark:text-gray-300">
                            Found on disk (eBook):
                          </span>{' '}
                          <span>{foundEbookPath}</span>
                        </div>
                      ) : null}
                      {foundAudiobookPath ? (
                        <div className="min-w-0 break-all">
                          <span className="font-medium text-gray-600 dark:text-gray-300">
                            Found on disk (Audiobook):
                          </span>{' '}
                          <span>{foundAudiobookPath}</span>
                        </div>
                      ) : null}
                      {latestDownloaderFinalPath ? (
                        <div className="min-w-0 break-all">
                          <span className="font-medium text-gray-600 dark:text-gray-300">
                            Downloader moved to:
                          </span>{' '}
                          <span>{latestDownloaderFinalPath}</span>
                        </div>
                      ) : null}
                    </div>
                  ) : null}

                  {/* ISBN & source link */}
                  <div className="flex flex-wrap items-center gap-3 text-xs">
                    {embeddedSearchBook.isbn_13 || embeddedSearchBook.isbn_10 ? (
                      <span className="text-gray-500 dark:text-gray-400">
                        ISBN: {embeddedSearchBook.isbn_13 || embeddedSearchBook.isbn_10}
                      </span>
                    ) : null}
                    {embeddedSearchBook.source_url ? (
                      <a
                        href={embeddedSearchBook.source_url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-emerald-600 hover:underline dark:text-emerald-400"
                      >
                        View source
                        <svg
                          className="h-3 w-3"
                          fill="none"
                          stroke="currentColor"
                          viewBox="0 0 24 24"
                        >
                          <path
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            strokeWidth={2}
                            d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
                          />
                        </svg>
                      </a>
                    ) : null}
                  </div>
                </div>
              ) : tab === 'files' ? (
                /* ── Files tab ── */
                <div className="space-y-3">
                  <div className="overflow-hidden rounded-2xl border border-[var(--border-muted)]">
                    <div className="bg-black/5 px-4 py-3 text-xs tracking-wide text-gray-500 uppercase dark:bg-white/5 dark:text-gray-400">
                      Matched files
                    </div>
                    <div className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
                      {matchedFiles.length === 0 ? (
                        <div className="px-4 py-4 text-sm text-gray-500 dark:text-gray-400">
                          No files matched to this book yet.
                        </div>
                      ) : (
                        matchedFiles.map((f) => {
                          const isAbs = f.source === 'audiobookshelf';
                          const isBooklore = f.source === 'booklore';
                          const formatLabel = f.ext
                            ? f.ext.toUpperCase()
                            : f.file_type
                              ? f.file_type.toUpperCase()
                              : 'FILE';
                          const badgeKey = f.ext || f.file_type || '';
                          const path = f.path || '';
                          const lastSlash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
                          const fileName = lastSlash >= 0 ? path.slice(lastSlash + 1) : path;
                          const dirPath = lastSlash >= 0 ? path.slice(0, lastSlash + 1) : '';
                          const isManual = !!f.manual_override;
                          const metaParts = [
                            typeof f.confidence === 'number'
                              ? `${(f.confidence * 100).toFixed(0)}%`
                              : null,
                            isAbs ? 'from AudioBookShelf' : isBooklore ? 'from Booklore' : null,
                          ].filter(Boolean);
                          const evidence = f.evidence ?? null;
                          const evidenceExpanded = expandedEvidenceIds.has(f.id);
                          const toggleEvidence = () => {
                            setExpandedEvidenceIds((prev) => {
                              const next = new Set(prev);
                              if (next.has(f.id)) next.delete(f.id);
                              else next.add(f.id);
                              return next;
                            });
                          };
                          return (
                            <div key={f.id} className="px-4 py-3">
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0 flex-1">
                                  <div
                                    className="text-sm break-words text-gray-900 dark:text-gray-100"
                                    title={f.path}
                                  >
                                    {fileName}
                                    {isManual ? (
                                      <span
                                        className="ml-2 rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] tracking-wide text-amber-700 uppercase dark:text-amber-300"
                                        title="Manually attributed — future scans will not change this match"
                                      >
                                        ✋ manual
                                      </span>
                                    ) : null}
                                  </div>
                                  {dirPath ? (
                                    <div
                                      className="mt-0.5 text-xs break-all text-gray-500 dark:text-gray-400"
                                      title={f.path}
                                    >
                                      {dirPath}
                                    </div>
                                  ) : null}
                                  {metaParts.length > 0 ? (
                                    <div className="mt-0.5 flex items-center gap-2 truncate text-xs text-gray-500 dark:text-gray-400">
                                      <span>{metaParts.join(' · ')}</span>
                                      {evidence ? (
                                        <button
                                          type="button"
                                          onClick={toggleEvidence}
                                          className="text-blue-600 hover:underline focus:outline-none dark:text-blue-400"
                                        >
                                          {evidenceExpanded ? 'hide why' : 'why?'}
                                        </button>
                                      ) : null}
                                      <button
                                        type="button"
                                        onClick={() =>
                                          setFixMatchTarget({ mode: 'byFile', fileId: f.id })
                                        }
                                        className="text-blue-600 hover:underline focus:outline-none dark:text-blue-400"
                                        title="Pick the correct book for this file"
                                      >
                                        fix match
                                      </button>
                                    </div>
                                  ) : null}
                                  {evidence && evidenceExpanded ? (
                                    <EvidencePanel evidence={evidence} />
                                  ) : null}
                                </div>
                                {badgeKey ? (
                                  <span
                                    className={`${getFormatColor(badgeKey).bg} ${getFormatColor(badgeKey).text} inline-flex flex-shrink-0 items-center rounded-lg px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase`}
                                  >
                                    {formatLabel}
                                  </span>
                                ) : null}
                              </div>
                            </div>
                          );
                        })
                      )}
                    </div>
                    {provider && providerBookId && (!hasEbookFile || !hasAudiobookFile) ? (
                      <div className="flex flex-wrap items-center gap-2 border-t border-[var(--border-muted)] bg-black/5 px-4 py-3 dark:bg-white/5">
                        <span className="text-xs text-gray-500 dark:text-gray-400">
                          Manually link a file:
                        </span>
                        {!hasEbookFile ? (
                          <button
                            type="button"
                            onClick={() =>
                              setFixMatchTarget({
                                mode: 'byBook',
                                provider,
                                providerBookId,
                                fileType: 'ebook',
                              })
                            }
                            className="rounded-lg border border-[var(--border-muted)] px-2 py-1 text-xs text-blue-700 hover:bg-blue-500/10 dark:text-blue-300"
                          >
                            + Ebook
                          </button>
                        ) : null}
                        {!hasAudiobookFile ? (
                          <button
                            type="button"
                            onClick={() =>
                              setFixMatchTarget({
                                mode: 'byBook',
                                provider,
                                providerBookId,
                                fileType: 'audiobook',
                              })
                            }
                            className="rounded-lg border border-[var(--border-muted)] px-2 py-1 text-xs text-purple-700 hover:bg-purple-500/10 dark:text-purple-300"
                          >
                            + Audiobook
                          </button>
                        ) : null}
                      </div>
                    ) : null}
                  </div>

                  {/* ── Possible Candidates section ── */}
                  {candidateFiles.length > 0 && entityId != null ? (
                    <div className="overflow-hidden rounded-2xl border border-amber-500/40">
                      <div className="flex items-center justify-between bg-amber-500/10 px-4 py-3 text-xs tracking-wide text-amber-700 uppercase dark:text-amber-300">
                        <span>Possible candidates</span>
                        <span className="text-[10px] tracking-normal text-amber-700/80 normal-case dark:text-amber-300/80">
                          Review and accept or reject
                        </span>
                      </div>
                      <div className="divide-y divide-amber-500/20">
                        {candidateFiles.map((f) => {
                          const isAbs = f.source === 'audiobookshelf';
                          const isBooklore = f.source === 'booklore';
                          const formatLabel = f.ext
                            ? f.ext.toUpperCase()
                            : f.file_type
                              ? f.file_type.toUpperCase()
                              : 'FILE';
                          const badgeKey = f.ext || f.file_type || '';
                          const path = f.path || '';
                          const lastSlash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
                          const fileName = lastSlash >= 0 ? path.slice(lastSlash + 1) : path;
                          const dirPath = lastSlash >= 0 ? path.slice(0, lastSlash + 1) : '';
                          const metaParts = [
                            typeof f.confidence === 'number'
                              ? `${(f.confidence * 100).toFixed(0)}%`
                              : null,
                            isAbs ? 'from AudioBookShelf' : isBooklore ? 'from Booklore' : null,
                          ].filter(Boolean);
                          const evidence = f.evidence ?? null;
                          const evidenceExpanded = expandedEvidenceIds.has(f.id);
                          const toggleEvidence = () => {
                            setExpandedEvidenceIds((prev) => {
                              const next = new Set(prev);
                              if (next.has(f.id)) next.delete(f.id);
                              else next.add(f.id);
                              return next;
                            });
                          };
                          const isPending = pendingCandidateAction.has(f.id);
                          const handleAccept = async () => {
                            if (isPending) return;
                            setPendingCandidateAction((prev) => new Set(prev).add(f.id));
                            setCandidateActionError((prev) => (prev?.id === f.id ? null : prev));
                            try {
                              await promoteCandidate(entityId, f.id);
                              setFilesReloadKey((k) => k + 1);
                              onBookModified?.();
                            } catch (error) {
                              const message =
                                error instanceof Error && error.message
                                  ? error.message
                                  : 'Failed to accept candidate.';
                              setCandidateActionError({ id: f.id, message });
                            } finally {
                              setPendingCandidateAction((prev) => {
                                const next = new Set(prev);
                                next.delete(f.id);
                                return next;
                              });
                            }
                          };
                          const handleReject = async () => {
                            if (isPending) return;
                            setPendingCandidateAction((prev) => new Set(prev).add(f.id));
                            setCandidateActionError((prev) => (prev?.id === f.id ? null : prev));
                            try {
                              await rejectCandidate(entityId, f.id);
                              setFilesReloadKey((k) => k + 1);
                            } catch (error) {
                              const message =
                                error instanceof Error && error.message
                                  ? error.message
                                  : 'Failed to reject candidate.';
                              setCandidateActionError({ id: f.id, message });
                            } finally {
                              setPendingCandidateAction((prev) => {
                                const next = new Set(prev);
                                next.delete(f.id);
                                return next;
                              });
                            }
                          };
                          return (
                            <div key={f.id} className="px-4 py-3">
                              <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0 flex-1">
                                  <div
                                    className="text-sm break-words text-gray-900 dark:text-gray-100"
                                    title={f.path}
                                  >
                                    {fileName}
                                  </div>
                                  {dirPath ? (
                                    <div
                                      className="mt-0.5 text-xs break-all text-gray-500 dark:text-gray-400"
                                      title={f.path}
                                    >
                                      {dirPath}
                                    </div>
                                  ) : null}
                                  <div className="mt-1 flex flex-wrap items-center gap-2">
                                    {metaParts.length > 0 ? (
                                      <span className="text-xs text-gray-500 dark:text-gray-400">
                                        {metaParts.join(' · ')}
                                      </span>
                                    ) : null}
                                    {evidence ? (
                                      <button
                                        type="button"
                                        onClick={toggleEvidence}
                                        className="text-xs text-blue-600 hover:underline focus:outline-none dark:text-blue-400"
                                      >
                                        {evidenceExpanded ? 'hide why' : 'why?'}
                                      </button>
                                    ) : null}
                                    <button
                                      type="button"
                                      onClick={handleAccept}
                                      disabled={isPending}
                                      className="rounded-md border border-green-500/40 px-2 py-0.5 text-xs text-green-700 hover:bg-green-500/10 disabled:opacity-50 dark:text-green-300"
                                    >
                                      Accept
                                    </button>
                                    <button
                                      type="button"
                                      onClick={handleReject}
                                      disabled={isPending}
                                      className="rounded-md border border-red-500/40 px-2 py-0.5 text-xs text-red-700 hover:bg-red-500/10 disabled:opacity-50 dark:text-red-300"
                                    >
                                      Reject
                                    </button>
                                  </div>
                                  {candidateActionError?.id === f.id ? (
                                    <div className="mt-1 text-xs text-red-600 dark:text-red-400">
                                      {candidateActionError.message}
                                    </div>
                                  ) : null}
                                  {evidence && evidenceExpanded ? (
                                    <EvidencePanel evidence={evidence} />
                                  ) : null}
                                </div>
                                {badgeKey ? (
                                  <span
                                    className={`${getFormatColor(badgeKey).bg} ${getFormatColor(badgeKey).text} inline-flex flex-shrink-0 items-center rounded-lg px-2 py-0.5 text-[10px] font-semibold tracking-wide uppercase`}
                                  >
                                    {formatLabel}
                                  </span>
                                ) : null}
                              </div>
                            </div>
                          );
                        })}
                      </div>
                    </div>
                  ) : null}
                </div>
              ) : tab === 'history' ? (
                /* ── History tab — unified events timeline ── */
                <div className="space-y-2">
                  {eventsLoading ? (
                    <div className="px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                      Loading history…
                    </div>
                  ) : eventsError ? (
                    <div className="px-4 py-8 text-center text-sm text-red-500">{eventsError}</div>
                  ) : eventRows.length === 0 ? (
                    <div className="px-4 py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                      No activity recorded yet.
                    </div>
                  ) : (
                    <div className="divide-y divide-gray-200/60 overflow-hidden rounded-2xl border border-[var(--border-muted)] dark:divide-gray-800/60">
                      {eventRows.map((ev) => (
                        <MonitoredEventRow key={ev.id} event={ev} />
                      ))}
                    </div>
                  )}
                </div>
              ) : tab === 'ebooks' ? (
                renderEmbeddedSearch(embeddedSearchBook, 'ebook', entityId)
              ) : (
                renderEmbeddedSearch(embeddedSearchBook, 'audiobook', entityId)
              )}
            </div>
          </div>
        </div>
      </div>
      {fixMatchTarget && entityId != null ? (
        <FixMatchModal
          entityId={entityId}
          target={fixMatchTarget}
          onClose={() => setFixMatchTarget(null)}
          onApplied={() => {
            setFilesReloadKey((k) => k + 1);
            onBookModified?.();
          }}
        />
      ) : null}
    </div>
  );
};
