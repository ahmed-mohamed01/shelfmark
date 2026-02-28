import { useCallback, useEffect, useMemo, useState } from 'react';
import { Book, ContentType } from '../types';
import { getMetadataBookInfo } from '../services/api';
import {
  listMonitoredBookDownloadHistory,
  MonitoredBookAttemptHistoryRow,
  MonitoredBookDownloadHistoryRow,
  MonitoredBookFileRow,
} from '../services/monitoredApi';
import { getFormatColor } from '../utils/colorMaps';

interface BookDetailsModalProps {
  book: Book | null;
  files: MonitoredBookFileRow[];
  monitoredEntityId?: number | null;
  onClose: () => void;
  onOpenSearch: (contentType: ContentType) => void;
  monitorEbook?: boolean;
  monitorAudiobook?: boolean;
  onToggleMonitor?: (type: 'ebook' | 'audiobook' | 'both') => void;
  onNavigateToSeries?: (seriesName: string) => void;
}

type TabKey = 'files' | 'ebooks' | 'audiobooks';

const isEnabledFlag = (value: unknown): boolean => value === true || value === 1;

export const BookDetailsModal = ({ book, files, monitoredEntityId, onClose, onOpenSearch, monitorEbook, monitorAudiobook, onToggleMonitor, onNavigateToSeries }: BookDetailsModalProps) => {
  const [isClosing, setIsClosing] = useState(false);
  const [tab, setTab] = useState<TabKey>('files');

  const [enrichedBook, setEnrichedBook] = useState<Book | null>(null);
  const [historyRows, setHistoryRows] = useState<MonitoredBookDownloadHistoryRow[]>([]);
  const [attemptHistoryRows, setAttemptHistoryRows] = useState<MonitoredBookAttemptHistoryRow[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [attemptHistoryOpen, setAttemptHistoryOpen] = useState(false);

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

  useEffect(() => {
    if (book) {
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = previousOverflow;
      };
    }
  }, [book]);

  useEffect(() => {
    if (!book || !monitoredEntityId) {
      setHistoryRows([]);
      setAttemptHistoryRows([]);
      setHistoryError(null);
      setHistoryLoading(false);
      return;
    }

    const provider = (book.provider || '').trim();
    const providerId = (book.provider_id || '').trim();
    if (!provider || !providerId) {
      setHistoryRows([]);
      setAttemptHistoryRows([]);
      setHistoryError(null);
      setHistoryLoading(false);
      return;
    }

    let cancelled = false;
    setHistoryLoading(true);
    setHistoryError(null);
    void (async () => {
      try {
        const resp = await listMonitoredBookDownloadHistory(monitoredEntityId, provider, providerId, 30);
        if (cancelled) return;
        setHistoryRows(resp.history || []);
        setAttemptHistoryRows(resp.attempt_history || []);
      } catch (error) {
        if (cancelled) return;
        const message = error instanceof Error ? error.message : 'Failed to load history';
        setHistoryError(message);
        setHistoryRows([]);
        setAttemptHistoryRows([]);
      } finally {
        if (!cancelled) {
          setHistoryLoading(false);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [book?.id, book?.provider, book?.provider_id, monitoredEntityId]);

  useEffect(() => {
    if (book) setTab('files');
  }, [book?.id]);

  useEffect(() => {
    if (!book) {
      setEnrichedBook(null);
      setHistoryRows([]);
      setAttemptHistoryRows([]);
      setHistoryError(null);
      setHistoryLoading(false);
      return;
    }

    setEnrichedBook(book);

    const provider = book.provider || '';
    const providerId = book.provider_id || '';
    if (!provider || !providerId) {
      return;
    }

    let cancelled = false;
    void (async () => {
      try {
        const full = await getMetadataBookInfo(provider, providerId);
        if (cancelled) return;
        setEnrichedBook((current) => {
          if (!current) return full;
          return {
            ...current,
            publisher: full.publisher ?? current.publisher,
            release_date: full.release_date ?? current.release_date,
            language: full.language ?? current.language,
            genres: full.genres ?? current.genres,
            description: full.description ?? current.description,
            display_fields: full.display_fields ?? current.display_fields,
            source_url: full.source_url ?? current.source_url,
            isbn_10: full.isbn_10 ?? current.isbn_10,
            isbn_13: full.isbn_13 ?? current.isbn_13,
            series_name: full.series_name ?? current.series_name,
            series_position: full.series_position ?? current.series_position,
            series_count: full.series_count ?? current.series_count,
          };
        });
      } catch {
        // best-effort
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [book]);

  useEffect(() => {
    setDescriptionExpanded(false);
    setDescriptionOverflows(false);
  }, [enrichedBook?.id]);

  useEffect(() => {
    if (!descriptionEl || descriptionExpanded) return;
    setDescriptionOverflows(descriptionEl.scrollHeight > descriptionEl.clientHeight);
  }, [descriptionEl, descriptionExpanded, enrichedBook?.description]);

  const matchedFileTypes = useMemo(() => {
    const set = new Set<string>();
    for (const f of files) {
      const t = typeof f.file_type === 'string' ? f.file_type.trim().toLowerCase() : '';
      if (t) set.add(t);
    }
    return Array.from(set).sort((a, b) => a.localeCompare(b));
  }, [files]);

  const hasEbookFile = useMemo(() => {
    return isEnabledFlag(enrichedBook?.has_ebook_available);
  }, [enrichedBook?.has_ebook_available]);

  const hasAudiobookFile = useMemo(() => {
    return isEnabledFlag(enrichedBook?.has_audiobook_available);
  }, [enrichedBook?.has_audiobook_available]);

  const foundEbookPath = useMemo(() => {
    const path = (enrichedBook?.ebook_path || '').trim();
    return path || null;
  }, [enrichedBook?.ebook_path]);

  const foundAudiobookPath = useMemo(() => {
    const path = (enrichedBook?.audiobook_path || '').trim();
    return path || null;
  }, [enrichedBook?.audiobook_path]);

  const latestDownloaderFinalPath = useMemo(() => {
    for (const row of historyRows) {
      const path = (row.final_path || '').trim();
      if (path) {
        return path;
      }
    }
    return null;
  }, [historyRows]);

  const ebookMonitorLocked = hasEbookFile;
  const audiobookMonitorLocked = hasAudiobookFile;

  const genresSummary = useMemo(() => {
    if (!Array.isArray(enrichedBook?.genres) || enrichedBook.genres.length === 0) {
      return null;
    }
    return enrichedBook.genres.slice(0, 5).join(', ');
  }, [enrichedBook?.genres]);

  const releaseDateSummary = useMemo(() => {
    if (typeof enrichedBook?.release_date === 'string' && enrichedBook.release_date.trim()) {
      return enrichedBook.release_date.trim();
    }

    if (!Array.isArray(enrichedBook?.display_fields)) {
      return enrichedBook?.year || null;
    }

    for (const field of enrichedBook.display_fields) {
      if (!field || typeof field.label !== 'string' || typeof field.value !== 'string') {
        continue;
      }
      const label = field.label.trim().toLowerCase();
      if (!label) continue;
      const isReleaseDateLabel =
        label.includes('released') ||
        label.includes('release date') ||
        label.includes('publish date') ||
        label.includes('publication date') ||
        label === 'release' ||
        label === 'published' ||
        label === 'publication';
      if (!isReleaseDateLabel) continue;

      const value = field.value.trim();
      if (value) return value;
    }

    return enrichedBook?.year || null;
  }, [enrichedBook?.release_date, enrichedBook?.display_fields, enrichedBook?.year]);

  const displayFields = useMemo(() => {
    const fields: Array<{ label: string; value: string }> = [];

    if (enrichedBook?.publisher) {
      fields.push({ label: 'Publisher', value: enrichedBook.publisher });
    }

    if (enrichedBook?.language) {
      fields.push({ label: 'Language', value: enrichedBook.language });
    }

    if (Array.isArray(enrichedBook?.display_fields)) {
      for (const field of enrichedBook.display_fields) {
        if (!field || typeof field.label !== 'string' || typeof field.value !== 'string') {
          continue;
        }
        const label = field.label.trim();
        const value = field.value.trim();
        if (!label || !value) {
          continue;
        }
        const lowerLabel = label.toLowerCase();
        const isGenresLabel = lowerLabel === 'genres' || lowerLabel === 'genre';
        const isReleaseDateLabel =
          (lowerLabel.includes('release') || lowerLabel.includes('publish') || lowerLabel.includes('publication')) &&
          lowerLabel.includes('date');
        if (isGenresLabel || isReleaseDateLabel) {
          continue;
        }
        fields.push({ label, value });
      }
    }

    const seen = new Set<string>();
    return fields.filter((field) => {
      const key = `${field.label.toLowerCase()}::${field.value.toLowerCase()}`;
      if (seen.has(key)) {
        return false;
      }
      seen.add(key);
      return true;
    });
  }, [enrichedBook?.publisher, enrichedBook?.language, enrichedBook?.display_fields]);

  if (!book && !isClosing) return null;
  if (!book) return null;
  if (!enrichedBook) return null;

  const titleId = `book-details-modal-title-${enrichedBook.id}`;
  const formatHistoryDate = (value?: string | null): string => {
    if (!value) return 'Unknown date';
    const parsed = new Date(value);
    if (Number.isNaN(parsed.getTime())) return value;
    return parsed.toLocaleString();
  };

  const renderAttemptStatusBadge = (status: string) => {
    const normalized = (status || '').trim().toLowerCase();
    const statusClass =
      normalized === 'queued'
        ? 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300'
        : normalized === 'download_failed' || normalized === 'error'
          ? 'bg-red-500/20 text-red-700 dark:text-red-300'
          : normalized === 'below_cutoff' || normalized === 'no_match' || normalized === 'not_released'
            ? 'bg-amber-500/20 text-amber-700 dark:text-amber-300'
            : 'bg-gray-500/20 text-gray-700 dark:text-gray-300';
    return (
      <span className={`inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-semibold tracking-wide uppercase ${statusClass}`}>
        {normalized || 'unknown'}
      </span>
    );
  };

  return (
    <div
      className="modal-overlay active sm:px-6 sm:py-6"
      onClick={(e) => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div
        className={`details-container w-full max-w-4xl h-full sm:h-auto ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex h-full sm:h-[90vh] sm:max-h-[90vh] flex-col overflow-hidden rounded-none sm:rounded-2xl border-0 sm:border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-none sm:shadow-2xl">
          <header className="flex items-start gap-3 border-b border-[var(--border-muted)] px-5 py-4">
            <div className="flex-1 space-y-1 min-w-0">
              <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Book</p>
              <h3 id={titleId} className="text-lg font-semibold leading-snug truncate">
                {enrichedBook.title || 'Untitled'}
              </h3>
              <p className="text-sm text-gray-600 dark:text-gray-300 truncate">{enrichedBook.author || 'Unknown author'}</p>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button
                type="button"
                onClick={handleClose}
                className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
                aria-label="Close"
              >
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </div>
          </header>

          <div className="flex-1 min-h-0 overflow-y-auto">
            <div className="flex gap-4 px-5 py-4 border-b border-[var(--border-muted)]">
              {enrichedBook.preview ? (
                <img
                  src={enrichedBook.preview}
                  alt="Book cover"
                  className="rounded-lg shadow-md object-cover object-top flex-shrink-0 w-20 h-[120px]"
                />
              ) : (
                <div className="rounded-lg border border-dashed border-[var(--border-muted)] bg-[var(--bg)]/60 flex items-center justify-center text-[10px] text-gray-500 flex-shrink-0 w-20 h-[120px]">
                  No cover
                </div>
              )}

              <div className="flex-1 min-w-0 space-y-2">
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-gray-600 dark:text-gray-400">
                  {enrichedBook.year ? <span>{enrichedBook.year}</span> : null}
                  {enrichedBook.series_name ? (
                    onNavigateToSeries ? (
                      <button
                        type="button"
                        onClick={() => onNavigateToSeries(enrichedBook.series_name!)}
                        className="truncate text-emerald-600 dark:text-emerald-400 hover:underline text-left"
                        title={`Go to ${enrichedBook.series_name} series`}
                      >
                        {enrichedBook.series_position != null ? (
                          <>#{ enrichedBook.series_position}{enrichedBook.series_count != null ? `/${enrichedBook.series_count}` : ''} in {enrichedBook.series_name}</>
                        ) : (
                          <>Part of {enrichedBook.series_name}</>
                        )}
                      </button>
                    ) : (
                      <span className="truncate">
                        {enrichedBook.series_position != null ? (
                          <>#{ enrichedBook.series_position}{enrichedBook.series_count != null ? `/${enrichedBook.series_count}` : ''} in {enrichedBook.series_name}</>
                        ) : (
                          <>Part of {enrichedBook.series_name}</>
                        )}
                      </span>
                    )
                  ) : null}
                  {enrichedBook.additional_series && enrichedBook.additional_series.length > 0 ? (
                    enrichedBook.additional_series.map((s) => {
                      const seriesKey = `${s.name}-${s.position ?? ''}`;
                      const label = s.position != null ? (
                        <>#{ s.position}{s.count != null ? `/${s.count}` : ''} in {s.name}</>
                      ) : (
                        <>Part of {s.name}</>
                      );
                      return onNavigateToSeries ? (
                        <button
                          key={seriesKey}
                          type="button"
                          onClick={() => onNavigateToSeries(s.name)}
                          className="truncate text-sky-600 dark:text-sky-400 hover:underline text-left"
                          title={`Go to ${s.name} series`}
                        >
                          {label}
                        </button>
                      ) : (
                        <span key={seriesKey} className="truncate">{label}</span>
                      );
                    })
                  ) : null}
                  {matchedFileTypes.length > 0 ? (
                    <span className="inline-flex items-center gap-1">
                      {matchedFileTypes.slice(0, 3).map((t) => (
                        <span
                          key={t}
                          className={`${getFormatColor(t).bg} ${getFormatColor(t).text} inline-flex items-center px-2 py-0.5 rounded-lg text-[10px] font-semibold tracking-wide uppercase`}
                        >
                          {t.toUpperCase()}
                        </span>
                      ))}
                    </span>
                  ) : (
                    <span className="text-gray-500 dark:text-gray-400">No matched files</span>
                  )}
                </div>

                {enrichedBook.description ? (
                  <div className="text-sm text-gray-600 dark:text-gray-400 relative">
                    <p
                      ref={(el) => setDescriptionEl(el)}
                      className={descriptionExpanded ? '' : 'line-clamp-3'}
                    >
                      {enrichedBook.description}
                      {descriptionExpanded && descriptionOverflows ? (
                        <>
                          {' '}
                          <button
                            type="button"
                            onClick={() => setDescriptionExpanded(false)}
                            className="text-emerald-600 dark:text-emerald-400 hover:underline font-medium inline"
                          >
                            Show less
                          </button>
                        </>
                      ) : null}
                    </p>
                    {!descriptionExpanded && descriptionOverflows ? (
                      <button
                        type="button"
                        onClick={() => setDescriptionExpanded(true)}
                        className="absolute bottom-0 right-0 text-emerald-600 dark:text-emerald-400 hover:underline font-medium pl-8 bg-gradient-to-r from-transparent via-[var(--bg)] to-[var(--bg)] sm:via-[var(--bg-soft)] sm:to-[var(--bg-soft)]"
                      >
                        more
                      </button>
                    ) : null}
                  </div>
                ) : null}

                {(genresSummary || releaseDateSummary) ? (
                  <div className="space-y-1 text-xs text-gray-500 dark:text-gray-400">
                    {genresSummary ? (
                      <div className="min-w-0 truncate">
                        <span className="font-medium text-gray-600 dark:text-gray-300">Genres:</span>{' '}
                        <span>{genresSummary}</span>
                      </div>
                    ) : null}
                    {releaseDateSummary ? (
                      <div className="min-w-0 truncate">
                        <span className="font-medium text-gray-600 dark:text-gray-300">Release date:</span>{' '}
                        <span>{releaseDateSummary}</span>
                      </div>
                    ) : null}
                  </div>
                ) : null}

                {displayFields.length > 0 ? (
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1 text-xs text-gray-500 dark:text-gray-400">
                    {displayFields.slice(0, 8).map((field) => (
                      <div key={`${field.label}:${field.value}`} className="min-w-0 truncate">
                        <span className="font-medium text-gray-600 dark:text-gray-300">{field.label}:</span>{' '}
                        <span>{field.value}</span>
                      </div>
                    ))}
                  </div>
                ) : null}

                {(foundEbookPath || foundAudiobookPath || latestDownloaderFinalPath) ? (
                  <div className="space-y-1 text-xs text-gray-500 dark:text-gray-400">
                    <div className="font-medium text-gray-600 dark:text-gray-300">Paths</div>
                    {foundEbookPath ? (
                      <div className="min-w-0 break-all">
                        <span className="font-medium text-gray-600 dark:text-gray-300">Found on disk (eBook):</span>{' '}
                        <span>{foundEbookPath}</span>
                      </div>
                    ) : null}
                    {foundAudiobookPath ? (
                      <div className="min-w-0 break-all">
                        <span className="font-medium text-gray-600 dark:text-gray-300">Found on disk (Audiobook):</span>{' '}
                        <span>{foundAudiobookPath}</span>
                      </div>
                    ) : null}
                    {latestDownloaderFinalPath ? (
                      <div className="min-w-0 break-all">
                        <span className="font-medium text-gray-600 dark:text-gray-300">Downloader moved to:</span>{' '}
                        <span>{latestDownloaderFinalPath}</span>
                      </div>
                    ) : null}
                  </div>
                ) : null}

                <div className="flex flex-wrap items-center gap-3 text-xs">
                  {(enrichedBook.isbn_13 || enrichedBook.isbn_10) ? (
                    <span className="text-gray-500 dark:text-gray-400">ISBN: {enrichedBook.isbn_13 || enrichedBook.isbn_10}</span>
                  ) : null}
                  {enrichedBook.source_url ? (
                    <a
                      href={enrichedBook.source_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 hover:underline"
                    >
                      View source
                      <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14" />
                      </svg>
                    </a>
                  ) : null}
                </div>

                {monitoredEntityId ? (
                  <div className="flex flex-wrap items-center gap-x-4 gap-y-2 pt-2 border-t border-[var(--border-muted)]">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-medium text-gray-500 dark:text-gray-400">Available:</span>
                      <span
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                          hasEbookFile
                            ? 'bg-blue-500/20 text-blue-700 dark:text-blue-300'
                            : 'bg-gray-500/10 text-gray-400 dark:text-gray-500'
                        }`}
                      >
                        {hasEbookFile ? (
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                          </svg>
                        ) : (
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                          </svg>
                        )}
                        eBook
                      </span>
                      <span
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium ${
                          hasAudiobookFile
                            ? 'bg-purple-500/20 text-purple-700 dark:text-purple-300'
                            : 'bg-gray-500/10 text-gray-400 dark:text-gray-500'
                        }`}
                      >
                        {hasAudiobookFile ? (
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                          </svg>
                        ) : (
                          <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                          </svg>
                        )}
                        Audiobook
                      </span>
                    </div>
                    {onToggleMonitor ? (
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-medium text-gray-500 dark:text-gray-400">Monitoring:</span>
                        <button
                          type="button"
                          onClick={() => {
                            if (!ebookMonitorLocked) {
                              onToggleMonitor('ebook');
                            }
                          }}
                          disabled={ebookMonitorLocked}
                          className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-medium transition-colors ${
                            ebookMonitorLocked
                              ? 'bg-gray-500/10 text-gray-500 dark:text-gray-400 cursor-not-allowed opacity-80'
                              : monitorEbook
                                ? 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/30'
                                : 'bg-gray-500/10 text-gray-500 dark:text-gray-400 hover:bg-gray-500/20'
                          }`}
                          title={ebookMonitorLocked ? 'eBook already available; monitoring auto-paused' : 'Toggle eBook monitoring'}
                        >
                          {ebookMonitorLocked ? (
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="m2.5 12.75 4 4 6-9" />
                              <path strokeLinecap="round" strokeLinejoin="round" d="m10.5 12.75 4 4 7-10" />
                            </svg>
                          ) : monitorEbook ? (
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                            </svg>
                          ) : (
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                            </svg>
                          )}
                          eBook
                        </button>
                        <button
                          type="button"
                          onClick={() => {
                            if (!audiobookMonitorLocked) {
                              onToggleMonitor('audiobook');
                            }
                          }}
                          disabled={audiobookMonitorLocked}
                          className={`inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-xs font-medium transition-colors ${
                            audiobookMonitorLocked
                              ? 'bg-gray-500/10 text-gray-500 dark:text-gray-400 cursor-not-allowed opacity-80'
                              : monitorAudiobook
                                ? 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300 hover:bg-emerald-500/30'
                              : 'bg-gray-500/10 text-gray-500 dark:text-gray-400 hover:bg-gray-500/20'
                          }`}
                          title={audiobookMonitorLocked ? 'Audiobook already available; monitoring auto-paused' : 'Toggle audiobook monitoring'}
                        >
                          {audiobookMonitorLocked ? (
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="m2.5 12.75 4 4 6-9" />
                              <path strokeLinecap="round" strokeLinejoin="round" d="m10.5 12.75 4 4 7-10" />
                            </svg>
                          ) : monitorAudiobook ? (
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
                            </svg>
                          ) : (
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" />
                            </svg>
                          )}
                          Audiobook
                        </button>
                      </div>
                    ) : null}
                  </div>
                ) : null}

              </div>
            </div>

            <div className="sticky top-0 z-10 border-b border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] px-5">
              <div className="relative flex gap-1">
                <div
                  className="absolute bottom-0 h-0.5 bg-emerald-600 transition-all duration-300 ease-out"
                  style={{
                    left: tab === 'files' ? 0 : tab === 'ebooks' ? 84 : 188,
                    width: tab === 'files' ? 64 : tab === 'ebooks' ? 88 : 120,
                  }}
                />
                <button
                  type="button"
                  onClick={() => setTab('files')}
                  className={`px-4 py-2.5 text-sm font-medium border-b-2 border-transparent transition-colors whitespace-nowrap ${
                    tab === 'files' ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                  }`}
                >
                  Files
                </button>
                <button
                  type="button"
                  onClick={() => setTab('ebooks')}
                  className={`px-4 py-2.5 text-sm font-medium border-b-2 border-transparent transition-colors whitespace-nowrap ${
                    tab === 'ebooks' ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                  }`}
                >
                  Search eBooks
                </button>
                <button
                  type="button"
                  onClick={() => setTab('audiobooks')}
                  className={`px-4 py-2.5 text-sm font-medium border-b-2 border-transparent transition-colors whitespace-nowrap ${
                    tab === 'audiobooks' ? 'text-emerald-600 dark:text-emerald-400' : 'text-gray-600 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-200'
                  }`}
                >
                  Search Audiobooks
                </button>
              </div>
            </div>

            <div className="px-5 py-4">
              {tab === 'files' ? (
                <div className="space-y-4">
                  <div className="rounded-2xl border border-[var(--border-muted)] overflow-hidden">
                    <div className="px-4 py-3 text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 bg-black/5 dark:bg-white/5">Matched files</div>
                    <div className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
                      {files.length === 0 ? (
                        <div className="px-4 py-4 text-sm text-gray-500 dark:text-gray-400">No files matched to this book yet.</div>
                      ) : (
                        files.map((f) => (
                          <div key={f.id} className="px-4 py-3">
                            <div className="flex items-start justify-between gap-3">
                              <div className="min-w-0">
                                <div className="text-sm text-gray-900 dark:text-gray-100 truncate" title={f.path}>
                                  {f.path}
                                </div>
                                <div className="mt-0.5 text-xs text-gray-500 dark:text-gray-400 truncate">
                                  {f.file_type ? f.file_type.toUpperCase() : 'FILE'}
                                  {typeof f.confidence === 'number' ? ` · ${(f.confidence * 100).toFixed(0)}%` : ''}
                                </div>
                              </div>
                              {f.file_type ? (
                                <span className={`${getFormatColor(f.file_type).bg} ${getFormatColor(f.file_type).text} inline-flex items-center px-2 py-0.5 rounded-lg text-[10px] font-semibold tracking-wide uppercase flex-shrink-0`}>
                                  {f.file_type.toUpperCase()}
                                </span>
                              ) : null}
                            </div>
                          </div>
                        ))
                      )}
                    </div>
                  </div>

                  <div className="rounded-2xl border border-[var(--border-muted)] overflow-hidden">
                    <div className="px-4 py-3 text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 bg-black/5 dark:bg-white/5">History</div>
                    <div className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
                      {historyLoading ? (
                        <div className="px-4 py-4 text-sm text-gray-500 dark:text-gray-400">Loading history…</div>
                      ) : historyError ? (
                        <div className="px-4 py-4 text-sm text-red-500">{historyError}</div>
                      ) : historyRows.length === 0 ? (
                        <div className="px-4 py-4 text-sm text-gray-500 dark:text-gray-400">No download/rename history yet.</div>
                      ) : (
                        historyRows.map((row) => (
                          <div key={row.id} className="px-4 py-3">
                            <div className="text-xs text-gray-500 dark:text-gray-400">{formatHistoryDate(row.downloaded_at)}</div>
                            <div className="mt-1 text-xs text-gray-700 dark:text-gray-200 break-words">
                              <span className="font-medium">{row.downloaded_filename || 'Unknown file'}</span>
                              {row.source_display_name ? ` (${row.source_display_name})` : row.source ? ` (${row.source})` : ''}
                              {typeof row.match_score === 'number' ? ` · score ${row.match_score}` : ''}
                            </div>
                            <div className="mt-1 text-xs text-gray-600 dark:text-gray-300 break-words">
                              renamed → {row.final_path || 'Unknown location'}
                            </div>
                            {row.overwritten_path ? (
                              <div className="mt-1 text-xs text-amber-600 dark:text-amber-400 break-words">
                                overwrote: {row.overwritten_path}
                              </div>
                            ) : null}
                          </div>
                        ))
                      )}
                    </div>
                  </div>

                  <div className="rounded-2xl border border-[var(--border-muted)] overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setAttemptHistoryOpen((prev) => !prev)}
                      className="w-full px-4 py-3 text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400 bg-black/5 dark:bg-white/5 flex items-center justify-between hover-action"
                      aria-expanded={attemptHistoryOpen}
                    >
                      <span>Attempt history ({attemptHistoryRows.length})</span>
                      <svg
                        className={`w-3.5 h-3.5 transition-transform duration-200 ${attemptHistoryOpen ? 'rotate-90' : ''}`}
                        fill="none"
                        stroke="currentColor"
                        viewBox="0 0 24 24"
                        strokeWidth={2}
                      >
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                    {attemptHistoryOpen ? (
                      <div className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
                        {historyLoading ? (
                          <div className="px-4 py-4 text-sm text-gray-500 dark:text-gray-400">Loading attempt history…</div>
                        ) : historyError ? (
                          <div className="px-4 py-4 text-sm text-red-500">{historyError}</div>
                        ) : attemptHistoryRows.length === 0 ? (
                          <div className="px-4 py-4 text-sm text-gray-500 dark:text-gray-400">No monitored attempt history yet.</div>
                        ) : (
                          attemptHistoryRows.map((row) => (
                            <div key={row.id} className="px-4 py-3">
                              <div className="flex items-center justify-between gap-3">
                                <div className="text-xs text-gray-500 dark:text-gray-400">{formatHistoryDate(row.attempted_at)}</div>
                                {renderAttemptStatusBadge(row.status)}
                              </div>
                              <div className="mt-1 text-xs text-gray-700 dark:text-gray-200 break-words">
                                <span className="font-medium">{row.release_title || 'No release title'}</span>
                                {row.source ? ` (${row.source})` : ''}
                                {typeof row.match_score === 'number' ? ` · score ${row.match_score}` : ''}
                              </div>
                              {row.error_message ? (
                                <div className="mt-1 text-xs text-red-600 dark:text-red-300 break-words">{row.error_message}</div>
                              ) : null}
                            </div>
                          ))
                        )}
                      </div>
                    ) : null}
                  </div>
                </div>
              ) : tab === 'ebooks' ? (
                <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] p-4">
                  <div className="text-sm text-gray-600 dark:text-gray-300">
                    Search providers for ebook releases for this book.
                  </div>
                  <div className="mt-3">
                    <button
                      type="button"
                      onClick={() => onOpenSearch('ebook')}
                      className="px-4 py-2 rounded-full bg-emerald-600 hover:bg-emerald-700 text-white text-sm font-medium"
                    >
                      Open eBook search
                    </button>
                  </div>
                </div>
              ) : (
                <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] p-4">
                  <div className="text-sm text-gray-600 dark:text-gray-300">
                    Search providers for audiobook releases for this book.
                  </div>
                  <div className="mt-3">
                    <button
                      type="button"
                      onClick={() => onOpenSearch('audiobook')}
                      className="px-4 py-2 rounded-full bg-violet-600 hover:bg-violet-700 text-white text-sm font-medium"
                    >
                      Open audiobook search
                    </button>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
