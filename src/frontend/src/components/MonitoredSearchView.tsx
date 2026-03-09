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

  return (
    <section className="rounded-2xl border border-black/10 dark:border-white/10 bg-white/80 dark:bg-white/5 p-4">
      <div className="flex items-center justify-between mb-3 pb-2 border-b border-black/10 dark:border-white/10 gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <h2 className="text-base sm:text-lg font-semibold tracking-tight text-gray-900 dark:text-gray-100 truncate">
            Search Results
          </h2>
          <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-medium bg-black/5 dark:bg-white/10 text-gray-600 dark:text-gray-300">
            {searchScope === 'books' ? bookSearchResults.length : authorResults.length}
          </span>
          {searchScope === 'books' ? (
            <Dropdown
              align="left"
              widthClassName="w-60 sm:w-72"
              renderTrigger={({ isOpen, toggle }) => (
                <button
                  type="button"
                  onClick={toggle}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium bg-white/70 hover:bg-white text-gray-900 dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100 ${isOpen ? 'ring-1 ring-emerald-500/50' : ''}`}
                  title="Sort"
                  aria-label="Sort"
                  aria-haspopup="listbox"
                  aria-expanded={isOpen}
                >
                  {monitoredSearchSortOptions.find((option) => option.value === bookSearchSortValue)?.label || monitoredSearchSortOptions[0]?.label || 'Most relevant'}
                </button>
              )}
            >
              {({ close }) => (
                <div role="listbox" aria-label="Sort search results">
                  {monitoredSearchSortOptions.map((option) => {
                    const isSelected = option.value === bookSearchSortValue;
                    return (
                      <button
                        type="button"
                        key={option.value}
                        className={`w-full px-3 py-2 text-left text-base flex items-center justify-between gap-2 hover-surface ${
                          isSelected ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''
                        }`}
                        onClick={() => {
                          onBookSortChange(option.value);
                          close();
                        }}
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
              )}
            </Dropdown>
          ) : (
            <Dropdown
              align="left"
              widthClassName="w-60 sm:w-72"
              renderTrigger={({ isOpen, toggle }) => (
                <button
                  type="button"
                  onClick={toggle}
                  className={`px-3 py-1.5 rounded-full text-xs font-medium bg-white/70 hover:bg-white text-gray-900 dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100 ${isOpen ? 'ring-1 ring-emerald-500/50' : ''}`}
                  title="Sort"
                  aria-label="Sort"
                  aria-haspopup="listbox"
                  aria-expanded={isOpen}
                >
                  {monitoredSearchSortOptions.find((o) => o.value === authorSearchSortValue)?.label || monitoredSearchSortOptions[0]?.label || 'Most relevant'}
                </button>
              )}
            >
              {({ close }) => (
                <div role="listbox" aria-label="Sort search results">
                  {monitoredSearchSortOptions.map((option) => {
                    const isSelected = option.value === authorSearchSortValue;
                    return (
                      <button
                        type="button"
                        key={option.value}
                        className={`w-full px-3 py-2 text-left text-base flex items-center justify-between gap-2 hover-surface ${isSelected ? 'text-emerald-600 dark:text-emerald-400 font-medium' : ''}`}
                        onClick={() => { onAuthorSortChange(option.value); close(); }}
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
              )}
            </Dropdown>
          )}
        </div>
        <div className="shrink-0">
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
    </section>
  );
}
