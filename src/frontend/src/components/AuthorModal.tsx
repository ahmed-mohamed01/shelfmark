import { useCallback, useEffect, useMemo, useState, type ReactNode } from 'react';
import { Book, ContentType, OpenReleasesOptions, ReleasePrimaryAction, StatusData } from '../types';
import { getMetadataAuthorInfo, MetadataAuthor, MetadataAuthorDetailsResult } from '../services/monitoredApi';
import { withBasePath } from '../utils/basePath';
import { EditAuthorModal } from './EditAuthorModal';
import { MonitoredAuthorBooksTab } from './MonitoredAuthorBooksTab';

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
  renderEmbeddedSearch?: (book: Book, contentType: ContentType) => ReactNode;
}

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
  renderEmbeddedSearch,
}: AuthorModalProps) => {
  const [isClosing, setIsClosing] = useState(false);
  const [details, setDetails] = useState<MetadataAuthor | null>(null);
  const [supportsDetails, setSupportsDetails] = useState<boolean | null>(null);
  const [isLoadingDetails, setIsLoadingDetails] = useState(false);
  const [detailsError, setDetailsError] = useState<string | null>(null);
  const [showMoreDetails, setShowMoreDetails] = useState(false);
  const [isEditModalOpen, setIsEditModalOpen] = useState(openEditOnMount);
  const [fallbackAuthorPhoto, setFallbackAuthorPhoto] = useState<string | null>(null);
  const [booksQuery, setBooksQuery] = useState('');
  const isPageMode = displayMode === 'page';
  const activeBooksQuery = booksSearchQuery ?? booksQuery;
  const updateBooksQuery = (value: string) => {
    if (onBooksSearchQueryChange) {
      onBooksSearchQueryChange(value);
      return;
    }
    setBooksQuery(value);
  };

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
      setIsEditModalOpen(false);
    }
  }, [author, monitoredEntityId]);

  useEffect(() => {
    const nextQuery = (initialBooksQuery || '').trim();
    if (onBooksSearchQueryChange) {
      onBooksSearchQueryChange(nextQuery);
      return;
    }
    setBooksQuery(nextQuery);
  }, [author?.name, initialBooksQuery, initialBookProvider, initialBookProviderId, onBooksSearchQueryChange]);

  useEffect(() => {
    if (!author) {
      setDetails(null);
      setSupportsDetails(null);
      setDetailsError(null);
      setIsLoadingDetails(false);
      setShowMoreDetails(false);
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

  const titleId = useMemo(() => {
    if (!author) return '';
    const key = author.provider && author.provider_id ? `${author.provider}-${author.provider_id}` : author.name;
    return `author-details-title-${key.replace(/[^a-zA-Z0-9_-]/g, '')}`;
  }, [author]);

  if (!author && !isClosing) return null;
  if (!author) return null;

  const resolvedName = details?.name || author.name;
  const resolvedPhoto = details?.photo_url || author.photo_url || fallbackAuthorPhoto || null;
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

              <MonitoredAuthorBooksTab
                author={author}
                monitoredEntityId={monitoredEntityId}
                status={status}
                isPageMode={isPageMode}
                activeBooksQuery={activeBooksQuery}
                updateBooksQuery={updateBooksQuery}
                initialBookProvider={initialBookProvider}
                initialBookProviderId={initialBookProviderId}
                onGetReleases={onGetReleases}
                defaultReleaseContentType={defaultReleaseContentType}
                defaultReleaseActionEbook={defaultReleaseActionEbook}
                defaultReleaseActionAudiobook={defaultReleaseActionAudiobook}
                renderEmbeddedSearch={renderEmbeddedSearch}
                onFallbackPhotoChange={setFallbackAuthorPhoto}
              />
            </div>
          </div>
        </div>
      </div>

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
