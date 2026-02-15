import { useCallback, useEffect, useMemo, useState } from 'react';
import { Book, ContentType } from '../types';
import { getMetadataAuthorInfo, getMetadataBookInfo, MetadataAuthor, MetadataAuthorDetailsResult, searchMetadata } from '../services/api';
import { withBasePath } from '../utils/basePath';
import { Dropdown } from './Dropdown';

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
  onGetReleases?: (book: Book, contentType: ContentType) => Promise<void>;
}

export const AuthorModal = ({ author, onClose, onGetReleases }: AuthorModalProps) => {
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

  const handleClose = useCallback(() => {
    setIsClosing(true);
    setTimeout(() => {
      onClose();
      setIsClosing(false);
    }, 150);
  }, [onClose]);

  useEffect(() => {
    const handleEscape = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        handleClose();
      }
    };

    document.addEventListener('keydown', handleEscape);
    return () => document.removeEventListener('keydown', handleEscape);
  }, [handleClose]);

  useEffect(() => {
    if (author) {
      const previousOverflow = document.body.style.overflow;
      document.body.style.overflow = 'hidden';
      return () => {
        document.body.style.overflow = previousOverflow;
      };
    }
  }, [author]);

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

    const loadBooks = async () => {
      setBooks([]);
      setBooksError(null);
      setIsLoadingBooks(true);

      try {
        const limit = 40;
        const maxPages = 12;
        const maxBooks = 500;

        let page = 1;
        let hasMore = true;
        const allBooks: Book[] = [];

        while (hasMore && page <= maxPages && allBooks.length < maxBooks) {
          const result = await searchMetadata('', limit, 'relevance', { author: author.name }, page, 'ebook');
          if (isCancelled) return;

          allBooks.push(...result.books);
          setBooks([...allBooks]);

          hasMore = result.hasMore;
          page += 1;
        }

        const candidates = allBooks
          .filter((book) => Boolean(book.provider && book.provider_id))
          .filter((book) => !(book.series_name && book.series_position != null));

        const maxEnrich = 40;
        const batchSize = 5;
        const toEnrich = candidates.slice(0, maxEnrich);

        if (toEnrich.length > 0) {
          const enriched: Array<Book | null> = [];

          for (let i = 0; i < toEnrich.length; i += batchSize) {
            const batch = toEnrich.slice(i, i + batchSize);
            const batchResults = await Promise.all(
              batch.map(async (book) => {
                try {
                  if (!book.provider || !book.provider_id) return null;
                  const full = await getMetadataBookInfo(book.provider, book.provider_id);
                  return full;
                } catch {
                  return null;
                }
              })
            );
            enriched.push(...batchResults);
            if (isCancelled) return;
          }

          const byId = new Map(enriched.filter((b): b is Book => Boolean(b)).map((b) => [b.id, b]));

          if (byId.size > 0) {
            setBooks((current) =>
              current.map((book) => {
                const update = byId.get(book.id);
                if (!update) return book;
                if (book.series_name && book.series_position != null) return book;
                return {
                  ...book,
                  series_name: update.series_name,
                  series_position: update.series_position,
                  series_count: update.series_count,
                };
              })
            );
          }
        }
      } catch (e) {
        if (isCancelled) return;
        const message = e instanceof Error ? e.message : 'Failed to load books';
        setBooksError(message);
      } finally {
        if (!isCancelled) {
          setIsLoadingBooks(false);
        }
      }
    };

    void loadBooks();

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
  const resolvedPhoto = details?.photo_url || author.photo_url || null;
  const resolvedBio = details?.bio || null;
  const resolvedUrl = details?.source_url || author.source_url || null;
  const providerLabel = details?.provider || author.provider || null;
  const booksCount = details?.stats?.books_count ?? null;

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

  return (
    <div
      className="modal-overlay active sm:px-6 sm:py-6"
      onClick={e => {
        if (e.target === e.currentTarget) handleClose();
      }}
    >
      <div
        className={`details-container w-full max-w-5xl h-full sm:h-auto ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <div className="flex h-full sm:h-[90vh] sm:max-h-[90vh] flex-col overflow-hidden rounded-none sm:rounded-2xl border-0 sm:border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-none sm:shadow-2xl">
          <header className="flex items-start gap-4 border-b border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] px-5 py-4">
            <div className="flex-1 space-y-1 min-w-0">
              <p className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Author</p>
              <h3 id={titleId} className="text-lg font-semibold leading-snug truncate">
                {resolvedName || 'Unknown author'}
              </h3>
              {resolvedUrl ? (
                <a
                  href={resolvedUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="text-sm text-gray-600 dark:text-gray-300 hover:underline"
                >
                  View on provider
                </a>
              ) : null}
            </div>
            <button
              type="button"
              onClick={handleClose}
              className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
              aria-label="Close author details"
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

          <div className="flex-1 min-h-0 overflow-y-auto px-5 py-6">
            <div className="flex flex-col gap-6">
              <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg-soft)] sm:bg-[var(--bg)] p-4">
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
                        {resolvedUrl ? (
                          <a
                            href={resolvedUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-1 inline-block text-xs text-gray-600 dark:text-gray-300 hover:underline"
                          >
                            View on provider
                          </a>
                        ) : null}
                      </div>

                      <button
                        type="button"
                        onClick={() => setShowMoreDetails((v) => !v)}
                        className="px-3 py-1 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100 flex-shrink-0"
                      >
                        {showMoreDetails ? 'Hide details' : 'Show details'}
                      </button>
                    </div>

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
                      </div>
                    ) : null}
                  </div>
                </div>
              </div>

              <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg-soft)] sm:bg-[var(--bg)] overflow-hidden">
                <div className="flex items-center justify-between gap-3 px-4 py-3 border-b border-[var(--border-muted)]">
                  <p className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400">Books</p>
                  <div className="flex items-center gap-1 flex-wrap justify-end">
                    <Dropdown
                      align="right"
                      widthClassName="w-48"
                      renderTrigger={({ isOpen, toggle }) => (
                        <button
                          type="button"
                          onClick={toggle}
                          className={`px-3 py-1 rounded-full text-xs font-medium transition-all duration-200 ${
                            isOpen
                              ? 'bg-white text-gray-900 dark:bg-white/20 dark:text-gray-100'
                              : 'bg-white/70 hover:bg-white text-gray-900 dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100'
                          }`}
                          aria-haspopup="listbox"
                          aria-expanded={isOpen}
                        >
                          <span className="inline-flex items-center gap-1">
                            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                              <path strokeLinecap="round" strokeLinejoin="round" d="M3 6h18M6 12h12M10 18h4" />
                            </svg>
                            <span>Filters</span>
                          </span>
                        </button>
                      )}
                    >
                      {({ close }) => (
                        <div role="listbox" aria-label="Sort books">
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'series_asc' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => {
                              setBooksSort('series_asc');
                              close();
                            }}
                            role="option"
                            aria-selected={booksSort === 'series_asc'}
                          >
                            Series (A–Z)
                          </button>
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'series_desc' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => {
                              setBooksSort('series_desc');
                              close();
                            }}
                            role="option"
                            aria-selected={booksSort === 'series_desc'}
                          >
                            Series (Z–A)
                          </button>
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'year_desc' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => {
                              setBooksSort('year_desc');
                              close();
                            }}
                            role="option"
                            aria-selected={booksSort === 'year_desc'}
                          >
                            Year (newest)
                          </button>
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'year_asc' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => {
                              setBooksSort('year_asc');
                              close();
                            }}
                            role="option"
                            aria-selected={booksSort === 'year_asc'}
                          >
                            Year (oldest)
                          </button>
                          <button
                            type="button"
                            className={`w-full px-3 py-2 text-left text-sm hover-surface ${booksSort === 'title_asc' ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
                            onClick={() => {
                              setBooksSort('title_asc');
                              close();
                            }}
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
                      onClick={() => window.location.assign(withBasePath(`/?q=${encodeURIComponent(author.name)}`))}
                      className="px-3 py-1 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                    >
                      Open in main search
                    </button>
                  </div>
                </div>

                <div className="px-4 py-3">
                  {booksError && <div className="text-sm text-red-500">{booksError}</div>}

                  {books.length === 0 && isLoadingBooks ? (
                    <div className="text-sm text-gray-600 dark:text-gray-300">Loading…</div>
                  ) : books.length === 0 && !isLoadingBooks ? (
                    <div className="text-sm text-gray-600 dark:text-gray-300">No books found.</div>
                  ) : (
                    <>
                    <div className="w-full rounded-xl overflow-hidden" style={{ background: 'var(--bg-soft)' }}>
                      {groupedBooks.map((group, groupIndex) => (
                        <div key={group.key} className={groupIndex === 0 ? '' : 'mt-3'}>
                          <div className="px-3 sm:px-4 py-2 border-t border-b border-gray-200/60 dark:border-gray-800/60 bg-black/5 dark:bg-white/5">
                            <div className="flex items-center justify-between gap-3">
                              <p className="text-[11px] uppercase tracking-wide text-gray-500 dark:text-gray-400 truncate">{group.title}</p>
                              <p className="text-xs text-gray-500 dark:text-gray-400 flex-shrink-0">{group.books.length}</p>
                            </div>
                          </div>

                          <div className="divide-y divide-gray-200/60 dark:divide-gray-800/60">
                            {group.books.map((book, index) => (
                              <div
                                key={book.id}
                                className="px-1.5 sm:px-2 py-1.5 sm:py-2 transition-colors duration-200 hover-row w-full animate-pop-up will-change-transform"
                                style={{
                                  animationDelay: `${Math.min((groupIndex * 30) + (index * 30), 1500)}ms`,
                                  animationFillMode: 'both',
                                }}
                                role="article"
                              >
                                <div className="grid items-center gap-2 sm:gap-y-1 sm:gap-x-0.5 w-full grid-cols-[auto_minmax(0,1fr)_auto] sm:grid-cols-[auto_minmax(0,2fr)_minmax(50px,0.25fr)_auto]">
                                  <div className="flex items-center pl-1 sm:pl-3">
                                    <BooksListThumbnail preview={book.preview} title={book.title} />
                                  </div>

                                  <button
                                    type="button"
                                    className="min-w-0 flex flex-col justify-center sm:pl-3 text-left"
                                    onClick={() => window.location.assign(withBasePath(`/?q=${encodeURIComponent(book.title)}&author=${encodeURIComponent(author.name)}`))}
                                  >
                                    <h3 className="font-semibold text-xs min-[400px]:text-sm sm:text-base leading-tight line-clamp-1 sm:line-clamp-2" title={book.title || 'Untitled'}>
                                      <span className="truncate">{book.title || 'Untitled'}</span>
                                    </h3>
                                    <p className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-600 dark:text-gray-300 truncate">
                                      {book.author || resolvedName || 'Unknown author'}
                                      {book.year && <span className="sm:hidden"> • {book.year}</span>}
                                    </p>
                                    {group.key !== '__standalone__' && book.series_position != null ? (
                                      <div className="text-[10px] min-[400px]:text-xs text-gray-500 dark:text-gray-400 truncate flex items-center gap-2">
                                        <span
                                          className="inline-flex px-1.5 py-0.5 text-[10px] sm:text-xs font-bold text-white bg-emerald-600 rounded border border-emerald-700 flex-shrink-0"
                                          style={{
                                            boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)',
                                            textShadow: '0 1px 2px rgba(0, 0, 0, 0.3)',
                                          }}
                                          title={`${group.title}${book.series_count ? ` (${book.series_position}/${book.series_count})` : ` (#${book.series_position})`}`}
                                        >
                                          #{book.series_position}
                                          {book.series_count != null ? `/${book.series_count}` : ''}
                                        </span>
                                      </div>
                                    ) : null}
                                  </button>

                                  <div className="hidden sm:flex text-xs text-gray-700 dark:text-gray-200 justify-center">
                                    {book.year || '-'}
                                  </div>

                                  <div className="flex flex-row justify-end gap-0.5 sm:gap-1 sm:pr-3">
                                    {onGetReleases ? (
                                      <>
                                        <button
                                          type="button"
                                          className="flex items-center justify-center p-1.5 sm:p-2 rounded-full text-gray-600 dark:text-gray-200 hover-action transition-all duration-200"
                                          onClick={() => void onGetReleases(book, 'ebook')}
                                          aria-label={`Search providers for ebook: ${book.title || 'this book'}`}
                                        >
                                          <BookIcon />
                                        </button>
                                        <button
                                          type="button"
                                          className="flex items-center justify-center p-1.5 sm:p-2 rounded-full text-gray-600 dark:text-gray-200 hover-action transition-all duration-200"
                                          onClick={() => void onGetReleases(book, 'audiobook')}
                                          aria-label={`Search providers for audiobook: ${book.title || 'this book'}`}
                                        >
                                          <AudiobookIcon />
                                        </button>
                                      </>
                                    ) : null}
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
                        </div>
                      ))}
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
  );
};
