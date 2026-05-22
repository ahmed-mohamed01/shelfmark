import type { FormatAvailabilityStatus } from '../utils/monitoredBookState';

export const EbookIcon = ({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) => (
  <svg
    className={className}
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth={strokeWidth}
    stroke="currentColor"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
    />
  </svg>
);

export const AudiobookIcon = ({
  className,
  strokeWidth = 1.5,
}: {
  className?: string;
  strokeWidth?: number;
}) => (
  <svg
    className={className}
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth={strokeWidth}
    stroke="currentColor"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M19.114 5.636a9 9 0 0 1 0 12.728M16.463 8.288a5.25 5.25 0 0 1 0 7.424M6.75 8.25l4.72-4.72a.75.75 0 0 1 1.28.53v15.88a.75.75 0 0 1-1.28.53l-4.72-4.72H4.51c-.88 0-1.704-.507-1.938-1.354A9.009 9.009 0 0 1 2.25 12c0-.83.112-1.633.322-2.396C2.806 8.756 3.63 8.25 4.51 8.25H6.75Z"
    />
  </svg>
);

const DownloadIcon = ({
  className,
  strokeWidth = 2,
}: {
  className?: string;
  strokeWidth?: number;
}) => (
  <svg
    className={className}
    fill="none"
    viewBox="0 0 24 24"
    strokeWidth={strokeWidth}
    stroke="currentColor"
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M3 16.5v2.25A2.25 2.25 0 0 0 5.25 21h13.5A2.25 2.25 0 0 0 21 18.75V16.5M16.5 12 12 16.5m0 0L7.5 12m4.5 4.5V3"
    />
  </svg>
);

const STATUS_CLASSES: Record<FormatAvailabilityStatus, string> = {
  available: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300',
  candidate: 'bg-blue-500/15 text-blue-700 dark:text-blue-300',
  wanted: 'bg-amber-500/15 text-amber-700 dark:text-amber-300',
  missing: 'bg-red-500/15 text-red-700 dark:text-red-300',
};

const STATUS_CLASSES_OPAQUE: Record<FormatAvailabilityStatus, string> = {
  available: 'bg-emerald-600/90 text-white',
  candidate: 'bg-blue-600/90 text-white',
  wanted: 'bg-amber-600/90 text-white',
  missing: 'bg-red-600/90 text-white',
};

const STATUS_LABELS: Record<FormatAvailabilityStatus, string> = {
  available: 'Available',
  candidate: 'Awaiting review',
  wanted: 'Wanted',
  missing: 'Missing',
};

interface FormatStatusBadgeProps {
  format: 'ebook' | 'audiobook';
  status: FormatAvailabilityStatus;
  /** Icon-only mode for compact tile overlays */
  compact?: boolean;
  /** Click handler — triggers interactive search for this format */
  onClick?: (e: React.MouseEvent) => void;
}

export const FormatStatusBadge = ({
  format,
  status,
  compact = false,
  onClick,
}: FormatStatusBadgeProps) => {
  const Icon = format === 'ebook' ? EbookIcon : AudiobookIcon;
  const colorClass = STATUS_CLASSES[status];
  const label = STATUS_LABELS[status];
  const title = `${format === 'ebook' ? 'eBook' : 'Audiobook'}: ${label}`;
  const clickable = Boolean(onClick);

  if (compact) {
    return (
      <button
        type="button"
        onClick={onClick}
        disabled={!clickable}
        className={`group/badge inline-flex items-center justify-center rounded p-1 shadow ${STATUS_CLASSES_OPAQUE[status]} ${clickable ? 'cursor-pointer transition-all hover:brightness-110 active:scale-95' : ''}`}
        title={clickable ? `Search ${format === 'ebook' ? 'eBook' : 'Audiobook'}` : title}
      >
        {clickable ? (
          <>
            <Icon className="block h-4 w-4 group-hover/badge:hidden" strokeWidth={2.5} />
            <DownloadIcon className="hidden h-4 w-4 group-hover/badge:block" strokeWidth={2.5} />
          </>
        ) : (
          <Icon className="h-4 w-4" strokeWidth={2.5} />
        )}
      </button>
    );
  }

  if (clickable) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`group/badge inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold ${colorClass} cursor-pointer transition-all hover:brightness-110 active:scale-95`}
        title={`Search ${format === 'ebook' ? 'eBook' : 'Audiobook'}`}
      >
        <Icon className="block h-3.5 w-3.5 flex-shrink-0 group-hover/badge:hidden" />
        <DownloadIcon className="hidden h-3.5 w-3.5 flex-shrink-0 group-hover/badge:block" />
        {label}
      </button>
    );
  }

  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs font-semibold ${colorClass}`}
      title={title}
    >
      <Icon className="h-3.5 w-3.5 flex-shrink-0" />
      {label}
    </span>
  );
};

/** Combined badge when both formats share the same status — renders two separate badges so each is independently clickable. */
export const CombinedFormatBadge = ({
  status,
  compact = false,
  onEbookClick,
  onAudiobookClick,
}: {
  status: FormatAvailabilityStatus;
  compact?: boolean;
  onEbookClick?: (e: React.MouseEvent) => void;
  onAudiobookClick?: (e: React.MouseEvent) => void;
}) => (
  <>
    <FormatStatusBadge format="ebook" status={status} compact={compact} onClick={onEbookClick} />
    <FormatStatusBadge
      format="audiobook"
      status={status}
      compact={compact}
      onClick={onAudiobookClick}
    />
  </>
);
