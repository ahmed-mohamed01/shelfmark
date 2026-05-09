export type MonitoredBookStateLike = {
  monitor_ebook?: unknown;
  monitor_audiobook?: unknown;
  has_ebook_available?: unknown;
  has_audiobook_available?: unknown;
  ebook_path?: unknown;
  audiobook_path?: unknown;
  ebook_available_format?: unknown;
  audiobook_available_format?: unknown;
  ebook_last_search_status?: unknown;
  audiobook_last_search_status?: unknown;
};

export const isEnabledMonitoredFlag = (value: unknown): boolean => {
  if (value === true || value === 1) {
    return true;
  }
  if (typeof value === 'string') {
    const normalized = value.trim().toLowerCase();
    return normalized === '1' || normalized === 'true';
  }
  return false;
};

const hasNonEmptyString = (value: unknown): boolean => (
  typeof value === 'string' && value.trim().length > 0
);

export const monitoredBookTracksEbook = (book: MonitoredBookStateLike): boolean => (
  isEnabledMonitoredFlag(book.monitor_ebook)
);

export const monitoredBookTracksAudiobook = (book: MonitoredBookStateLike): boolean => (
  isEnabledMonitoredFlag(book.monitor_audiobook)
);

export const monitoredBookHasFormatAvailable = (
  book: MonitoredBookStateLike,
  format: 'ebook' | 'audiobook',
): boolean => {
  if (format === 'ebook') {
    return (
      isEnabledMonitoredFlag(book.has_ebook_available)
      || hasNonEmptyString(book.ebook_path)
      || hasNonEmptyString(book.ebook_available_format)
    );
  }

  return (
    isEnabledMonitoredFlag(book.has_audiobook_available)
    || hasNonEmptyString(book.audiobook_path)
    || hasNonEmptyString(book.audiobook_available_format)
  );
};

export const monitoredBookHasAnyAvailable = (book: MonitoredBookStateLike): boolean => (
  monitoredBookHasFormatAvailable(book, 'ebook') || monitoredBookHasFormatAvailable(book, 'audiobook')
);

// A book is "missing" if any tracked format lacks a file. Aligns the
// Monitored Books tab with the per-format badges and per-format auto-search:
// a book with ebook present but a tracked-and-missing audiobook still counts.
export const monitoredBookHasMissingTrackedFormat = (book: MonitoredBookStateLike): boolean => (
  (monitoredBookTracksEbook(book) && !monitoredBookHasFormatAvailable(book, 'ebook'))
  || (monitoredBookTracksAudiobook(book) && !monitoredBookHasFormatAvailable(book, 'audiobook'))
);

export const isMonitoredBookDormantState = (book: MonitoredBookStateLike): boolean => (
  !monitoredBookTracksEbook(book)
  && !monitoredBookTracksAudiobook(book)
  && !monitoredBookHasAnyAvailable(book)
);

export type FormatAvailabilityStatus = 'available' | 'wanted' | 'missing';

const MISSING_SEARCH_STATUSES = new Set(['no_match', 'below_cutoff', 'download_failed', 'error']);

// Whether a monitored book is upcoming (unreleased). True when the release
// date is in the future or absent/unparseable — books with missing metadata
// surface in the Upcoming section so the user can triage (keep monitoring
// vs. unmonitor) instead of burning auto-search cycles. publish_year is not
// a fallback: it usually reflects the original work, not the edition the
// user wants, so it can't substitute for a real release date.
export const isMonitoredBookUpcoming = (
  book: { release_date?: string | null },
  todayStartMs: number,
): boolean => {
  if (typeof book.release_date === 'string' && book.release_date.trim()) {
    const parsed = Date.parse(book.release_date);
    if (Number.isFinite(parsed)) {
      const releaseDay = new Date(parsed);
      releaseDay.setHours(0, 0, 0, 0);
      return releaseDay.getTime() > todayStartMs;
    }
  }
  return true;
};

/**
 * Whether a monitored book was recently released (past release date within the last N days).
 * Complement of isMonitoredBookUpcoming — covers the window right after release.
 */
export const isMonitoredBookRecentlyReleased = (
  book: { release_date?: string | null },
  todayStartMs: number,
  recentDays = 90,
): boolean => {
  if (typeof book.release_date === 'string' && book.release_date.trim()) {
    const parsed = Date.parse(book.release_date);
    if (Number.isFinite(parsed)) {
      const releaseDay = new Date(parsed);
      releaseDay.setHours(0, 0, 0, 0);
      const releaseMs = releaseDay.getTime();
      if (releaseMs > todayStartMs) return false; // future = upcoming, not recent
      const cutoffMs = todayStartMs - recentDays * 86_400_000;
      return releaseMs >= cutoffMs;
    }
  }
  return false;
};

/**
 * Returns the availability status for a single format on a monitored book.
 * Returns null if the format is not tracked and no file exists (no badge shown).
 *   available — file found (shown even when monitoring is disabled)
 *   missing   — monitored, searched, all attempts failed (no_match / below_cutoff / download_failed / error)
 *   wanted    — monitored, not yet found (never searched, queued, or not yet released)
 */
export const getFormatStatus = (
  book: MonitoredBookStateLike,
  format: 'ebook' | 'audiobook',
): FormatAvailabilityStatus | null => {
  // Show "available" whenever a file exists, even if monitoring is disabled
  if (monitoredBookHasFormatAvailable(book, format)) return 'available';

  // "wanted" / "missing" only apply when actively monitoring
  const tracks = format === 'ebook' ? monitoredBookTracksEbook(book) : monitoredBookTracksAudiobook(book);
  if (!tracks) return null;

  const rawStatus = format === 'ebook' ? book.ebook_last_search_status : book.audiobook_last_search_status;
  const lastStatus = typeof rawStatus === 'string' ? rawStatus.trim().toLowerCase() : null;
  if (lastStatus && MISSING_SEARCH_STATUSES.has(lastStatus)) return 'missing';

  return 'wanted';
};
