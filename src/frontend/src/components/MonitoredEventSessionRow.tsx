import { useMemo, useState } from 'react';
import { MonitoredEvent } from '../services/monitoredApi';
import { parseEventMeta, formatEventDate } from './MonitoredEventRow';
import { ActivityProgressBar } from './activity/ActivityProgressBar';
import { STATUS_BADGE_STYLES } from './activity/activityStyles';
import { ActivityVisualStatus } from './activity/activityTypes';
import { RowThumbnail } from './RowThumbnail';
import { Book } from '../types';

export type SessionLatestStatus = 'searching' | 'downloading' | 'complete' | 'failed';

interface MonitoredEventSessionRowProps {
  events: MonitoredEvent[]; // sorted DESC by created_at
  bookTitle: string | null;
  authorName: string | null;
  latestStatus: SessionLatestStatus;
  isActive: boolean;
  liveBook: Book | null;
  defaultExpanded?: boolean;
}

function fmtTime(iso: string): string {
  try {
    const d = new Date(iso);
    const hh = String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    const ss = String(d.getSeconds()).padStart(2, '0');
    return `${hh}:${mm}:${ss}`;
  } catch {
    return iso;
  }
}

function formatLiveProgress(progress: number, sizeRaw?: string): string {
  if (sizeRaw) {
    const v = parseFloat(sizeRaw.replace(/[^\d.]/g, ''));
    const u = sizeRaw.replace(/[\d.\s]/g, '');
    if (v > 0) return `${((progress / 100) * v).toFixed(1)}${u} / ${sizeRaw}`;
  }
  return `Downloading ${Math.round(progress)}%`;
}

function timelineMessage(ev: MonitoredEvent): string {
  const meta = parseEventMeta(ev);
  const score = typeof meta?.best_score === 'number' ? meta.best_score : null;
  const cutoff = typeof meta?.cutoff_score === 'number' ? meta.cutoff_score : null;
  switch (ev.event_type) {
    case 'search_started':
      return 'Searching releases';
    case 'search_queued': {
      const releaseTitle = (meta?.release_title as string | undefined) || ev.book_title || 'Unknown';
      const scoreText = score != null ? ` (score ${score.toFixed(1)})` : '';
      return `"${releaseTitle}" matched${scoreText}`;
    }
    case 'search_no_match':
      return 'No match found';
    case 'search_below_cutoff': {
      const a = score != null ? score.toFixed(1) : '?';
      const b = cutoff != null ? cutoff.toFixed(1) : '?';
      return `Below cutoff (${a} < ${b})`;
    }
    case 'search_not_released':
      return 'Not yet released';
    case 'search_result':
      return ev.message || 'Search result';
    case 'download_queued': {
      const dest = ev.source_display_name || ev.source || 'downloader';
      return `Sent to ${dest}`;
    }
    case 'download_complete':
      return 'Download complete';
    case 'download_failed': {
      const err = (meta?.error_message as string | undefined) || '';
      return err ? `Download failed: ${err}` : 'Download failed';
    }
    case 'file_imported': {
      const path = (meta?.final_path as string | undefined) || '';
      return path ? `File available: ${path}` : 'File imported';
    }
    default:
      return ev.message || ev.event_type;
  }
}

function visualStatusFor(latestStatus: SessionLatestStatus, isActive: boolean): ActivityVisualStatus {
  if (isActive) return 'downloading';
  if (latestStatus === 'complete') return 'complete';
  if (latestStatus === 'failed') return 'error';
  if (latestStatus === 'downloading') return 'queued';
  return 'pending';
}

function statusLabel(latestStatus: SessionLatestStatus, isActive: boolean): string {
  if (isActive) return 'Active';
  if (latestStatus === 'complete') return 'Complete';
  if (latestStatus === 'failed') return 'Failed';
  if (latestStatus === 'downloading') return 'Queued';
  return 'Searching';
}

export const MonitoredEventSessionRow = ({
  events,
  bookTitle,
  authorName,
  latestStatus,
  isActive,
  liveBook,
  defaultExpanded = false,
}: MonitoredEventSessionRowProps) => {
  const [expanded, setExpanded] = useState<boolean>(defaultExpanded);

  // Events arrive DESC; render chronologically (oldest first).
  const chronological = useMemo(() => events.slice().reverse(), [events]);
  const earliest = chronological[0];
  const latest = events[0];

  // Pull the cover from any event in the session — the DB layer snapshots it
  // at insert time on every event for the same book.
  const coverUrl = useMemo(() => {
    for (const ev of events) {
      if (ev.book_cover_url) return ev.book_cover_url;
    }
    return null;
  }, [events]);

  const livePct = liveBook && typeof liveBook.progress === 'number' ? liveBook.progress : null;
  const liveStatusDetail = liveBook?.status_message || null;

  const visualStatus = visualStatusFor(latestStatus, isActive);
  const badgeStyle = STATUS_BADGE_STYLES[visualStatus];

  // Meta line — latest event message + event count
  const latestMessage = latest ? timelineMessage(latest) : null;
  const metaParts: string[] = [];
  if (latestMessage) metaParts.push(latestMessage);
  metaParts.push(`${events.length} event${events.length !== 1 ? 's' : ''}`);
  const metaLine = metaParts.join(' · ');

  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded(v => !v)}
        className="w-full px-4 py-2 hover:bg-black/5 dark:hover:bg-white/5 transition-colors text-left"
      >
        <div className="flex gap-3 items-start">
          <RowThumbnail
            url={coverUrl ?? undefined}
            alt={bookTitle || undefined}
            kind="book"
            className="w-12 h-18 shrink-0"
          />
          <div className="flex-1 min-w-0 py-0.5">
            <div className="flex items-start justify-between gap-2">
              <p className="text-sm truncate leading-tight min-w-0 flex-1">
                <span className="font-semibold">{bookTitle || 'Unknown title'}</span>
                {authorName ? <span className="opacity-60 text-xs"> — {authorName}</span> : null}
              </p>
              <div className="flex items-center gap-1 flex-shrink-0">
                <span className="text-[10px] text-gray-400 whitespace-nowrap">{formatEventDate(latest.created_at)}</span>
                <svg
                  className={`w-3.5 h-3.5 text-gray-400 transition-transform duration-200 ${expanded ? 'rotate-90' : ''}`}
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth={2}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                </svg>
              </div>
            </div>

            {metaLine ? (
              <p className="text-[11px] leading-tight opacity-60 truncate mt-0.5" title={metaLine}>
                {metaLine}
              </p>
            ) : null}

            {isActive && livePct != null ? (
              <p className="text-[11px] leading-tight text-blue-600 dark:text-blue-300 truncate mt-0.5">
                {formatLiveProgress(livePct, liveBook?.size)}
                {liveStatusDetail ? <span className="text-gray-400"> · {liveStatusDetail}</span> : null}
              </p>
            ) : null}

            <div className="mt-1.5 flex items-center gap-2 min-w-0">
              <span className={`px-2 py-0.5 rounded-md text-[11px] font-medium ${badgeStyle.bg} ${badgeStyle.text}`}>
                {statusLabel(latestStatus, isActive)}
              </span>
              {events.some(e => e.triggered_by === 'scheduled') ? (
                <span className="px-2 py-0.5 rounded-md text-[11px] font-medium bg-blue-500/15 text-blue-700 dark:text-blue-300">
                  Scheduled
                </span>
              ) : null}
            </div>
          </div>
        </div>
      </button>

      {isActive && livePct != null ? (
        <ActivityProgressBar status="downloading" progress={livePct} animated />
      ) : null}

      {expanded ? (
        <div className="border-t border-gray-200/40 dark:border-gray-800/40 bg-black/[0.02] dark:bg-white/[0.02]">
          {chronological.map(ev => {
            const isFailEv = ev.event_type === 'download_failed' || ev.status === 'error';
            return (
              <div key={ev.id} className={`px-4 py-1.5 pl-19 ${isFailEv ? 'bg-red-500/5' : ''}`}>
                <div className="flex items-baseline gap-2 text-xs">
                  <span className="font-mono text-gray-500 dark:text-gray-400 flex-shrink-0">{fmtTime(ev.created_at)}</span>
                  <span className={`break-words ${isFailEv ? 'text-red-600 dark:text-red-300' : 'text-gray-700 dark:text-gray-200'}`}>
                    {timelineMessage(ev)}
                  </span>
                </div>
              </div>
            );
          })}
          {isActive && livePct != null ? (
            <div className="px-4 py-1.5 pl-19">
              <div className="flex items-baseline gap-2 text-xs">
                <span className="font-mono text-gray-500 dark:text-gray-400 flex-shrink-0">{fmtTime(new Date().toISOString())}</span>
                <span className="text-blue-600 dark:text-blue-300">
                  {formatLiveProgress(livePct, liveBook?.size)}
                </span>
              </div>
            </div>
          ) : null}
          {chronological.length === 0 && earliest == null ? (
            <div className="px-4 py-2 pl-19 text-xs text-gray-500">No events recorded yet.</div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
};
