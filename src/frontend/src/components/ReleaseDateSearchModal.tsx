import { useState, useEffect, useCallback, useId } from 'react';
import { searchReleaseDates, setBookReleaseDate, type ReleaseDateSearchResult, type MonitoredBookRow } from '../services/monitoredApi';

interface ReleaseDateSearchModalProps {
  book: MonitoredBookRow;
  entityId: number;
  onClose: () => void;
  onMatched: (releaseDate: string | null) => void;
}

export default function ReleaseDateSearchModal({ book, entityId, onClose, onMatched }: ReleaseDateSearchModalProps) {
  const titleId = useId();
  const [query, setQuery] = useState(book.title || '');
  const [authorQuery, setAuthorQuery] = useState(book.authors || '');
  const [results, setResults] = useState<ReleaseDateSearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  const [savingIndex, setSavingIndex] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const doSearch = useCallback(async () => {
    if (!query.trim() && !authorQuery.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await searchReleaseDates(query.trim(), authorQuery.trim() || undefined);
      setResults(res);
      if (res.length === 0) setError('No results found');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Search failed');
    } finally {
      setLoading(false);
    }
  }, [query, authorQuery]);

  useEffect(() => { void doSearch(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const handleSelect = async (result: ReleaseDateSearchResult, index: number) => {
    setSavingIndex(index);
    try {
      await setBookReleaseDate(
        entityId,
        book.provider || '',
        book.provider_book_id || '',
        result.asin,
        result.release_date,
      );
      onMatched(result.release_date);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to save');
      setSavingIndex(null);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/50 backdrop-blur-xs" onClick={onClose} />
      <div
        className="relative w-full max-w-2xl max-h-[80vh] flex flex-col rounded-xl border border-(--border-muted) shadow-2xl settings-modal-enter"
        style={{ background: 'var(--bg)' }}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
      >
        <header className="flex items-center justify-between border-b border-(--border-muted) px-6 py-4 shrink-0">
          <h3 id={titleId} className="text-lg font-semibold">Set Release Date</h3>
          <button type="button" onClick={onClose} className="p-1.5 rounded-lg hover:bg-(--hover-surface) transition-colors" aria-label="Close">
            <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
            </svg>
          </button>
        </header>

        <div className="flex gap-2 px-6 py-3 border-b border-(--border-muted) shrink-0">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            placeholder="Title"
            className="flex-1 px-3 py-1.5 text-sm rounded-lg border border-(--border-muted) bg-(--bg) focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <input
            type="text"
            value={authorQuery}
            onChange={(e) => setAuthorQuery(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && doSearch()}
            placeholder="Author"
            className="flex-1 px-3 py-1.5 text-sm rounded-lg border border-(--border-muted) bg-(--bg) focus:outline-none focus:ring-1 focus:ring-blue-500"
          />
          <button
            type="button"
            onClick={doSearch}
            disabled={loading}
            className="px-4 py-1.5 text-sm font-medium rounded-lg bg-blue-600 text-white hover:bg-blue-700 disabled:opacity-50 transition-colors"
          >
            {loading ? 'Searching...' : 'Search'}
          </button>
        </div>

        <div className="flex-1 overflow-y-auto px-6 py-3">
          {error && <p className="text-sm text-red-500 mb-3">{error}</p>}

          {results.map((r, i) => (
            <div key={r.asin || `gb-${i}`} className="flex gap-3 py-3 border-b border-(--border-muted) last:border-b-0">
              {r.cover_url && (
                <img src={r.cover_url} alt="" className="w-12 h-16 object-cover rounded shrink-0" />
              )}
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium truncate">{r.title}</p>
                  <span className={`shrink-0 text-[10px] px-1.5 py-0.5 rounded font-medium uppercase ${r.source === 'google' ? 'bg-blue-100 text-blue-700 dark:bg-blue-900/40 dark:text-blue-300' : 'bg-orange-100 text-orange-700 dark:bg-orange-900/40 dark:text-orange-300'}`}>
                    {r.source === 'google' ? 'Google' : 'Audible'}
                  </span>
                </div>
                {r.authors.length > 0 && (
                  <p className="text-xs text-(--text-muted) truncate">{r.authors.join(', ')}</p>
                )}
                {r.series_name && (
                  <p className="text-xs text-(--text-muted)">{r.series_name}</p>
                )}
                <p className={`text-xs font-medium mt-0.5 ${r.release_date ? 'text-emerald-600 dark:text-emerald-400' : 'text-(--text-muted)'}`}>
                  {r.release_date || (r.publish_year ? `${r.publish_year} (month unknown)` : 'No release date')}
                </p>
              </div>
              <button
                type="button"
                onClick={() => handleSelect(r, i)}
                disabled={savingIndex !== null}
                className="self-center px-3 py-1.5 text-xs font-medium rounded-lg border border-(--border-muted) hover:bg-(--hover-surface) disabled:opacity-50 transition-colors shrink-0"
              >
                {savingIndex === i ? 'Saving...' : 'Select'}
              </button>
            </div>
          ))}

          {!loading && results.length === 0 && !error && (
            <p className="text-sm text-(--text-muted) text-center py-8">Search to find release dates</p>
          )}
        </div>
      </div>
    </div>
  );
}
