import type { CSSProperties } from 'react';

import type { MetadataAuthor } from '../services/monitoredApi';
import type { Book, ButtonStateInfo, SortOption } from '../types';
import { MonitoredAuthorTableRow } from './AuthorTableRow';
import { Dropdown } from './Dropdown';
import { MonitoredAuthorCompactTile } from './MonitoredAuthorCompactTile';
import { ResultsSection } from './ResultsSection';
import { RowThumbnail } from './RowThumbnail';
import { ViewModeToggle, type ViewModeToggleOption } from './ViewModeToggle';

export interface MonitoredSearchViewProps {
  // search state
  searchScope: 'authors' | 'books';
  authorQuery: string;
  isSearching: boolean;
  // view modes
  authorViewMode: 'compact' | 'list';
  bookSearchViewMode: 'compact' | 'list';
  authorSearchViewOptions: ViewModeToggleOption[];
  bookSearchViewOptions: ViewModeToggleOption[];
  onAuthorViewModeChange: (next: string) => void;
  onBookSearchViewModeChange: (next: string) => void;
  // books search
  bookSearchResults: Book[];
  bookSearchSortValue: string;
  monitoredSearchSortOptions: SortOption[];
  onBookSortChange: (value: string) => void;
  // author search
  authorSearchSortValue: string;
  onAuthorSortChange: (value: string) => void;
  onScopeChange: (scope: 'authors' | 'books') => void;
  authorResults: string[];
  authorCards: MetadataAuthor[];
  monitoredNames: Set<string>;
  // callbacks
  onAuthorNavigate: (author: MetadataAuthor) => void;
  onMonitorAuthor: (payload: {
    name: string;
    provider?: string;
    provider_id?: string;
    photo_url?: string;
    books_count?: number;
  }) => void;
  onBookDetails: (bookId: string) => Promise<void>;
  onBookGet: (book: Book) => Promise<void>;
  onBookMonitorAction: (book: Book) => void;
  isBookMonitored: (book: Book) => boolean;
  getMonitorResultButtonState: (bookId: string) => ButtonStateInfo;
  noopDownload: (book: Book) => Promise<void>;
  compactGridStyle?: CSSProperties;
  // desktop nav tabs
  onTabChange: (tab: 'authors' | 'books' | 'upcoming') => void;
  onBack: () => void;
  displayAuthorsCount: number | string;
  displayBooksCount: number | string;
  displayUpcomingCount: number | string;
  displaySearchCount: number | string;
  /** Hide the built-in header (tabs + scope toggle) when rendered inside a parent that provides its own */
  hideHeader?: boolean;
}

/** Emerald "Monitoring ✓" badge for authors already followed (not a button). */
const MonitoringBadge = ({ compact = false }: { compact?: boolean }) => (
  <span
    className={`inline-flex items-center justify-center gap-1.5 rounded-full bg-emerald-100 font-semibold text-emerald-700 dark:bg-emerald-500/15 dark:text-emerald-400 ${
      compact ? 'w-full px-2.5 py-1.5 text-xs' : 'px-3 py-1 text-xs'
    }`}
    title="This author is already monitored"
  >
    <svg
      className="h-3 w-3"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={2.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <polyline points="20 6 9 17 4 12" />
    </svg>
    Monitoring
  </span>
);

/** Secondary outline action: drill into the author's books without following. */
const ViewBooksButton = ({
  onClick,
  compact = false,
}: {
  onClick: () => void;
  compact?: boolean;
}) => (
  <button
    type="button"
    onClick={(e) => {
      e.stopPropagation();
      onClick();
    }}
    className={`hover-action inline-flex items-center justify-center rounded-full border border-[var(--border-muted)] font-medium text-gray-800 transition-colors dark:text-gray-100 ${
      compact ? 'w-full px-2.5 py-1.5 text-xs' : 'px-3 py-1 text-xs'
    }`}
  >
    View books
  </button>
);

export function MonitoredSearchView({
  searchScope,
  authorQuery,
  isSearching,
  authorViewMode,
  bookSearchViewMode,
  authorSearchViewOptions,
  bookSearchViewOptions,
  onAuthorViewModeChange,
  onBookSearchViewModeChange,
  bookSearchResults,
  authorSearchSortValue,
  onAuthorSortChange,
  onScopeChange,
  bookSearchSortValue,
  monitoredSearchSortOptions,
  onBookSortChange,
  authorResults,
  authorCards,
  monitoredNames,
  onAuthorNavigate,
  onMonitorAuthor,
  onBookDetails,
  onBookGet,
  onBookMonitorAction,
  isBookMonitored,
  getMonitorResultButtonState,
  noopDownload,
  compactGridStyle,
  onTabChange,
  onBack,
  displayAuthorsCount,
  displayBooksCount,
  displayUpcomingCount,
  displaySearchCount,
  hideHeader = false,
}: MonitoredSearchViewProps) {
  // Prefer enriched author cards; fall back to plain name-only stubs from text search
  const displayedAuthors: MetadataAuthor[] =
    authorCards.length > 0
      ? authorCards
      : authorResults.map(
          (name) =>
            ({
              provider: 'hardcover',
              provider_id: name,
              name,
              stats: { books_count: null },
            }) as MetadataAuthor,
        );

  const sortValue = searchScope === 'books' ? bookSearchSortValue : authorSearchSortValue;
  const onSortChange = searchScope === 'books' ? onBookSortChange : onAuthorSortChange;
  const currentSortLabel =
    monitoredSearchSortOptions.find((o) => o.value === sortValue)?.label ||
    monitoredSearchSortOptions[0]?.label ||
    'Most relevant';

  const sortDropdownChildren = ({ close }: { close: () => void }) => (
    <div role="listbox" aria-label="Sort search results">
      {monitoredSearchSortOptions.map((option) => {
        const isSelected = option.value === sortValue;
        return (
          <button
            type="button"
            key={option.value}
            className={`hover-surface flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-base ${isSelected ? 'font-medium text-emerald-600 dark:text-emerald-400' : ''}`}
            onClick={() => {
              onSortChange(option.value);
              close();
            }}
            role="option"
            aria-selected={isSelected}
          >
            <span>{option.label}</span>
            {isSelected ? (
              <svg
                xmlns="http://www.w3.org/2000/svg"
                fill="none"
                viewBox="0 0 24 24"
                strokeWidth={1.5}
                stroke="currentColor"
                className="h-4 w-4"
              >
                <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
              </svg>
            ) : null}
          </button>
        );
      })}
    </div>
  );

  return (
    <section
      className="flex flex-col rounded-none border-0 border-black/10 bg-transparent sm:overflow-hidden sm:rounded-2xl sm:border sm:bg-white/80 sm:shadow-xl dark:border-white/10 sm:dark:bg-white/5"
      style={{ maxHeight: 'calc(100dvh - 8rem)' }}
    >
      {/* Single header row — tabs + controls, flex-wrap so controls fall below tabs on narrow screens */}
      <div
        className={`relative z-10 flex shrink-0 flex-wrap items-center gap-3 gap-y-2 border-b border-black/10 px-4 pt-4 pb-2 dark:border-white/10 ${hideHeader ? 'hidden' : ''}`}
      >
        {/* Left: back button + mobile label / desktop tab pills */}
        <div className="flex min-w-0 items-center gap-2">
          {/* Back button — desktop only, same as landing page */}
          <button
            type="button"
            onClick={onBack}
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
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.5 7.5 12 15 4.5" />
            </svg>
          </button>
          {/* Mobile: "Search Results n" label */}
          <div className="flex items-center gap-2 sm:hidden">
            <span className="text-base font-bold text-gray-900 dark:text-gray-100">
              Search Results
            </span>
            <span className="inline-flex min-w-[1.5rem] items-center justify-center rounded-full bg-emerald-100 px-2 py-0.5 text-xs leading-none font-semibold text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-400">
              {searchScope === 'books' ? bookSearchResults.length : authorResults.length}
            </span>
          </div>
          {/* Desktop: tab pills — same container style as landing page */}
          <div className="hidden items-center rounded-full border border-[var(--border-muted)] bg-transparent sm:inline-flex">
            <button
              type="button"
              onClick={() => onTabChange('authors')}
              className="hover-action flex items-center gap-1.5 rounded-full px-3.5 py-2 text-sm font-medium text-gray-700 transition-colors dark:text-gray-200"
            >
              <span>Monitored </span>Authors
              <span className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-emerald-100 px-1.5 py-0.5 text-xs leading-none font-semibold text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-400">
                {displayAuthorsCount}
              </span>
            </button>
            <button
              type="button"
              onClick={() => onTabChange('books')}
              className="hover-action flex items-center gap-1.5 rounded-full px-3.5 py-2 text-sm font-medium text-gray-700 transition-colors dark:text-gray-200"
            >
              <span>Monitored </span>Books
              <span className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-emerald-100 px-1.5 py-0.5 text-xs leading-none font-semibold text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-400">
                {displayBooksCount}
              </span>
            </button>
            <button
              type="button"
              onClick={() => onTabChange('upcoming')}
              className="hover-action flex items-center gap-1.5 rounded-full px-3.5 py-2 text-sm font-medium text-gray-700 transition-colors dark:text-gray-200"
            >
              Upcoming
              <span className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-emerald-100 px-1.5 py-0.5 text-xs leading-none font-semibold text-emerald-700 dark:bg-emerald-500/20 dark:text-emerald-400">
                {displayUpcomingCount}
              </span>
            </button>
            {/* Search — active */}
            <button
              type="button"
              className="flex items-center gap-1.5 rounded-full bg-emerald-600 px-3.5 py-2 text-sm font-medium text-white shadow-sm transition-colors"
              aria-current="true"
            >
              Search
              <span className="inline-flex min-w-[1.25rem] items-center justify-center rounded-full bg-white/25 px-1.5 py-0.5 text-xs leading-none font-semibold text-white">
                {displaySearchCount}
              </span>
            </button>
          </div>
        </div>

        {/* Right: controls — Authors/Books scope | filter icon | view mode toggle */}
        <div className="ml-auto flex items-center justify-end gap-2">
          {/* Authors / Books scope toggle */}
          <div className="inline-flex shrink-0 items-center rounded-full border border-[var(--border-muted)] bg-transparent">
            <button
              type="button"
              onClick={() => onScopeChange('authors')}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${searchScope === 'authors' ? 'bg-emerald-600 text-white shadow-sm' : 'hover-action text-gray-700 dark:text-gray-200'}`}
              aria-pressed={searchScope === 'authors'}
            >
              Authors
            </button>
            <button
              type="button"
              onClick={() => onScopeChange('books')}
              className={`rounded-full px-3 py-1.5 text-xs font-medium transition-colors ${searchScope === 'books' ? 'bg-emerald-600 text-white shadow-sm' : 'hover-action text-gray-700 dark:text-gray-200'}`}
              aria-pressed={searchScope === 'books'}
            >
              Books
            </button>
          </div>
          {/* Sort — always a funnel icon */}
          <Dropdown
            align="right"
            widthClassName="w-60 sm:w-72"
            renderTrigger={({ isOpen, toggle }) => (
              <button
                type="button"
                onClick={toggle}
                title={`Sort: ${currentSortLabel}`}
                aria-label="Sort"
                aria-haspopup="listbox"
                aria-expanded={isOpen}
                className={`hover-action rounded-full p-2 transition-colors ${isOpen ? 'bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400' : 'text-gray-600 dark:text-gray-300'}`}
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
                    d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591l-5.432 5.432a2.25 2.25 0 0 0-.659 1.591v2.927a2.25 2.25 0 0 1-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 0 0-.659-1.591L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z"
                  />
                </svg>
              </button>
            )}
          >
            {sortDropdownChildren}
          </Dropdown>
          {/* View mode toggle — far right */}
          {searchScope === 'authors' ? (
            <ViewModeToggle
              value={authorViewMode}
              onChange={onAuthorViewModeChange}
              options={authorSearchViewOptions}
            />
          ) : (
            <ViewModeToggle
              value={bookSearchViewMode}
              onChange={onBookSearchViewModeChange}
              options={bookSearchViewOptions}
            />
          )}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 pt-3 pb-4">
        {!authorQuery.trim() && !isSearching ? (
          <div className="rounded-2xl border border-dashed border-black/10 bg-white/60 px-4 py-8 text-center dark:border-white/10 dark:bg-white/5">
            <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
              Start a search from the top bar
            </div>
            <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">
              Use the header search input to find authors or books to monitor.
            </div>
            <div className="mt-4">
              <button
                type="button"
                onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
                className="rounded-full bg-white/70 px-3 py-1.5 text-xs font-medium text-gray-900 hover:bg-white dark:bg-white/10 dark:text-gray-100 dark:hover:bg-white/20"
              >
                Go to search bar
              </button>
            </div>
          </div>
        ) : searchScope === 'books' ? (
          bookSearchResults.length === 0 ? (
            <div className="text-sm text-gray-500 dark:text-gray-400">
              Search for a book to monitor.
            </div>
          ) : (
            <ResultsSection
              books={bookSearchResults}
              visible
              onDetails={onBookDetails}
              onDownload={noopDownload}
              onGetReleases={onBookGet}
              getButtonState={getMonitorResultButtonState}
              getUniversalButtonState={getMonitorResultButtonState}
              sortValue={bookSearchSortValue}
              onSortChange={onBookSortChange}
              hideSortControl
              hideViewToggle
              viewMode={bookSearchViewMode}
              onViewModeChange={(next) =>
                onBookSearchViewModeChange(next === 'list' ? 'list' : 'compact')
              }
              customAction={{
                label: 'Monitor',
                onClick: (book) => onBookMonitorAction(book),
                isDisabled: (book) => isBookMonitored(book),
                getLabel: (book) => (isBookMonitored(book) ? 'Monitored' : 'Monitor'),
              }}
            />
          )
        ) : authorResults.length === 0 ? (
          <div className="text-sm text-gray-500 dark:text-gray-400">
            Search for an author to add.
          </div>
        ) : authorViewMode === 'list' ? (
          <div className="flex flex-col gap-2">
            {displayedAuthors.map((author) => {
              const name = author.name;
              const isMonitored = monitoredNames.has(name.toLowerCase());
              const booksCount = author.stats?.books_count;
              const subtitle = `${typeof booksCount === 'number' ? `${booksCount} books` : 'Unknown'}${author.provider ? ` • ${author.provider}` : ''}`;
              return (
                <MonitoredAuthorTableRow
                  key={`${author.provider}:${author.provider_id}`}
                  name={name || 'Unknown author'}
                  subtitle={subtitle}
                  thumbnail={
                    <RowThumbnail
                      url={author.photo_url}
                      alt={name || 'Unknown author'}
                      kind="author"
                    />
                  }
                  onOpen={() => onAuthorNavigate(author)}
                  trailingAction={
                    <div className="flex items-center gap-1.5">
                      {isMonitored ? (
                        <MonitoringBadge />
                      ) : (
                        <button
                          type="button"
                          onClick={(e) => {
                            e.stopPropagation();
                            onMonitorAuthor({
                              name,
                              provider: author.provider,
                              provider_id: author.provider_id,
                              photo_url: author.photo_url || undefined,
                              books_count:
                                typeof author.stats?.books_count === 'number'
                                  ? author.stats?.books_count
                                  : undefined,
                            });
                          }}
                          className="inline-flex items-center gap-1 rounded-full bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-700"
                        >
                          <svg
                            className="h-3 w-3"
                            viewBox="0 0 24 24"
                            fill="none"
                            stroke="currentColor"
                            strokeWidth={2.5}
                            strokeLinecap="round"
                            strokeLinejoin="round"
                            aria-hidden="true"
                          >
                            <line x1="12" y1="5" x2="12" y2="19" />
                            <line x1="5" y1="12" x2="19" y2="12" />
                          </svg>
                          Monitor
                        </button>
                      )}
                      <ViewBooksButton onClick={() => onAuthorNavigate(author)} />
                    </div>
                  }
                />
              );
            })}
          </div>
        ) : (
          <div className="grid items-start gap-4" style={compactGridStyle}>
            {displayedAuthors.map((author, index) => {
              const name = author.name;
              const isMonitored = monitoredNames.has(name.toLowerCase());
              const booksCount = author.stats?.books_count;
              const subtitle = typeof booksCount === 'number' ? `${booksCount} books` : 'Unknown';
              return (
                <div
                  key={`${author.provider}:${author.provider_id}`}
                  className={index < 12 ? 'animate-pop-up' : undefined}
                  style={index < 12 ? { animationDelay: `${index * 20}ms` } : undefined}
                >
                  <MonitoredAuthorCompactTile
                    name={name || 'Unknown author'}
                    thumbnail={
                      <RowThumbnail
                        url={author.photo_url}
                        alt={name || 'Author photo'}
                        kind="author"
                        className="aspect-[2/3] w-full"
                      />
                    }
                    subtitle={subtitle}
                    onOpenDetails={() => onAuthorNavigate(author)}
                    footer={
                      <div className="flex flex-col gap-1.5">
                        {isMonitored ? (
                          <MonitoringBadge compact />
                        ) : (
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              onMonitorAuthor({
                                name,
                                provider: author.provider,
                                provider_id: author.provider_id,
                                photo_url: author.photo_url || undefined,
                                books_count:
                                  typeof author.stats?.books_count === 'number'
                                    ? author.stats?.books_count
                                    : undefined,
                              });
                            }}
                            className="inline-flex w-full items-center justify-center gap-1.5 rounded-full bg-emerald-600 px-2.5 py-1.5 text-xs text-white transition-all duration-200 hover:bg-emerald-700"
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
                        )}
                        <ViewBooksButton compact onClick={() => onAuthorNavigate(author)} />
                      </div>
                    }
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>
    </section>
  );
}
