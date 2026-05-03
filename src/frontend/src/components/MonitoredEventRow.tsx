import React from 'react';
import { MonitoredEvent } from '../services/monitoredApi';

function eventIcon(eventType: string): React.ReactNode {
  if (eventType.startsWith('download_')) {
    return <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3" /></svg>;
  }
  if (eventType.startsWith('search_')) {
    return <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="m21 21-5.197-5.197m0 0A7.5 7.5 0 1 0 5.196 5.196a7.5 7.5 0 0 0 10.607 10.607Z" /></svg>;
  }
  if (eventType.startsWith('author_')) {
    return <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z" /></svg>;
  }
  return <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}><path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" /></svg>;
}

function eventIconColor(status: string | null): string {
  if (status === 'error') return 'text-red-500';
  if (status === 'success') return 'text-emerald-500';
  if (status === 'warning') return 'text-amber-500';
  return 'text-blue-400';
}

function statusBadgeClass(status: string | null): string {
  if (status === 'error') return 'bg-red-500/20 text-red-700 dark:text-red-300';
  if (status === 'success') return 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300';
  if (status === 'warning') return 'bg-amber-500/20 text-amber-700 dark:text-amber-300';
  return 'bg-blue-500/20 text-blue-700 dark:text-blue-300';
}

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

interface MonitoredEventRowProps {
  event: MonitoredEvent;
}

export const MonitoredEventRow = ({ event: ev }: MonitoredEventRowProps) => {
  const meta = parseEventMeta(ev);
  const isError = ev.status === 'error';
  const typeLabel = ev.event_type.replace(/_/g, ' ');
  return (
    <div className={`px-4 py-3 ${isError ? 'bg-red-500/5' : ''}`}>
      <div className="flex items-start gap-3">
        <div className={`mt-0.5 flex-shrink-0 ${eventIconColor(ev.status)}`}>{eventIcon(ev.event_type)}</div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className={`inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide ${statusBadgeClass(ev.status)}`}>
              {typeLabel}
            </span>
            {ev.content_type ? <span className="text-[10px] text-gray-400">{ev.content_type}</span> : null}
            {ev.source_display_name || ev.source ? <span className="text-[10px] text-gray-400">· {ev.source_display_name || ev.source}</span> : null}
          </div>
          {ev.book_title || ev.author_name ? (
            <div className="mt-1 text-sm text-gray-700 dark:text-gray-200">
              {ev.book_title ? <span className="font-medium">{ev.book_title}</span> : null}
              {ev.book_title && ev.author_name ? <span className="text-gray-500"> — {ev.author_name}</span> : null}
              {!ev.book_title && ev.author_name ? <span className="font-medium">{ev.author_name}</span> : null}
            </div>
          ) : null}
          {ev.message ? <div className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">{ev.message}</div> : null}
          {meta?.download_path ? <div className="mt-0.5 text-[11px] text-gray-500 dark:text-gray-400 break-all">→ {meta.download_path}</div> : null}
          {meta?.error_message && isError ? <div className="mt-0.5 text-[11px] text-red-600 dark:text-red-300 break-words">{meta.error_message}</div> : null}
          {meta?.match_score != null && typeof meta.match_score === 'number' ? (
            <div className="mt-0.5 text-[10px] text-gray-400">score {meta.match_score.toFixed(0)}%</div>
          ) : null}
          {meta?.books_added != null || meta?.books_removed != null ? (
            <div className="mt-0.5 text-[11px] text-gray-500">
              {meta.books_added ? `+${meta.books_added} new` : ''}
              {meta.books_added && meta.books_removed ? ', ' : ''}
              {meta.books_removed ? `-${meta.books_removed} removed` : ''}
            </div>
          ) : null}
        </div>
        <div className="text-[10px] text-gray-400 flex-shrink-0 whitespace-nowrap">{formatEventDate(ev.created_at)}</div>
      </div>
    </div>
  );
};
