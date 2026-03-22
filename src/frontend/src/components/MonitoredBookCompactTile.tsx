import { type ReactNode } from 'react';
import { MediaCompactTileBase } from './MediaCompactTileBase';
import { FormatStatusBadge, CombinedFormatBadge } from './FormatStatusBadge';
import type { FormatAvailabilityStatus } from '../utils/monitoredBookState';

interface MonitoredBookCompactTileProps {
  title: string;
  thumbnail: ReactNode;
  overflowMenu: ReactNode;
  onOpenDetails?: (() => void) | undefined;
  onToggleSelect?: (() => void) | undefined;
  isSelected: boolean;
  hasActiveSelection: boolean;
  seriesPosition?: number;
  seriesCount?: number;
  ebookStatus?: FormatAvailabilityStatus | null;
  audiobookStatus?: FormatAvailabilityStatus | null;
  seriesLabel?: string;
  showSeriesName?: boolean;
  /** Fallback subtitle when seriesLabel/showSeriesName is not used (e.g. author name in global view) */
  subtitle?: string;
  metaLine?: ReactNode;
  showMetaLine?: boolean;
  popularityLine?: string;
  showPopularityLine?: boolean;
  isDimmed?: boolean;
  /** Countdown tag to display on the poster (e.g. "in 12 days") */
  countdownTag?: string | null;
  /** Show an upcoming/unreleased indicator on the cover */
  isUpcoming?: boolean;
  onEbookSearch?: () => void;
  onAudiobookSearch?: () => void;
}

export const MonitoredBookCompactTile = ({
  title,
  thumbnail,
  overflowMenu,
  onOpenDetails,
  onToggleSelect,
  isSelected,
  hasActiveSelection,
  seriesPosition,
  seriesCount,
  ebookStatus,
  audiobookStatus,
  seriesLabel,
  showSeriesName = false,
  subtitle,
  metaLine,
  showMetaLine = false,
  popularityLine,
  showPopularityLine = false,
  isDimmed = false,
  countdownTag,
  isUpcoming = false,
  onEbookSearch,
  onAudiobookSearch,
}: MonitoredBookCompactTileProps) => {
  const topLeftOverlay = onToggleSelect ? (
    <button
      type="button"
      onClick={onToggleSelect}
      className={`${isSelected ? 'text-emerald-500 dark:text-emerald-400' : 'text-white/80'} ${isSelected || hasActiveSelection ? 'opacity-100' : 'opacity-0 group-hover:opacity-65'} hover-action rounded-full p-0.5 bg-black/30 backdrop-blur-[1px] transition-opacity`}
      role="checkbox"
      aria-checked={isSelected}
      aria-label={`Select ${title || 'book'}`}
    >
      {isSelected ? (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><rect x="4" y="4" width="16" height="16" rx="3" /><path strokeLinecap="round" strokeLinejoin="round" d="m8 12 2.5 2.5L16 9" /></svg>
      ) : (
        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><rect x="4" y="4" width="16" height="16" rx="3" /></svg>
      )}
    </button>
  ) : null;

  // Series position + upcoming badge at top-right
  const seriesBadge = seriesPosition != null ? (
    <span className="inline-flex px-1.5 py-0.5 text-[10px] font-bold text-white bg-emerald-600 rounded" style={{ boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)' }}>
      #{seriesPosition}{seriesCount != null ? `/${seriesCount}` : ''}
    </span>
  ) : null;
  const upcomingBadge = (countdownTag || isUpcoming) ? (
    <span className="inline-flex px-1.5 py-0.5 text-[10px] font-bold text-white bg-amber-500 rounded" style={{ boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)' }}>
      {countdownTag || 'Upcoming'}
    </span>
  ) : null;
  const topRightOverlay = (seriesBadge || upcomingBadge) ? (
    <>{seriesBadge}{upcomingBadge}</>
  ) : null;

  // Format badges at bottom of cover — consolidated when both share the same status
  const hasBoth = ebookStatus && audiobookStatus;
  const sameStatus = hasBoth && ebookStatus === audiobookStatus;
  const bottomRightOverlay = (ebookStatus || audiobookStatus) ? (
    <>
      {sameStatus ? (
        <CombinedFormatBadge status={ebookStatus} compact onEbookClick={onEbookSearch ? (e) => { e.stopPropagation(); onEbookSearch(); } : undefined} onAudiobookClick={onAudiobookSearch ? (e) => { e.stopPropagation(); onAudiobookSearch(); } : undefined} />
      ) : (
        <>
          {ebookStatus ? <FormatStatusBadge format="ebook" status={ebookStatus} compact onClick={onEbookSearch ? (e) => { e.stopPropagation(); onEbookSearch(); } : undefined} /> : null}
          {audiobookStatus ? <FormatStatusBadge format="audiobook" status={audiobookStatus} compact onClick={onAudiobookSearch ? (e) => { e.stopPropagation(); onAudiobookSearch(); } : undefined} /> : null}
        </>
      )}
    </>
  ) : null;

  const footer = showPopularityLine && popularityLine ? (
    <div className="mt-1.5 text-[10px] text-gray-500 dark:text-gray-400">{popularityLine}</div>
  ) : null;

  return (
    <MediaCompactTileBase
      title={title}
      media={thumbnail}
      onOpen={onOpenDetails}
      overflowMenu={overflowMenu}
      topLeftOverlay={topLeftOverlay}
      topRightOverlay={topRightOverlay}
      bottomRightOverlay={bottomRightOverlay}
      seriesLine={showSeriesName ? seriesLabel : undefined}
      subtitle={subtitle}
      metaLine={showMetaLine ? metaLine : undefined}
      footer={footer}
      isDimmed={isDimmed}
      isSelected={isSelected}
      hasActiveSelection={hasActiveSelection}
      onToggleSelect={onToggleSelect}
    />
  );
};
