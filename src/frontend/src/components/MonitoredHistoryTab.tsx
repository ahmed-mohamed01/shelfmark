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
import { MonitoredEventSessionRow, SessionLatestStatus } from './MonitoredEventSessionRow';
import { useRealtimeStatus } from '../hooks/useRealtimeStatus';
import { Book } from '../types';
import { showConfirm } from './ConfirmDialog';
import { StackedThumbnails, StackedThumb } from './StackedThumbnails';
import { STATUS_BADGE_STYLES } from './activity/activityStyles';
import { ActivityVisualStatus } from './activity/activityTypes';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type FilterCategory = 'all' | 'downloads' | 'searches' | 'syncs' | 'authors' | 'failures';

const FILTER_EVENT_TYPES: Record<FilterCategory, string[] | null> = {
  all: null,
  downloads: ['download_queued', 'download_complete', 'download_failed', 'file_imported'],
  searches: ['search_started', 'search_queued', 'search_no_match', 'search_below_cutoff', 'search_not_released', 'search_result'],
  syncs: ['author_synced', 'author_sync_failed'],
  authors: ['author_added', 'author_removed'],
  failures: ['download_failed', 'author_sync_failed'],
};

const SESSION_EVENT_TYPES = new Set([
  'search_started', 'search_queued', 'search_no_match', 'search_below_cutoff',
  'search_not_released', 'search_result',
  'download_queued', 'download_complete', 'download_failed', 'file_imported',
]);


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
    if (!(await showConfirm({
      title: 'Clear history',
      message: 'Clear all monitored history? This cannot be undone.',
      confirmLabel: 'Clear',
      destructive: true,
    }))) return;
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
  const [expandedRuns, setExpandedRuns] = useState<Set<string>>(new Set());

  // Collapse sync events with the same batch_id, download lifecycle events with
  // the same session_id, and per-book sessions tied to a monitored_run_started
  // event into grouped display items.
  type SessionDisplayItem = {
    kind: 'session';
    sessionId: string;
    events: MonitoredEvent[];
    bookTitle: string | null;
    authorName: string | null;
    latestStatus: SessionLatestStatus;
    activeTaskId: string | null;
    firstAt: string;
    lastAt: string;
    isActive: boolean;
  };
  type RunDisplayItem = {
    kind: 'run';
    runId: string;
    trigger: 'scheduled' | 'manual';
    totalCandidates: number;
    slot: string | null;
    startEvent: MonitoredEvent | null;
    sessions: SessionDisplayItem[];
    firstAt: string;
    lastAt: string;
  };
  type DisplayItem =
    | { kind: 'single'; event: MonitoredEvent }
    | { kind: 'batch'; batchId: string; events: MonitoredEvent[]; totalAdded: number; totalRemoved: number; totalBooks: number }
    | SessionDisplayItem
    | RunDisplayItem;

  // Live download progress (read-only consumption of upstream hook)
  const { status: liveStatus } = useRealtimeStatus({ pollInterval: 5000 });

  const liveTaskIds = useMemo(() => {
    const ids = new Set<string>();
    for (const cat of ['queued', 'resolving', 'locating', 'downloading'] as const) {
      const bucket = liveStatus[cat];
      if (bucket) Object.keys(bucket).forEach(id => ids.add(id));
    }
    return ids;
  }, [liveStatus]);

  const lookupLiveBook = (taskId: string): Book | null => (
    (liveStatus.downloading?.[taskId] ?? null) ||
    (liveStatus.locating?.[taskId] ?? null) ||
    (liveStatus.resolving?.[taskId] ?? null) ||
    (liveStatus.queued?.[taskId] ?? null) ||
    null
  );

  const renderSession = (
    item: Extract<DisplayItem, { kind: 'session' }>,
    defaultExpanded = false,
  ) => (
    <MonitoredEventSessionRow
      key={`session-${item.sessionId}`}
      events={item.events}
      bookTitle={item.bookTitle}
      authorName={item.authorName}
      latestStatus={item.latestStatus}
      isActive={item.isActive}
      liveBook={item.activeTaskId ? lookupLiveBook(item.activeTaskId) : null}
      defaultExpanded={defaultExpanded}
    />
  );

  // Pass 1: group events into sessions/batches/singles. Heavy work — depends
  // only on the events array, NOT on liveTaskIds, so it doesn't re-run on every
  // WebSocket tick. Sessions are emitted with isActive=false; pass 2 annotates.
  const baseDisplayItems = useMemo<DisplayItem[]>(() => {
    const sessionMap = new Map<string, MonitoredEvent[]>();
    const batchMap = new Map<string, MonitoredEvent[]>();
    const runStartedById = new Map<string, MonitoredEvent>();
    const sessionRunMap = new Map<string, string>(); // sessionId -> runId (from search_started.metadata)
    const sessionsByRun = new Map<string, Set<string>>(); // runId -> sessionIds

    // Pre-scan: catalog run_started events and link sessions to runs via the
    // session anchor's metadata. Runs with no run_started event in the loaded
    // window won't surface as parents; their sessions render standalone.
    for (const ev of events) {
      if (ev.event_type === 'monitored_run_started') {
        const meta = parseEventMeta(ev);
        const runId = meta?.run_id as string | undefined;
        if (runId && !runStartedById.has(runId)) {
          runStartedById.set(runId, ev);
        }
      }
      if (ev.event_type === 'search_started' && ev.session_id) {
        const meta = parseEventMeta(ev);
        const runId = meta?.run_id as string | undefined;
        if (runId) {
          sessionRunMap.set(ev.session_id, runId);
          if (!sessionsByRun.has(runId)) sessionsByRun.set(runId, new Set());
          sessionsByRun.get(runId)!.add(ev.session_id);
        }
      }
    }

    type Anchor = { kind: 'session' | 'batch' | 'single' | 'run'; key: string };
    const anchors: Anchor[] = [];
    const seenSessions = new Set<string>();
    const seenBatches = new Set<string>();
    const seenRuns = new Set<string>();

    for (const ev of events) {
      const meta = parseEventMeta(ev);
      const sid = ev.session_id;
      const bid = meta?.batch_id as string | undefined;

      if (ev.event_type === 'monitored_run_started') {
        const runId = meta?.run_id as string | undefined;
        if (runId && !seenRuns.has(runId)) {
          seenRuns.add(runId);
          anchors.push({ kind: 'run', key: runId });
        }
        continue;
      }

      if (sid && SESSION_EVENT_TYPES.has(ev.event_type)) {
        if (!sessionMap.has(sid)) sessionMap.set(sid, []);
        sessionMap.get(sid)!.push(ev);

        const linkedRun = sessionRunMap.get(sid);
        if (linkedRun && runStartedById.has(linkedRun)) {
          // Session is part of a known run; emit run anchor at this position
          // if not yet placed (handles loaded windows where run_started lives
          // later in the chronological tail than the session).
          if (!seenRuns.has(linkedRun)) {
            seenRuns.add(linkedRun);
            anchors.push({ kind: 'run', key: linkedRun });
          }
          continue;
        }

        if (!seenSessions.has(sid)) {
          seenSessions.add(sid);
          anchors.push({ kind: 'session', key: sid });
        }
      } else if (bid && (ev.event_type === 'author_synced' || ev.event_type === 'author_sync_failed')) {
        if (!batchMap.has(bid)) batchMap.set(bid, []);
        batchMap.get(bid)!.push(ev);
        if (!seenBatches.has(bid)) {
          seenBatches.add(bid);
          anchors.push({ kind: 'batch', key: bid });
        }
      } else {
        anchors.push({ kind: 'single', key: String(ev.id) });
      }
    }

    const buildSessionItem = (sessionId: string, evs: MonitoredEvent[]): SessionDisplayItem => {
      const latest = evs[0];
      const earliest = evs[evs.length - 1];
      const queuedEv = evs.find(e => e.event_type === 'download_queued');
      const queuedMeta = queuedEv ? parseEventMeta(queuedEv) : null;
      const taskId = (queuedMeta?.task_id as string | undefined) ?? null;
      const hasComplete = evs.some(e => e.event_type === 'download_complete' || e.event_type === 'file_imported');
      const hasFailed = evs.some(e => e.event_type === 'download_failed');
      const hasQueued = !!queuedEv;
      const latestStatus: SessionLatestStatus =
        hasComplete ? 'complete' :
        hasFailed ? 'failed' :
        hasQueued ? 'downloading' : 'searching';
      return {
        kind: 'session',
        sessionId,
        events: evs,
        bookTitle: latest.book_title ?? earliest.book_title ?? null,
        authorName: latest.author_name ?? earliest.author_name ?? null,
        latestStatus,
        activeTaskId: taskId,
        firstAt: earliest.created_at,
        lastAt: latest.created_at,
        isActive: false, // annotated in pass 2
      };
    };

    const result: DisplayItem[] = [];
    const eventById = new Map<string, MonitoredEvent>();
    for (const ev of events) eventById.set(String(ev.id), ev);

    for (const a of anchors) {
      if (a.kind === 'single') {
        const ev = eventById.get(a.key);
        if (ev) result.push({ kind: 'single', event: ev });
      } else if (a.kind === 'batch') {
        const evs = batchMap.get(a.key)!;
        const totalAdded = evs.reduce((sum, e) => { const m = parseEventMeta(e); return sum + (m?.books_added || 0); }, 0);
        const totalRemoved = evs.reduce((sum, e) => { const m = parseEventMeta(e); return sum + (m?.books_removed || 0); }, 0);
        const totalBooks = evs.reduce((sum, e) => { const m = parseEventMeta(e); return sum + (m?.total_books || 0); }, 0);
        result.push({ kind: 'batch', batchId: a.key, events: evs, totalAdded, totalRemoved, totalBooks });
      } else if (a.kind === 'run') {
        const startEvent = runStartedById.get(a.key) || null;
        const startMeta = startEvent ? parseEventMeta(startEvent) : null;
        const trigger = ((startMeta?.trigger as string) === 'scheduled' ? 'scheduled' : 'manual') as 'scheduled' | 'manual';
        const totalCandidates = (startMeta?.total_candidates as number | undefined) || 0;
        const slot = (startMeta?.slot as string | null | undefined) || null;
        const childIds = sessionsByRun.get(a.key) || new Set<string>();
        const childSessions: SessionDisplayItem[] = [];
        for (const sid of childIds) {
          const evs = sessionMap.get(sid);
          if (evs && evs.length > 0) childSessions.push(buildSessionItem(sid, evs));
        }
        // Newest first inside the run, matching the timeline's overall ordering.
        childSessions.sort((s1, s2) => s2.lastAt.localeCompare(s1.lastAt));
        const allTimes: string[] = childSessions.flatMap(s => [s.firstAt, s.lastAt]);
        if (startEvent) allTimes.push(startEvent.created_at);
        const firstAt = allTimes.length ? allTimes.reduce((a, b) => a < b ? a : b) : new Date().toISOString();
        const lastAt = allTimes.length ? allTimes.reduce((a, b) => a > b ? a : b) : firstAt;
        result.push({
          kind: 'run',
          runId: a.key,
          trigger,
          totalCandidates,
          slot,
          startEvent,
          sessions: childSessions,
          firstAt,
          lastAt,
        });
      } else {
        const evs = sessionMap.get(a.key)!;
        result.push(buildSessionItem(a.key, evs));
      }
    }

    // Drop single file_imported events when a matching download_complete single
    // exists for the same book (matched by entity_id/provider/provider_id within
    // 60s) — they convey the same outcome to the user. Session-grouped rows
    // already collapse naturally, so this only affects manual one-off downloads
    // without a session_id.
    const completeKeys = new Set<string>();
    for (const item of result) {
      if (item.kind === 'single' && item.event.event_type === 'download_complete') {
        const ev = item.event;
        completeKeys.add(`${ev.entity_id}|${ev.book_provider}|${ev.book_provider_id}`);
      }
    }
    if (completeKeys.size === 0) return result;
    const droppedImportedIds = new Set<number>();
    for (const item of result) {
      if (item.kind !== 'single' || item.event.event_type !== 'file_imported') continue;
      const fi = item.event;
      if (!completeKeys.has(`${fi.entity_id}|${fi.book_provider}|${fi.book_provider_id}`)) continue;
      const fiTime = new Date(fi.created_at).getTime();
      const hasNearbyComplete = result.some(other =>
        other.kind === 'single'
        && other.event.event_type === 'download_complete'
        && other.event.entity_id === fi.entity_id
        && other.event.book_provider === fi.book_provider
        && other.event.book_provider_id === fi.book_provider_id
        && Math.abs(new Date(other.event.created_at).getTime() - fiTime) < 60_000
      );
      if (hasNearbyComplete) droppedImportedIds.add(fi.id);
    }
    if (droppedImportedIds.size === 0) return result;
    return result.filter(item => !(item.kind === 'single' && droppedImportedIds.has(item.event.id)));
  }, [events]);

  // Pass 2: annotate sessions with isActive from live WebSocket state. Cheap
  // O(N) walk; when no session changed state we return the base array verbatim
  // so downstream memos (activeItems, restItems, grouped) early-bail on identity.
  const displayItems = useMemo<DisplayItem[]>(() => {
    let changed = false;
    const annotateSession = (item: SessionDisplayItem): SessionDisplayItem => {
      const isActive = !!item.activeTaskId
        && liveTaskIds.has(item.activeTaskId)
        && item.latestStatus !== 'complete'
        && item.latestStatus !== 'failed';
      if (isActive === item.isActive) return item;
      changed = true;
      return { ...item, isActive };
    };
    const result = baseDisplayItems.map(item => {
      if (item.kind === 'session') return annotateSession(item);
      if (item.kind === 'run') {
        const newSessions = item.sessions.map(annotateSession);
        if (newSessions.every((s, i) => s === item.sessions[i])) return item;
        return { ...item, sessions: newSessions };
      }
      return item;
    });
    return changed ? result : baseDisplayItems;
  }, [baseDisplayItems, liveTaskIds]);

  // Split active sessions out — pinned to top under "Active" heading
  const activeItems = useMemo(
    () => displayItems.filter((i): i is Extract<DisplayItem, { kind: 'session' }> => i.kind === 'session' && i.isActive),
    [displayItems],
  );
  const restItems = useMemo(
    () => displayItems.filter(i => !(i.kind === 'session' && i.isActive)),
    [displayItems],
  );

  // Group remaining display items by date
  const grouped = useMemo(() => {
    const groups: { label: string; items: DisplayItem[] }[] = [];
    let currentLabel = '';
    for (const item of restItems) {
      const ts =
        item.kind === 'single' ? item.event.created_at :
        item.kind === 'session' ? item.firstAt :
        item.kind === 'run' ? item.firstAt :
        item.events[0].created_at;
      const label = groupLabel(ts);
      if (label !== currentLabel) {
        currentLabel = label;
        groups.push({ label, items: [] });
      }
      groups[groups.length - 1].items.push(item);
    }
    return groups;
  }, [restItems]);

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


      {/* Active sessions — pinned to top */}
      {activeItems.length > 0 ? (
        <div>
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs font-semibold uppercase tracking-wide text-blue-600 dark:text-blue-400">Active</span>
            <span className="text-[10px] text-gray-400">({activeItems.length})</span>
          </div>
          <div className="rounded-2xl border border-blue-500/40 overflow-hidden divide-y divide-gray-200/60 dark:divide-gray-800/60">
            {activeItems.map(item => renderSession(item, true))}
          </div>
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
                  if (item.kind === 'session') {
                    return renderSession(item);
                  }
                  if (item.kind === 'run') {
                    const isExpanded = expandedRuns.has(item.runId);
                    const downloadingCount = item.sessions.filter(s => s.latestStatus === 'downloading').length;
                    const completeCount = item.sessions.filter(s => s.latestStatus === 'complete').length;
                    const failedCount = item.sessions.filter(s => s.latestStatus === 'failed').length;
                    const searchingCount = item.sessions.filter(s => s.latestStatus === 'searching').length;
                    const sessionsCount = item.sessions.length;
                    const headerCount = item.totalCandidates || sessionsCount;

                    let runStatus: ActivityVisualStatus = 'pending';
                    if (downloadingCount > 0) runStatus = 'downloading';
                    else if (searchingCount > 0) runStatus = 'queued';
                    else if (failedCount > 0) runStatus = 'error';
                    else if (completeCount > 0) runStatus = 'complete';
                    const runBadgeStyle = STATUS_BADGE_STYLES[runStatus];

                    const bookThumbs: StackedThumb[] = item.sessions
                      .map(s => {
                        const evWithCover = s.events.find(e => !!e.book_cover_url);
                        return {
                          url: evWithCover?.book_cover_url ?? null,
                          alt: s.bookTitle || 'Book',
                          kind: 'book' as const,
                        };
                      });

                    const triggerLabel = item.trigger === 'scheduled' ? 'Scheduled search' : 'Manual batch search';
                    const titleSuffix = headerCount ? ` — ${headerCount} book${headerCount === 1 ? '' : 's'}` : '';

                    const statusParts: string[] = [];
                    if (completeCount > 0) statusParts.push(`${completeCount} complete`);
                    if (downloadingCount > 0) statusParts.push(`${downloadingCount} downloading`);
                    if (searchingCount > 0) statusParts.push(`${searchingCount} searching`);
                    if (failedCount > 0) statusParts.push(`${failedCount} failed`);
                    if (sessionsCount === 0) statusParts.push('No books processed yet');
                    if (item.slot) statusParts.push(`@ ${item.slot}`);
                    const metaLine = statusParts.join(' · ');

                    return (
                      <div key={`run-${item.runId}`}>
                        <button
                          type="button"
                          onClick={() => setExpandedRuns(prev => { const next = new Set(prev); if (next.has(item.runId)) next.delete(item.runId); else next.add(item.runId); return next; })}
                          className="w-full px-4 py-2 hover:bg-black/5 dark:hover:bg-white/5 transition-colors text-left"
                        >
                          <div className="flex gap-3 items-start">
                            <StackedThumbnails thumbs={bookThumbs} defaultKind="book" />
                            <div className="flex-1 min-w-0 py-0.5">
                              <div className="flex items-start justify-between gap-2">
                                <p className="text-sm truncate leading-tight min-w-0 flex-1">
                                  <span className="font-semibold">{triggerLabel}</span>
                                  {titleSuffix ? <span className="opacity-60 text-xs">{titleSuffix}</span> : null}
                                </p>
                                <div className="flex items-center gap-1 flex-shrink-0">
                                  <span className="text-[10px] text-gray-400 whitespace-nowrap">{formatEventDate(item.firstAt)}</span>
                                  <svg className={`w-3.5 h-3.5 text-gray-400 transition-transform duration-200 ${isExpanded ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                                  </svg>
                                </div>
                              </div>
                              {metaLine ? (
                                <p className="text-[11px] leading-tight opacity-60 truncate mt-0.5" title={metaLine}>
                                  {metaLine}
                                </p>
                              ) : null}
                              <div className="mt-1.5 flex items-center gap-2 min-w-0">
                                <span className={`px-2 py-0.5 rounded-md text-[11px] font-medium ${runBadgeStyle.bg} ${runBadgeStyle.text}`}>
                                  {item.trigger === 'scheduled' ? 'Scheduled' : 'Manual'}
                                </span>
                              </div>
                            </div>
                          </div>
                        </button>
                        {isExpanded ? (
                          <div className="border-t border-gray-200/40 dark:border-gray-800/40 bg-black/[0.02] dark:bg-white/[0.02]">
                            {item.sessions.length === 0 ? (
                              <div className="px-4 py-3 pl-19 text-[11px] text-gray-500">No sessions recorded for this run.</div>
                            ) : (
                              item.sessions.map(s => renderSession(s))
                            )}
                          </div>
                        ) : null}
                      </div>
                    );
                  }
                  if (item.kind === 'batch') {
                    const isExpanded = expandedBatches.has(item.batchId);
                    const failCount = item.events.filter(e => e.event_type === 'author_sync_failed').length;
                    const successCount = item.events.length - failCount;
                    const batchStatus: ActivityVisualStatus = failCount === item.events.length
                      ? 'error'
                      : failCount > 0
                        ? 'queued'
                        : 'complete';
                    const batchBadgeStyle = STATUS_BADGE_STYLES[batchStatus];
                    const authorThumbs: StackedThumb[] = item.events.map(ev => ({
                      url: ev.author_photo_url,
                      alt: ev.author_name || 'Author',
                      kind: 'author' as const,
                    }));
                    const metaParts: string[] = [`${item.events.length} authors`];
                    if (item.totalBooks > 0) metaParts.push(`${item.totalBooks} books tracked`);
                    if (item.totalAdded > 0) metaParts.push(`+${item.totalAdded} new`);
                    if (item.totalRemoved > 0) metaParts.push(`-${item.totalRemoved} removed`);
                    const metaLine = metaParts.join(' · ');
                    return (
                      <div key={`batch-${item.batchId}`}>
                        <button
                          type="button"
                          onClick={() => setExpandedBatches(prev => { const next = new Set(prev); if (next.has(item.batchId)) next.delete(item.batchId); else next.add(item.batchId); return next; })}
                          className="w-full px-4 py-2 hover:bg-black/5 dark:hover:bg-white/5 transition-colors text-left"
                        >
                          <div className="flex gap-3 items-start">
                            <StackedThumbnails thumbs={authorThumbs} defaultKind="author" />
                            <div className="flex-1 min-w-0 py-0.5">
                              <div className="flex items-start justify-between gap-2">
                                <p className="text-sm truncate leading-tight min-w-0 flex-1">
                                  <span className="font-semibold">
                                    Refreshed {successCount} author{successCount !== 1 ? 's' : ''}
                                  </span>
                                  {failCount > 0 ? <span className="opacity-60 text-xs"> — {failCount} failed</span> : null}
                                </p>
                                <div className="flex items-center gap-1 flex-shrink-0">
                                  <span className="text-[10px] text-gray-400 whitespace-nowrap">{formatEventDate(item.events[0].created_at)}</span>
                                  <svg className={`w-3.5 h-3.5 text-gray-400 transition-transform duration-200 ${isExpanded ? 'rotate-90' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}><path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" /></svg>
                                </div>
                              </div>
                              <p className="text-[11px] leading-tight opacity-60 truncate mt-0.5" title={metaLine}>{metaLine}</p>
                              <div className="mt-1.5 flex items-center gap-2 min-w-0">
                                <span className={`px-2 py-0.5 rounded-md text-[11px] font-medium ${batchBadgeStyle.bg} ${batchBadgeStyle.text}`}>
                                  Author Refresh
                                </span>
                                {item.events.some(e => e.triggered_by === 'scheduled') ? (
                                  <span className="px-2 py-0.5 rounded-md text-[11px] font-medium bg-blue-500/15 text-blue-700 dark:text-blue-300">
                                    Scheduled
                                  </span>
                                ) : null}
                              </div>
                            </div>
                          </div>
                        </button>
                        {isExpanded ? (
                          <div className="border-t border-gray-200/40 dark:border-gray-800/40 bg-black/[0.02] dark:bg-white/[0.02]">
                            {item.events.map(ev => {
                              const meta = parseEventMeta(ev);
                              const isFail = ev.event_type === 'author_sync_failed';
                              return (
                                <div key={ev.id} className={`px-4 py-2 pl-19 ${isFail ? 'bg-red-500/5' : ''}`}>
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
