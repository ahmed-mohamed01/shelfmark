import React, { useState, useEffect, useCallback, useMemo } from 'react';
import {
  listMonitoredEvents,
  getMonitoredEventStats,
  deleteMonitoredEvents,
  exportMonitoredEventsCsv,
  MonitoredEvent,
  MonitoredEventStats,
} from '../services/monitoredApi';
import { MonitoredEventRow, parseEventMeta, formatEventDate } from './MonitoredEventRow';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FilterCategory = 'all' | 'downloads' | 'searches' | 'syncs' | 'authors' | 'failures';

const FILTER_EVENT_TYPES: Record<FilterCategory, string[] | null> = {
  all: null,
  downloads: ['download_queued', 'download_complete', 'download_failed'],
  searches: ['search_queued', 'search_no_match', 'search_below_cutoff', 'search_not_released', 'search_result'],
  syncs: ['author_synced', 'author_sync_failed'],
  authors: ['author_added', 'author_removed'],
  failures: ['download_failed', 'author_sync_failed'],
};


function dateRangeToSince(value: string): string | undefined {
  if (!value) return undefined;
  const now = new Date();
  if (value === 'today') {
    return new Date(now.getFullYear(), now.getMonth(), now.getDate()).toISOString();
  }
  if (value === '7d') {
    return new Date(now.getTime() - 7 * 86400000).toISOString();
  }
  if (value === '30d') {
    return new Date(now.getTime() - 30 * 86400000).toISOString();
  }
  return undefined;
}

// ---------------------------------------------------------------------------
// Date grouping (HistoryTab-specific)
// ---------------------------------------------------------------------------

function groupLabel(iso: string): string {
  const date = new Date(iso);
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const yesterday = new Date(today.getTime() - 86400000);
  if (date >= today) return 'Today';
  if (date >= yesterday) return 'Yesterday';
  return 'Earlier';
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface MonitoredHistoryTabProps {
  onShowToast?: (message: string, type?: 'info' | 'success' | 'error', persistent?: boolean) => string | void;
  exportRef?: React.MutableRefObject<(() => void) | null>;
  clearRef?: React.MutableRefObject<(() => void) | null>;
  dateRange?: string;
}

export const MonitoredHistoryTab = ({ onShowToast, exportRef, clearRef, dateRange: dateRangeProp }: MonitoredHistoryTabProps) => {
  const [events, setEvents] = useState<MonitoredEvent[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [stats, setStats] = useState<MonitoredEventStats | null>(null);

  const [filter, setFilter] = useState<FilterCategory>('all');
  const [offset, setOffset] = useState(0);
  const PAGE_SIZE = 50;
  const dateRange = dateRangeProp ?? '';

  const since = useMemo(() => dateRangeToSince(dateRange), [dateRange]);

  const fetchEvents = useCallback(async (resetOffset = true) => {
    setLoading(true);
    setError(null);
    const newOffset = resetOffset ? 0 : offset;
    if (resetOffset) setOffset(0);
    try {
      const eventTypes = FILTER_EVENT_TYPES[filter];
      const resp = await listMonitoredEvents({
        event_types: eventTypes ? eventTypes.join(',') : undefined,
        since,
        limit: PAGE_SIZE,
        offset: newOffset,
      });
      if (resetOffset) {
        setEvents(resp.events);
      } else {
        setEvents(prev => [...prev, ...resp.events]);
      }
      setTotal(resp.total);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load history');
    } finally {
      setLoading(false);
    }
  }, [filter, since, offset]);

  const fetchStats = useCallback(async () => {
    try {
      const s = await getMonitoredEventStats(since);
      setStats(s);
    } catch { /* ignore */ }
  }, [since]);

  useEffect(() => {
    void fetchEvents(true);
    void fetchStats();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [filter, since]);

  const loadMore = useCallback(() => {
    const newOffset = offset + PAGE_SIZE;
    setOffset(newOffset);
    void (async () => {
      setLoading(true);
      try {
        const eventTypes = FILTER_EVENT_TYPES[filter];
        const resp = await listMonitoredEvents({
          event_types: eventTypes ? eventTypes.join(',') : undefined,
          since,
          limit: PAGE_SIZE,
          offset: newOffset,
        });
        setEvents(prev => [...prev, ...resp.events]);
        setTotal(resp.total);
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Failed to load more');
      } finally {
        setLoading(false);
      }
    })();
  }, [offset, filter, since]);

  const handleClearHistory = useCallback(async () => {
    if (!confirm('Clear all monitored history? This cannot be undone.')) return;
    try {
      const result = await deleteMonitoredEvents();
      onShowToast?.(`Cleared ${result.deleted} event(s)`, 'success');
      void fetchEvents(true);
      void fetchStats();
    } catch {
      onShowToast?.('Failed to clear history', 'error');
    }
  }, [fetchEvents, fetchStats, onShowToast]);

  const handleExport = useCallback(() => {
    const eventTypes = FILTER_EVENT_TYPES[filter];
    exportMonitoredEventsCsv({
      event_types: eventTypes ? eventTypes.join(',') : undefined,
      since,
    });
  }, [filter, since]);

  // Expose export/clear to parent via refs
  useEffect(() => {
    if (exportRef) exportRef.current = handleExport;
    if (clearRef) clearRef.current = handleClearHistory;
  }, [exportRef, clearRef, handleExport, handleClearHistory]);

  const [expandedBatches, setExpandedBatches] = useState<Set<string>>(new Set());

  // Collapse sync events with the same batch_id into grouped display items
  type DisplayItem = { kind: 'single'; event: MonitoredEvent } | { kind: 'batch'; batchId: string; events: MonitoredEvent[]; totalAdded: number; totalRemoved: number; totalBooks: number };

  const displayItems = useMemo<DisplayItem[]>(() => {
    const items: DisplayItem[] = [];
    const batchMap = new Map<string, MonitoredEvent[]>();
    const batchOrder: string[] = [];

    for (const ev of events) {
      const meta = parseEventMeta(ev);
      const bid = meta?.batch_id;
      if (bid && (ev.event_type === 'author_synced' || ev.event_type === 'author_sync_failed')) {
        if (!batchMap.has(bid)) {
          batchMap.set(bid, []);
          batchOrder.push(bid);
        }
        batchMap.get(bid)!.push(ev);
      } else {
        // Flush any pending batch that appeared before this non-batch event
        // (batches are contiguous in time-sorted results)
        items.push({ kind: 'single', event: ev });
      }
    }

    // Build batch items and interleave at correct position
    // Since events are sorted desc, batch items go where the first event of the batch was
    const batchItems = new Map<string, DisplayItem>();
    for (const bid of batchOrder) {
      const batchEvents = batchMap.get(bid)!;
      const totalAdded = batchEvents.reduce((sum, e) => { const m = parseEventMeta(e); return sum + (m?.books_added || 0); }, 0);
      const totalRemoved = batchEvents.reduce((sum, e) => { const m = parseEventMeta(e); return sum + (m?.books_removed || 0); }, 0);
      const totalBooks = batchEvents.reduce((sum, e) => { const m = parseEventMeta(e); return sum + (m?.total_books || 0); }, 0);
      batchItems.set(bid, { kind: 'batch', batchId: bid, events: batchEvents, totalAdded, totalRemoved, totalBooks });
    }

    // Re-walk events to produce correct ordering with batches collapsed
    const result: DisplayItem[] = [];
    const seenBatches = new Set<string>();
    for (const ev of events) {
      const meta = parseEventMeta(ev);
      const bid = meta?.batch_id;
      if (bid && batchItems.has(bid)) {
        if (!seenBatches.has(bid)) {
          seenBatches.add(bid);
          result.push(batchItems.get(bid)!);
        }
      } else {
        result.push({ kind: 'single', event: ev });
      }
    }
    return result;
  }, [events]);

  // Group display items by date
  const grouped = useMemo(() => {
    const groups: { label: string; items: DisplayItem[] }[] = [];
    let currentLabel = '';
    for (const item of displayItems) {
      const ts = item.kind === 'single' ? item.event.created_at : item.events[0].created_at;
      const label = groupLabel(ts);
      if (label !== currentLabel) {
        currentLabel = label;
        groups.push({ label, items: [] });
      }
      groups[groups.length - 1].items.push(item);
    }
    return groups;
  }, [displayItems]);

  const hasMore = events.length < total;

  // ── Stat cards (clickable as filters) ──
  const statCards: { label: string; value: number; color: string; activeColor: string; filterKey: FilterCategory }[] = stats ? [
    { label: 'Downloads', value: stats.downloads, color: 'text-emerald-500', activeColor: 'ring-emerald-500 bg-emerald-500/10', filterKey: 'downloads' },
    { label: 'Searches', value: stats.searches, color: 'text-blue-500', activeColor: 'ring-blue-500 bg-blue-500/10', filterKey: 'searches' },
    { label: 'Syncs', value: stats.syncs, color: 'text-purple-500', activeColor: 'ring-purple-500 bg-purple-500/10', filterKey: 'syncs' },
    { label: 'Author Changes', value: stats.authors_added + stats.authors_removed, color: 'text-cyan-500', activeColor: 'ring-cyan-500 bg-cyan-500/10', filterKey: 'authors' },
    { label: 'Failures', value: stats.failures, color: 'text-red-500', activeColor: 'ring-red-500 bg-red-500/10', filterKey: 'failures' },
  ] : [];

  return (
    <div className="space-y-4">
      {/* Stats dashboard — clickable to filter */}
      {statCards.length > 0 ? (
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
          {statCards.map(s => {
            const isActive = filter === s.filterKey;
            return (
              <button
                key={s.label}
                type="button"
                onClick={() => setFilter(isActive ? 'all' : s.filterKey)}
                className={`rounded-xl border p-3 text-center transition-all cursor-pointer ${
                  isActive
                    ? `ring-2 ${s.activeColor} border-transparent`
                    : 'border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] hover:bg-black/5 dark:hover:bg-white/5'
                }`}
              >
                <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
                <div className="text-[10px] uppercase tracking-wide text-gray-500 dark:text-gray-400 mt-1">{s.label}</div>
              </button>
            );
          })}
        </div>
      ) : null}


      {/* Timeline */}
      {error ? (
        <div className="text-sm text-red-500 text-center py-8">{error}</div>
      ) : events.length === 0 && !loading ? (
        <div className="text-sm text-gray-500 dark:text-gray-400 text-center py-8">No events found.</div>
      ) : (
        <div className="space-y-4">
          {grouped.map(group => (
            <div key={group.label}>
              <div className="flex items-center gap-2 mb-2">
                <span className="text-xs font-semibold uppercase tracking-wide text-gray-500 dark:text-gray-400">{group.label}</span>
                <span className="text-[10px] text-gray-400">({group.items.length})</span>
              </div>
              <div className="rounded-2xl border border-[var(--border-muted)] overflow-hidden divide-y divide-gray-200/60 dark:divide-gray-800/60">
                {group.items.map(item => {
                  if (item.kind === 'batch') {
                    const isExpanded = expandedBatches.has(item.batchId);
                    const failCount = item.events.filter(e => e.event_type === 'author_sync_failed').length;
                    const successCount = item.events.length - failCount;
                    return (
                      <div key={`batch-${item.batchId}`}>
                        <button
                          type="button"
                          onClick={() => setExpandedBatches(prev => { const next = new Set(prev); if (next.has(item.batchId)) next.delete(item.batchId); else next.add(item.batchId); return next; })}
                          className="w-full px-4 py-3 flex items-start gap-3 hover:bg-black/5 dark:hover:bg-white/5 transition-colors text-left"
                        >
                          <div className="mt-0.5 flex-shrink-0 text-purple-500">
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 3.182m0-4.991v4.99" /></svg>
                          </div>
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center gap-2 flex-wrap">
                              <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-purple-500/20 text-purple-700 dark:text-purple-300">
                                Author Refresh
                              </span>
                              <span className="text-[10px] text-gray-400">{item.events.length} authors</span>
                            </div>
                            <div className="mt-1 text-sm text-gray-700 dark:text-gray-200">
                              <span className="font-medium">Refreshed {successCount} author{successCount !== 1 ? 's' : ''}</span>
                              {failCount > 0 ? <span className="text-red-500"> · {failCount} failed</span> : null}
                            </div>
                            <div className="mt-0.5 text-[11px] text-gray-500">
                              {item.totalBooks > 0 ? `${item.totalBooks} books tracked` : ''}
                              {item.totalAdded > 0 ? ` · +${item.totalAdded} new` : ''}
                              {item.totalRemoved > 0 ? ` · -${item.totalRemoved} removed` : ''}
                              {item.totalBooks === 0 && item.totalAdded === 0 && item.totalRemoved === 0 ? 'No changes' : ''}
                            </div>
                          </div>
                          <div className="flex items-center gap-2 flex-shrink-0">
                            <span className="text-[10px] text-gray-400 whitespace-nowrap">{formatEventDate(item.events[0].created_at)}</span>
                            <svg className={`w-3.5 h-3.5 text-gray-400 transition-transform duration-200 ${isExpanded ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
                          </div>
                        </button>
                        {isExpanded ? (
                          <div className="border-t border-gray-200/40 dark:border-gray-800/40 bg-black/[0.02] dark:bg-white/[0.02]">
                            {item.events.map(ev => {
                              const meta = parseEventMeta(ev);
                              const isFail = ev.event_type === 'author_sync_failed';
                              return (
                                <div key={ev.id} className={`px-4 py-2 pl-11 ${isFail ? 'bg-red-500/5' : ''}`}>
                                  <div className="flex items-center justify-between gap-2">
                                    <div className="text-xs text-gray-700 dark:text-gray-200">
                                      <span className={`font-medium ${isFail ? 'text-red-600 dark:text-red-400' : ''}`}>{ev.author_name || 'Unknown'}</span>
                                      {!isFail && meta?.books_added ? <span className="text-gray-500"> · +{meta.books_added} new</span> : null}
                                      {!isFail && meta?.books_removed ? <span className="text-gray-500"> · -{meta.books_removed} removed</span> : null}
                                      {!isFail && !meta?.books_added && !meta?.books_removed ? <span className="text-gray-400"> · no changes</span> : null}
                                    </div>
                                    {isFail ? <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide bg-red-500/20 text-red-700 dark:text-red-300">failed</span> : null}
                                  </div>
                                  {isFail && meta?.error_message ? <div className="mt-0.5 text-[11px] text-red-600 dark:text-red-300 break-words">{meta.error_message}</div> : null}
                                </div>
                              );
                            })}
                          </div>
                        ) : null}
                      </div>
                    );
                  }

                  return <MonitoredEventRow key={item.event.id} event={item.event} />;
                })}
              </div>
            </div>
          ))}

          {/* Load more */}
          {hasMore ? (
            <div className="text-center">
              <button
                type="button"
                onClick={loadMore}
                disabled={loading}
                className="px-4 py-2 rounded-lg text-xs font-medium bg-gray-500/10 text-gray-600 dark:text-gray-300 hover:bg-gray-500/20 transition-colors disabled:opacity-50"
              >
                {loading ? 'Loading…' : `Load more (${events.length} of ${total})`}
              </button>
            </div>
          ) : null}

          {loading && events.length === 0 ? (
            <div className="text-sm text-gray-500 dark:text-gray-400 text-center py-8">Loading history…</div>
          ) : null}
        </div>
      )}
    </div>
  );
};
