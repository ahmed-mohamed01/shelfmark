import { type CSSProperties, type ReactNode, useCallback, useState } from 'react';
import type { MonitoredBookRow } from '../services/monitoredApi';
import { RowThumbnail } from './RowThumbnail';
import {
  isMonitoredBookDormantState,
  monitoredBookHasAnyAvailable,
  monitoredBookTracksAudiobook,
  monitoredBookTracksEbook,
} from '../utils/monitoredBookState';
import { MediaCompactTileBase } from './MediaCompactTileBase';
import { MonitoredBookTableRow } from './MonitoredBookTableRow';

// Augmented type: MonitoredBookRow joined with its author entity fields.
// Must stay structurally in sync with the same-named interface in MonitoredPage.tsx.
export interface MonitoredBookListRow extends MonitoredBookRow {
  author_entity_id: number;
  author_name: string;
  author_provider?: string;
  author_provider_id?: string;
  author_photo_url?: string;
  author_source_url?: string;
}

export interface MonitoredBooksGroup {
  key: string;
  title: string;
  rows: MonitoredBookListRow[];
}

const GRID_CLASSES = {
  mobile: 'grid-cols-1 items-start',
  compact: 'grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 items-start',
} as const;


export interface MonitoredBooksViewProps {
  isLoading: boolean;
  isUpcomingTab: boolean;
  activeBooksCount: number;
  viewMode: 'table' | 'compact';
  bookGroups: MonitoredBooksGroup[];
  groupBy: string;
  selectedBookKeys: Record<string, boolean>;
  isDesktop: boolean;
  booksGridStyle: CSSProperties | undefined;
  loadError: string | null;
  showLoadError: boolean;
  onOpenDetails: (book: MonitoredBookListRow) => void;
  onToggleSelect: (book: MonitoredBookListRow) => void;
  getSelectionKey: (book: MonitoredBookListRow) => string;
  renderBookActions: (book: MonitoredBookListRow, compact?: boolean) => ReactNode;
}

export function MonitoredBooksView({
  isLoading,
  isUpcomingTab,
  activeBooksCount,
  viewMode,
  bookGroups,
  groupBy,
  selectedBookKeys,
  isDesktop,
  booksGridStyle,
  loadError,
  showLoadError,
  onOpenDetails,
  onToggleSelect,
  getSelectionKey,
  renderBookActions,
}: MonitoredBooksViewProps) {
  const [collapsedGroups, setCollapsedGroups] = useState<Record<string, boolean>>({});
  const toggleGroup = useCallback((key: string) => {
    setCollapsedGroups((prev) => ({ ...prev, [key]: !(prev[key] ?? false) }));
  }, []);

  if (isLoading) {
    return <div className="text-sm text-gray-500 dark:text-gray-400">Loading monitored books…</div>;
  }

  if (activeBooksCount === 0) {
    return (
      <div className="text-sm text-gray-500 dark:text-gray-400">
        {isUpcomingTab ? 'No upcoming monitored books yet.' : 'No books with active eBook/audiobook monitoring yet.'}
      </div>
    );
  }

  return (
    <>
      {viewMode === 'table' ? (
        <div className="flex flex-col gap-4">
          {bookGroups.map((group) => {
            const isCollapsed = groupBy !== 'none' && Boolean(collapsedGroups[group.key]);
            return (
            <div key={group.key} className="flex flex-col gap-2">
              {groupBy !== 'none' ? (
                <button
                  type="button"
                  onClick={() => toggleGroup(group.key)}
                  className="flex items-center gap-2 px-1 pt-1 hover-action w-fit"
                  aria-expanded={!isCollapsed}
                >
                  <svg
                    className={`w-3.5 h-3.5 flex-shrink-0 text-gray-400 dark:text-gray-500 transition-transform duration-200 ${isCollapsed ? '' : 'rotate-90'}`}
                    fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                  <h3 className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-200">{group.title}</h3>
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-black/5 dark:bg-white/10 text-gray-600 dark:text-gray-300">
                    {group.rows.length}
                  </span>
                </button>
              ) : null}
              {!isCollapsed && group.rows.map((book) => {
                const isSelected = Boolean(selectedBookKeys[getSelectionKey(book)]);
                const tracksEbook = monitoredBookTracksEbook(book);
                const tracksAudiobook = monitoredBookTracksAudiobook(book);
                const isFulfilled = monitoredBookHasAnyAvailable(book);
                const isDormant = isMonitoredBookDormantState(book);
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
                    {isFulfilled ? (
                      <span
                        className="inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-semibold tracking-wide uppercase bg-sky-500/20 text-sky-700 dark:text-sky-300"
                        title="Book files are available"
                      >
                        Available
                      </span>
                    ) : null}
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
                        onClick={() => onToggleSelect(book)}
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
                    thumbnail={<RowThumbnail url={book.cover_url} alt={book.title || 'Unknown title'} />}
                    onOpen={() => onOpenDetails(book)}
                    titleRow={titleRow}
                    subtitleRow={subtitleRow}
                    metaRow={metaRow}
                    availabilitySlot={availabilitySlot}
                    trailingSlot={renderBookActions(book)}
                    isDimmed={isDormant}
                  />
                );
              })}
            </div>
            );
          })}
        </div>
      ) : (
        <div className="flex flex-col gap-5">
          {bookGroups.map((group) => {
            const isCollapsed = groupBy !== 'none' && Boolean(collapsedGroups[group.key]);
            return (
            <div key={group.key} className="flex flex-col gap-3">
              {groupBy !== 'none' ? (
                <button
                  type="button"
                  onClick={() => toggleGroup(group.key)}
                  className="flex items-center gap-2 px-1 pt-1 hover-action w-fit"
                  aria-expanded={!isCollapsed}
                >
                  <svg
                    className={`w-3.5 h-3.5 flex-shrink-0 text-gray-400 dark:text-gray-500 transition-transform duration-200 ${isCollapsed ? '' : 'rotate-90'}`}
                    fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}
                  >
                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                  </svg>
                  <h3 className="text-xs sm:text-sm font-semibold text-gray-700 dark:text-gray-200">{group.title}</h3>
                  <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-medium bg-black/5 dark:bg-white/10 text-gray-600 dark:text-gray-300">
                    {group.rows.length}
                  </span>
                </button>
              ) : null}
              {!isCollapsed && <div
                className={`grid gap-4 ${!isDesktop ? GRID_CLASSES.mobile : 'items-stretch'}`}
                style={booksGridStyle}
              >
                {group.rows.map((book) => {
                  const isSelected = Boolean(selectedBookKeys[getSelectionKey(book)]);
                  const tracksEbook = monitoredBookTracksEbook(book);
                  const tracksAudiobook = monitoredBookTracksAudiobook(book);
                  const isFulfilled = monitoredBookHasAnyAvailable(book);
                  const isDormant = isMonitoredBookDormantState(book);
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
                      media={<RowThumbnail url={book.cover_url} alt={book.title || 'Book cover'} className="w-full aspect-[2/3]" />}
                      onOpen={() => onOpenDetails(book)}
                      topLeftOverlay={(
                        <button
                          type="button"
                          onClick={(event) => {
                            event.stopPropagation();
                            onToggleSelect(book);
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
                      topRightOverlay={(
                        <>
                          {isFulfilled ? (
                            <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-sky-600/90 text-white text-[9px] font-semibold uppercase shadow">Available</span>
                          ) : null}
                          {badge ? <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-emerald-600/90 text-white text-[9px] font-semibold uppercase shadow">{badge}</span> : null}
                        </>
                      )}
                      subtitle={authorName}
                      metaLine={seriesLabel || metaLine}
                      overflowMenu={renderBookActions(book, true)}
                      isDimmed={isDormant}
                    />
                  );
                })}
              </div>}
            </div>
          );
          })}
        </div>
      )}
      {showLoadError && loadError ? (
        <div className="mt-2 text-xs text-amber-600 dark:text-amber-400">{loadError}</div>
      ) : null}
    </>
  );
}
