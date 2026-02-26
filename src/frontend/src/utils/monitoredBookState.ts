export type MonitoredBookStateLike = {
  monitor_ebook?: unknown;
  monitor_audiobook?: unknown;
  has_ebook_available?: unknown;
  has_audiobook_available?: unknown;
  ebook_path?: unknown;
  audiobook_path?: unknown;
  ebook_available_format?: unknown;
  audiobook_available_format?: unknown;
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
