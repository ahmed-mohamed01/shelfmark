import { type CSSProperties, type ReactNode, useCallback, useMemo, useState } from 'react';
import type { MonitoredBookRow } from '../services/monitoredApi';
import { RowThumbnail } from './RowThumbnail';
import {
  getFormatStatus,
  isMonitoredBookDormantState,
  monitoredBookTracksAudiobook,
  monitoredBookTracksEbook,
} from '../utils/monitoredBookState';
import { MonitoredBookCompactTile } from './MonitoredBookCompactTile';
import { MonitoredBookTableRow } from './MonitoredBookTableRow';
import { FormatStatusBadge } from './FormatStatusBadge';

const formatUpcomingDate = (book: MonitoredBookListRow): string => {
  if (typeof book.release_date === 'string' && book.release_date.trim()) {
    const parsed = Date.parse(book.release_date);
    if (Number.isFinite(parsed)) {
      return new Date(parsed).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }
  }
  if (typeof book.publish_year === 'number') return String(book.publish_year);
  return 'TBA';
};

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
  compactMinWidth: number;
  loadError: string | null;
  showLoadError: boolean;
  onOpenDetails: (book: MonitoredBookListRow) => void;
  onToggleSelect: (book: MonitoredBookListRow) => void;
  getSelectionKey: (book: MonitoredBookListRow) => string;
  renderBookActions: (book: MonitoredBookListRow, compact?: boolean) => ReactNode;
}

const AuthorAvatar = ({ url, name }: { url?: string | null; name: string }) => {
  const initials = name.trim().split(/\s+/).map((w) => w[0]?.toUpperCase() ?? '').slice(0, 2).join('');
  if (url) {
    return (
      <img
        src={url}
        alt={name}
        className="w-6 h-6 rounded-full object-cover flex-shrink-0"
        onError={(e) => { (e.currentTarget as HTMLImageElement).style.display = 'none'; }}
      />
    );
  }
  return (
    <span className="w-6 h-6 rounded-full flex-shrink-0 flex items-center justify-center bg-gray-200 dark:bg-gray-700 text-[10px] font-semibold text-gray-600 dark:text-gray-300">
      {initials}
    </span>
  );
};

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
  compactMinWidth,
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

  const hasAnySelection = useMemo(
    () => Object.values(selectedBookKeys).some(Boolean),
    [selectedBookKeys],
  );

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
            const authorPhotoUrl = group.rows[0]?.author_photo_url;
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
                  {groupBy === 'author' ? (
                    <AuthorAvatar url={authorPhotoUrl} name={group.title} />
                  ) : null}
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
                const isDormant = isMonitoredBookDormantState(book);
                const authorName = book.author_name || 'Unknown author';
                const ebookStatus = getFormatStatus(book, 'ebook');
                const audiobookStatus = getFormatStatus(book, 'audiobook');
                const seriesLabel = book.series_name
                  ? `${book.series_name}${book.series_position != null ? ` #${book.series_position}` : ''}${book.series_count != null ? `/${book.series_count}` : ''}`
                  : null;
                const ratingLabel = typeof book.rating === 'number' ? `★ ${book.rating.toFixed(1)}` : null;
                const popularityLabel = typeof book.readers_count === 'number'
                  ? `${book.readers_count.toLocaleString()} readers`
                  : typeof book.ratings_count === 'number'
                    ? `${book.ratings_count.toLocaleString()} ratings`
                    : null;
                const popularityLine = [ratingLabel, popularityLabel].filter(Boolean).join(' • ');
                const releaseDatePart = isUpcomingTab ? formatUpcomingDate(book) : (book.publish_year ? String(book.publish_year) : null);
                const subtitleRow = (
                  <div className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-600 dark:text-gray-300 truncate">
                    {[authorName, releaseDatePart].filter(Boolean).join(' • ')}
                  </div>
                );
                const titleRow = (
                  <div className="flex items-center gap-2 min-w-0">
                    <h3 className="font-semibold text-xs min-[400px]:text-sm sm:text-base leading-tight truncate" title={book.title || 'Unknown title'}>
                      {book.title || 'Unknown title'}
                    </h3>
                    {seriesLabel ? (
                      <span className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-500 dark:text-gray-400 truncate">
                        • {book.series_name}
                      </span>
                    ) : null}
                    {book.series_position != null && seriesLabel ? (
                      <span
                        className="inline-flex px-1 py-0 text-[9px] sm:text-[10px] font-bold text-white bg-emerald-600 rounded flex-shrink-0"
                        style={{ boxShadow: '0 1px 4px rgba(0,0,0,0.3)', textShadow: '0 1px 2px rgba(0,0,0,0.3)' }}
                        title={seriesLabel}
                      >
                        #{book.series_position}{book.series_count != null ? `/${book.series_count}` : ''}
                      </span>
                    ) : null}
                  </div>
                );
                const metaRow = popularityLine ? (
                  <div className="text-[10px] min-[400px]:text-xs text-gray-500 dark:text-gray-400 truncate">
                    {popularityLine}
                  </div>
                ) : undefined;

                return (
                  <MonitoredBookTableRow
                    key={`${book.author_entity_id}:${book.provider || 'unknown'}:${book.provider_book_id || book.id}`}
                    leadingControl={(
                      <button
                        type="button"
                        onClick={() => onToggleSelect(book)}
                        className={`${isSelected ? 'text-emerald-500 dark:text-emerald-400' : 'text-gray-500 dark:text-gray-400'} ${isSelected || hasAnySelection ? 'opacity-100' : 'opacity-0 group-hover:opacity-50'} transition-opacity`}
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
                    availabilitySlot={(
                      <div className="flex items-center justify-center gap-1">
                        {ebookStatus ? <FormatStatusBadge format="ebook" status={ebookStatus} /> : null}
                        {audiobookStatus ? <FormatStatusBadge format="audiobook" status={audiobookStatus} /> : null}
                      </div>
                    )}
                    trailingSlot={renderBookActions(book)}
                    isDimmed={isDormant || (!tracksEbook && !tracksAudiobook)}
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
            const authorPhotoUrl = group.rows[0]?.author_photo_url;
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
                  {groupBy === 'author' ? (
                    <AuthorAvatar url={authorPhotoUrl} name={group.title} />
                  ) : null}
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
                  const isDormant = isMonitoredBookDormantState(book);
                  const authorName = book.author_name || 'Unknown author';
                  const ebookStatus = getFormatStatus(book, 'ebook');
                  const audiobookStatus = getFormatStatus(book, 'audiobook');
                  const seriesLabel = book.series_name
                    ? `${book.series_name}${book.series_position != null ? ` #${book.series_position}` : ''}${book.series_count != null ? `/${book.series_count}` : ''}`
                    : undefined;
                  const ratingLabel = typeof book.rating === 'number' ? `★ ${book.rating.toFixed(1)}` : null;
                  const popularityLabel = typeof book.readers_count === 'number'
                    ? `${book.readers_count.toLocaleString()} readers`
                    : typeof book.ratings_count === 'number'
                      ? `${book.ratings_count.toLocaleString()} ratings`
                      : null;
                  const popularityLine = [ratingLabel, popularityLabel].filter(Boolean).join(' • ') || undefined;
                  const showPopularity = compactMinWidth >= 194 && Boolean(popularityLine);
                  // In upcoming tab: always show release date; otherwise series or year
                  const metaLine = isUpcomingTab
                    ? formatUpcomingDate(book)
                    : (seriesLabel || (book.publish_year ? String(book.publish_year) : undefined));

                  return (
                    <MonitoredBookCompactTile
                      key={`${book.author_entity_id}:${book.provider || 'unknown'}:${book.provider_book_id || book.id}:compact`}
                      title={book.title || 'Unknown title'}
                      thumbnail={<RowThumbnail url={book.cover_url} alt={book.title || 'Book cover'} className="w-full aspect-[2/3]" />}
                      onOpenDetails={() => onOpenDetails(book)}
                      onToggleSelect={() => onToggleSelect(book)}
                      isSelected={isSelected}
                      hasActiveSelection={hasAnySelection}
                      subtitle={groupBy !== 'author' ? authorName : undefined}
                      metaLine={metaLine}
                      showMetaLine={Boolean(metaLine)}
                      popularityLine={popularityLine}
                      showPopularityLine={showPopularity}
                      ebookStatus={ebookStatus}
                      audiobookStatus={audiobookStatus}
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
