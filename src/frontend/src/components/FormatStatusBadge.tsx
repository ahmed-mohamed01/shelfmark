import type { FormatAvailabilityStatus } from '../utils/monitoredBookState';

const EbookIcon = ({ className }: { className?: string }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25" />
  </svg>
);

const AudiobookIcon = ({ className }: { className?: string }) => (
  <svg className={className} fill="none" viewBox="0 0 24 24" strokeWidth="1.5" stroke="currentColor">
    <path strokeLinecap="round" strokeLinejoin="round" d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z" />
  </svg>
);

const STATUS_CLASSES: Record<FormatAvailabilityStatus, string> = {
  available: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  wanted:    'bg-sky-500/15 text-sky-700 dark:text-sky-300',
  missing:   'bg-red-500/15 text-red-700 dark:text-red-300',
};

const STATUS_CLASSES_OPAQUE: Record<FormatAvailabilityStatus, string> = {
  available: 'bg-emerald-600/90 text-white',
  wanted:    'bg-sky-600/90 text-white',
  missing:   'bg-red-600/90 text-white',
};

const STATUS_LABELS: Record<FormatAvailabilityStatus, string> = {
  available: 'Available',
  wanted:    'Wanted',
  missing:   'Missing',
};

interface FormatStatusBadgeProps {
  format: 'ebook' | 'audiobook';
  status: FormatAvailabilityStatus;
  /** Icon-only mode for compact tile overlays */
  compact?: boolean;
}

export const FormatStatusBadge = ({ format, status, compact = false }: FormatStatusBadgeProps) => {
  const Icon = format === 'ebook' ? EbookIcon : AudiobookIcon;
  const colorClass = STATUS_CLASSES[status];
  const label = STATUS_LABELS[status];
  const title = `${format === 'ebook' ? 'eBook' : 'Audiobook'}: ${label}`;

  if (compact) {
    return (
      <span
        className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded shadow ${STATUS_CLASSES_OPAQUE[status]}`}
        title={title}
      >
        <Icon className="w-3 h-4 flex-shrink-0" />
        <span className="text-[11px] font-bold leading-none">{label}</span>
      </span>
    );
  }

  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-semibold ${colorClass}`}
      title={title}
    >
      <Icon className="w-3.5 h-3.5 flex-shrink-0" />
      {label}
    </span>
  );
};
