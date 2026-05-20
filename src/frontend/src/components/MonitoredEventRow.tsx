import { MonitoredEvent } from '../services/monitoredApi';
import { RowThumbnail } from './RowThumbnail';
import { STATUS_BADGE_STYLES } from './activity/activityStyles';
import { ActivityVisualStatus } from './activity/activityTypes';

export function parseEventMeta(ev: MonitoredEvent): Record<string, any> | null {
  if (!ev.metadata_json) return null;
  try { return JSON.parse(ev.metadata_json); } catch { return null; }
}

export function formatEventDate(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function visualStatusFor(ev: MonitoredEvent): ActivityVisualStatus {
  if (ev.status === 'error') return 'error';
  if (ev.status === 'success' || ev.status === 'complete') return 'complete';
  if (ev.event_type === 'download_complete' || ev.event_type === 'file_imported') return 'complete';
  if (ev.event_type === 'download_failed' || ev.event_type === 'author_sync_failed') return 'error';
  if (ev.event_type === 'download_queued') return 'queued';
  if (ev.event_type.startsWith('search_')) return 'pending';
  if (ev.event_type === 'author_removed') return 'cancelled';
  return 'pending';
}

function eventLabel(ev: MonitoredEvent): string {
  // Human-friendly label for the status badge.
  const map: Record<string, string> = {
    download_queued: 'Queued',
    download_complete: 'Complete',
    download_failed: 'Failed',
    file_imported: 'Imported',
    search_started: 'Searching',
    search_queued: 'Match Found',
    search_no_match: 'No Match',
    search_below_cutoff: 'Below Cutoff',
    search_not_released: 'Not Released',
    search_result: 'Result',
    author_synced: 'Synced',
    author_sync_failed: 'Sync Failed',
    author_added: 'Added',
    author_removed: 'Removed',
  };
  return map[ev.event_type] ?? ev.event_type.replace(/_/g, ' ');
}

interface MonitoredEventRowProps {
  event: MonitoredEvent;
}

export const MonitoredEventRow = ({ event: ev }: MonitoredEventRowProps) => {
  const meta = parseEventMeta(ev);
  const isError = ev.status === 'error' || ev.event_type === 'download_failed' || ev.event_type === 'author_sync_failed';
  const visualStatus = visualStatusFor(ev);
  const badgeStyle = STATUS_BADGE_STYLES[visualStatus];

  const hasBook = !!(ev.book_provider && ev.book_provider_id);
  const thumbUrl = hasBook ? ev.book_cover_url : ev.author_photo_url;
  const thumbKind: 'book' | 'author' = hasBook ? 'book' : 'author';

  const title = ev.book_title || ev.author_name || 'Unknown';
  const author = ev.book_title && ev.author_name ? ev.author_name : null;

  const metaParts: string[] = [];
  if (ev.content_type) metaParts.push(ev.content_type);
  if (ev.source_display_name || ev.source) metaParts.push(ev.source_display_name || ev.source!);
  if (meta?.match_score != null && typeof meta.match_score === 'number') {
    metaParts.push(`score ${meta.match_score.toFixed(0)}%`);
  }
  if (meta?.books_added) metaParts.push(`+${meta.books_added} new`);
  if (meta?.books_removed) metaParts.push(`-${meta.books_removed} removed`);
  const metaLine = metaParts.join(' · ');

  const noteLine =
    (isError && (meta?.error_message as string | undefined)) ||
    (meta?.download_path ? `→ ${meta.download_path}` : null) ||
    ev.message ||
    null;

  return (
    <div className={`px-4 py-2 ${isError ? 'bg-red-500/5' : ''}`}>
      <div className="flex gap-3 items-start">
        <RowThumbnail
          url={thumbUrl ?? undefined}
          alt={title}
          kind={thumbKind}
          className="w-12 h-18 shrink-0"
        />
        <div className="flex-1 min-w-0 py-0.5">
          <div className="flex items-start justify-between gap-2">
            <p className="text-sm truncate leading-tight min-w-0 flex-1">
              <span className="font-semibold">{title}</span>
              {author ? <span className="opacity-60 text-xs"> — {author}</span> : null}
            </p>
            <span className="text-[10px] text-gray-400 whitespace-nowrap flex-shrink-0">
              {formatEventDate(ev.created_at)}
            </span>
          </div>

          {metaLine ? (
            <p className="text-[11px] leading-tight opacity-60 truncate mt-0.5" title={metaLine}>
              {metaLine}
            </p>
          ) : null}

          {noteLine ? (
            <p className={`text-[11px] leading-tight truncate mt-0.5 ${isError ? 'text-red-600 dark:text-red-300' : 'opacity-60'}`} title={noteLine}>
              {noteLine}
            </p>
          ) : null}

          <div className="mt-1.5 flex items-center gap-2 min-w-0">
            <span className={`px-2 py-0.5 rounded-md text-[11px] font-medium ${badgeStyle.bg} ${badgeStyle.text}`}>
              {eventLabel(ev)}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};
