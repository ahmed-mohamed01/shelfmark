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

export const isMonitoredBookDormantState = (book: MonitoredBookStateLike): boolean => (
  !monitoredBookTracksEbook(book)
  && !monitoredBookTracksAudiobook(book)
  && !monitoredBookHasAnyAvailable(book)
);

export type FormatAvailabilityStatus = 'available' | 'wanted' | 'missing';

const MISSING_SEARCH_STATUSES = new Set(['no_match', 'below_cutoff', 'download_failed', 'error']);

/**
 * Returns the availability status for a single format on a monitored book.
 * Returns null if the format is not tracked and no file exists (no badge shown).
 *   available — file found (shown even when monitoring is disabled)
 *   missing   — monitored, searched, all attempts failed (no_match / below_cutoff / download_failed / error)
 *   wanted    — monitored, not yet found (never searched, queued, or not yet released)
 */
/**
 * Whether a monitored book is upcoming (unreleased).
 * True when release date is in the future, year is current/future, or no date is known (TBA).
 */
export const isMonitoredBookUpcoming = (
  book: { release_date?: string | null; publish_year?: number | null; no_release_date?: boolean },
  todayStartMs: number,
  currentYear: number,
): boolean => {
  if (typeof book.release_date === 'string' && book.release_date.trim()) {
    const parsed = Date.parse(book.release_date);
    if (Number.isFinite(parsed)) {
      const releaseDay = new Date(parsed);
      releaseDay.setHours(0, 0, 0, 0);
      if (releaseDay.getTime() >= todayStartMs) return true;
      return false;
    }
  }
  if (typeof book.publish_year === 'number') return book.publish_year >= currentYear;
  return book.no_release_date === true;
};

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
