import type { CSSProperties } from 'react';
import type { MetadataAuthor } from '../services/monitoredApi';
import type { Book, ButtonStateInfo, SortOption } from '../types';
import { Dropdown } from './Dropdown';
import { MonitoredAuthorTableRow } from './AuthorTableRow';
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
  onMonitorAuthor: (payload: { name: string; provider?: string; provider_id?: string; photo_url?: string; books_count?: number }) => void;
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
  const displayedAuthors: MetadataAuthor[] = authorCards.length > 0
    ? authorCards
    : authorResults.map((name) => ({
        provider: 'hardcover',
        provider_id: name,
        name,
        stats: { books_count: null },
      } as MetadataAuthor));

  const sortValue = searchScope === 'books' ? bookSearchSortValue : authorSearchSortValue;
  const onSortChange = searchScope === 'books' ? onBookSortChange : onAuthorSortChange;
  const currentSortLabel = monitoredSearchSortOptions.find((o) => o.value === sortValue)?.label || monitoredSearchSortOptions[0]?.label || 'Most relevant';

  const sortDropdownChildren = ({ close }: { close: () => void }) => (
    <div role="listbox" aria-label="Sort search results">
      {monitoredSearchSortOptions.map((option) => {
        const isSelected = option.value === sortValue;
        return (
          <button
            type="button"
            key={option.value}
            className={`w-full px-3 py-2 text-left text-base flex items-center justify-between gap-2 hover-surface ${isSelected ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''}`}
            onClick={() => { onSortChange(option.value); close(); }}
            role="option"
            aria-selected={isSelected}
          >
            <span>{option.label}</span>
            {isSelected ? (
              <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="w-4 h-4">
                <path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" />
              </svg>
            ) : null}
          </button>
        );
      })}
    </div>
  );

  return (
    <section className="rounded-none sm:rounded-2xl border-0 sm:border border-black/10 dark:border-white/10 bg-transparent sm:bg-white/80 sm:dark:bg-white/5 sm:shadow-xl sm:overflow-hidden flex flex-col" style={{ maxHeight: 'calc(100dvh - 8rem)' }}>
      {/* Single header row — tabs + controls, flex-wrap so controls fall below tabs on narrow screens */}
      <div className={`flex flex-wrap items-center pb-2 border-b border-black/10 dark:border-white/10 relative z-10 gap-3 gap-y-2 shrink-0 px-4 pt-4 ${hideHeader ? 'hidden' : ''}`}>

        {/* Left: back button + mobile label / desktop tab pills */}
        <div className="flex items-center gap-2 min-w-0">
          {/* Back button — desktop only, same as landing page */}
          <button
            type="button"
            onClick={onBack}
            className="hidden sm:block rounded-full p-1.5 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
            aria-label="Back to home"
            title="Back"
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 19.5 7.5 12 15 4.5" />
            </svg>
          </button>
          {/* Mobile: "Search Results n" label */}
          <div className="sm:hidden flex items-center gap-2">
            <span className="text-base font-bold text-gray-900 dark:text-gray-100">Search Results</span>
            <span className="inline-flex items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 text-xs font-semibold px-2 py-0.5 min-w-[1.5rem] leading-none">
              {searchScope === 'books' ? bookSearchResults.length : authorResults.length}
            </span>
          </div>
          {/* Desktop: tab pills — same container style as landing page */}
          <div className="hidden sm:inline-flex items-center rounded-full border border-[var(--border-muted)] bg-transparent">
            <button
              type="button"
              onClick={() => onTabChange('authors')}
              className="px-3.5 py-2 rounded-full text-sm font-medium transition-colors flex items-center gap-1.5 text-gray-700 dark:text-gray-200 hover-action"
            >
              <span>Monitored </span>Authors
              <span className="inline-flex items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 text-xs font-semibold px-1.5 py-0.5 leading-none min-w-[1.25rem]">{displayAuthorsCount}</span>
            </button>
            <button
              type="button"
              onClick={() => onTabChange('books')}
              className="px-3.5 py-2 rounded-full text-sm font-medium transition-colors flex items-center gap-1.5 text-gray-700 dark:text-gray-200 hover-action"
            >
              <span>Monitored </span>Books
              <span className="inline-flex items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 text-xs font-semibold px-1.5 py-0.5 leading-none min-w-[1.25rem]">{displayBooksCount}</span>
            </button>
            <button
              type="button"
              onClick={() => onTabChange('upcoming')}
              className="px-3.5 py-2 rounded-full text-sm font-medium transition-colors flex items-center gap-1.5 text-gray-700 dark:text-gray-200 hover-action"
            >
              Upcoming
              <span className="inline-flex items-center justify-center rounded-full bg-emerald-100 dark:bg-emerald-500/20 text-emerald-700 dark:text-emerald-400 text-xs font-semibold px-1.5 py-0.5 leading-none min-w-[1.25rem]">{displayUpcomingCount}</span>
            </button>
            {/* Search — active */}
            <button
              type="button"
              className="px-3.5 py-2 rounded-full text-sm font-medium transition-colors flex items-center gap-1.5 bg-emerald-600 text-white shadow-sm"
              aria-current="true"
            >
              Search
              <span className="inline-flex items-center justify-center rounded-full bg-white/25 text-white text-xs font-semibold px-1.5 py-0.5 leading-none min-w-[1.25rem]">{displaySearchCount}</span>
            </button>
          </div>
        </div>

        {/* Right: controls — Authors/Books scope | filter icon | view mode toggle */}
        <div className="flex items-center gap-2 justify-end ml-auto">
          {/* Authors / Books scope toggle */}
          <div className="inline-flex items-center rounded-full border border-[var(--border-muted)] bg-transparent shrink-0">
            <button
              type="button"
              onClick={() => onScopeChange('authors')}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${searchScope === 'authors' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
              aria-pressed={searchScope === 'authors'}
            >
              Authors
            </button>
            <button
              type="button"
              onClick={() => onScopeChange('books')}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${searchScope === 'books' ? 'bg-emerald-600 text-white shadow-sm' : 'text-gray-700 dark:text-gray-200 hover-action'}`}
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
                className={`rounded-full p-2 transition-colors hover-action ${isOpen ? 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-500/10' : 'text-gray-600 dark:text-gray-300'}`}
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 3c2.755 0 5.455.232 8.083.678.533.09.917.556.917 1.096v1.044a2.25 2.25 0 0 1-.659 1.591l-5.432 5.432a2.25 2.25 0 0 0-.659 1.591v2.927a2.25 2.25 0 0 1-1.244 2.013L9.75 21v-6.568a2.25 2.25 0 0 0-.659-1.591L3.659 7.409A2.25 2.25 0 0 1 3 5.818V4.774c0-.54.384-1.006.917-1.096A48.32 48.32 0 0 1 12 3Z" />
                </svg>
              </button>
            )}
          >
            {sortDropdownChildren}
          </Dropdown>
          {/* View mode toggle — far right */}
          {searchScope === 'authors' ? (
            <ViewModeToggle value={authorViewMode} onChange={onAuthorViewModeChange} options={authorSearchViewOptions} />
          ) : (
            <ViewModeToggle value={bookSearchViewMode} onChange={onBookSearchViewModeChange} options={bookSearchViewOptions} />
          )}
        </div>

      </div>

      <div className="flex-1 min-h-0 overflow-y-auto px-4 pt-3 pb-4">
        {!authorQuery.trim() && !isSearching ? (
        <div className="rounded-2xl border border-dashed border-black/10 dark:border-white/10 bg-white/60 dark:bg-white/5 px-4 py-8 text-center">
          <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Start a search from the top bar</div>
          <div className="mt-1 text-sm text-gray-500 dark:text-gray-400">Use the header search input to find authors or books to monitor.</div>
          <div className="mt-4">
            <button
              type="button"
              onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
              className="px-3 py-1.5 rounded-full text-xs font-medium bg-white/70 hover:bg-white text-gray-900 dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
            >
              Go to search bar
            </button>
          </div>
        </div>
      ) : searchScope === 'books' ? (
        bookSearchResults.length === 0 ? (
          <div className="text-sm text-gray-500 dark:text-gray-400">Search for a book to monitor.</div>
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
            onViewModeChange={(next) => onBookSearchViewModeChange(next === 'list' ? 'list' : 'compact')}
            customAction={{
              label: 'Monitor',
              onClick: (book) => onBookMonitorAction(book),
              isDisabled: (book) => isBookMonitored(book),
              getLabel: (book) => (isBookMonitored(book) ? 'Monitored' : 'Monitor'),
            }}
          />
        )
      ) : authorResults.length === 0 ? (
        <div className="text-sm text-gray-500 dark:text-gray-400">Search for an author to add.</div>
      ) : (
        authorViewMode === 'list' ? (
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
                  thumbnail={<RowThumbnail url={author.photo_url} alt={name || 'Unknown author'} kind="author" />}
                  onOpen={() => onAuthorNavigate(author)}
                  trailingAction={(
                    <button
                      type="button"
                      onClick={() => onMonitorAuthor({
                        name,
                        provider: author.provider,
                        provider_id: author.provider_id,
                        photo_url: author.photo_url || undefined,
                        books_count: typeof author.stats?.books_count === 'number' ? author.stats?.books_count : undefined,
                      })}
                      disabled={isMonitored}
                      className="px-3 py-1 rounded-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white text-xs font-medium"
                    >
                      {isMonitored ? 'Monitored' : 'Monitor'}
                    </button>
                  )}
                />
              );
            })}
          </div>
        ) : (
          <div className="grid gap-4 items-start" style={compactGridStyle}>
            {displayedAuthors.map((author, index) => {
              const name = author.name;
              const isMonitored = monitoredNames.has(name.toLowerCase());
              const booksCount = author.stats?.books_count;
              const subtitle = typeof booksCount === 'number' ? `${booksCount} books` : 'Unknown';
              return (
                <div
                  key={`${author.provider}:${author.provider_id}`}
                  className="animate-pop-up will-change-transform"
                  style={{ animationDelay: `${index * 30}ms` }}
                >
                  <MonitoredAuthorCompactTile
                    name={name || 'Unknown author'}
                    thumbnail={<RowThumbnail url={author.photo_url} alt={name || 'Author photo'} kind="author" className="w-full aspect-[2/3]" />}
                    subtitle={subtitle}
                    onOpenDetails={() => onAuthorNavigate(author)}
                    footer={(
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          onMonitorAuthor({
                            name,
                            provider: author.provider,
                            provider_id: author.provider_id,
                            photo_url: author.photo_url || undefined,
                            books_count: typeof author.stats?.books_count === 'number' ? author.stats?.books_count : undefined,
                          });
                        }}
                        disabled={isMonitored}
                        className="inline-flex items-center justify-center gap-1.5 rounded text-white transition-all duration-200 px-2.5 py-1.5 text-xs w-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60"
                      >
                        <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                          <path strokeLinecap="round" strokeLinejoin="round" d="M12 4.5v15m7.5-7.5h-15" />
                        </svg>
                        {isMonitored ? 'Monitored' : 'Monitor'}
                      </button>
                    )}
                  />
                </div>
              );
            })}
          </div>
        )
      )}
      </div>
    </section>
  );
}
