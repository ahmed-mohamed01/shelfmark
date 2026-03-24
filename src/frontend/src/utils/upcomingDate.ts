import type { ReactNode } from 'react';

/** Minimal shape needed by upcoming-date helpers. */
interface HasReleaseInfo {
  release_date?: string | null;
  publish_year?: number | null;
}

/**
 * Parse a release_date string as local midnight to avoid timezone off-by-one errors.
 * (Date.parse("2026-03-22") returns UTC midnight, which can be "yesterday" in UTC+ zones.)
 */
const parseReleaseDateLocal = (dateStr: string): number | null => {
  const parsed = Date.parse(dateStr);
  if (!Number.isFinite(parsed)) return null;
  // Re-parse as local midnight
  const d = new Date(parsed);
  return new Date(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()).getTime();
};

/**
 * Returns the countdown text (e.g. "Today", "Tomorrow", "in 12 days", "in 3 months") or null.
 * Returns null for past dates or dates more than 12 months away.
 */
export const getUpcomingCountdown = (book: HasReleaseInfo): string | null => {
  if (typeof book.release_date === 'string' && book.release_date.trim()) {
    const releaseMs = parseReleaseDateLocal(book.release_date);
    if (releaseMs !== null) {
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      const days = Math.ceil((releaseMs - today.getTime()) / 86_400_000);
      if (days < 0) return null;
      if (days === 0) return 'Today';
      if (days === 1) return 'Tomorrow';
      if (days <= 90) return `in ${days} days`;
      const months = Math.round(days / 30.44);
      if (months <= 12) return `in ${months} month${months === 1 ? '' : 's'}`;
    }
  }
  return null;
};

/**
 * Returns a past-tense label for recently released books (e.g. "Released today", "1 day ago", "12 days ago").
 * Returns null if the book has no release_date or if the release date is in the future.
 */
export const getRecentlyReleasedLabel = (book: HasReleaseInfo): string | null => {
  if (typeof book.release_date === 'string' && book.release_date.trim()) {
    const releaseMs = parseReleaseDateLocal(book.release_date);
    if (releaseMs !== null) {
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      const days = Math.floor((today.getTime() - releaseMs) / 86_400_000);
      if (days < 0) return null;
      if (days === 0) return 'Released today';
      if (days === 1) return '1 day ago';
      return `${days} days ago`;
    }
  }
  return null;
};

/**
 * Format the release date for display (e.g. "Mar 24, 2026", "2026", or "TBA").
 * Does NOT include the countdown — use getUpcomingCountdown or getRecentlyReleasedLabel for that.
 */
export const formatUpcomingDate = (book: HasReleaseInfo): ReactNode => {
  if (typeof book.release_date === 'string' && book.release_date.trim()) {
    const releaseMs = parseReleaseDateLocal(book.release_date);
    if (releaseMs !== null) {
      return new Date(releaseMs).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
    }
  }
  if (typeof book.publish_year === 'number') return String(book.publish_year);
  return null;
};
