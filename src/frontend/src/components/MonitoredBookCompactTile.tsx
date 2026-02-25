import { ReactNode } from 'react';
import { MediaCompactTileBase } from './MediaCompactTileBase';

interface MonitoredBookCompactTileProps {
  title: string;
  thumbnail: ReactNode;
  overflowMenu: ReactNode;
  onOpenDetails: () => void;
  onToggleSelect: () => void;
  isSelected: boolean;
  hasActiveSelection: boolean;
  seriesPosition?: number;
  seriesCount?: number;
  primaryFormat?: string;
  extraFormatsCount?: number;
  seriesLabel?: string;
  showSeriesName?: boolean;
  metaLine?: string;
  showMetaLine?: boolean;
  popularityLine?: string;
  showPopularityLine?: boolean;
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
  primaryFormat,
  extraFormatsCount = 0,
  seriesLabel,
  showSeriesName = false,
  metaLine,
  showMetaLine = false,
  popularityLine,
  showPopularityLine = false,
}: MonitoredBookCompactTileProps) => {
  const topLeftOverlay = (
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
  );

  const topRightOverlay = (
    <>
      {seriesPosition != null ? (
        <span className="inline-flex px-1.5 py-0.5 text-[10px] font-bold text-white bg-emerald-600 rounded" style={{ boxShadow: '0 1px 4px rgba(0, 0, 0, 0.3)' }}>
          #{seriesPosition}{seriesCount != null ? `/${seriesCount}` : ''}
        </span>
      ) : null}
      {primaryFormat ? (
        <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-emerald-600/90 text-white text-[9px] font-semibold uppercase shadow">
          {primaryFormat}
        </span>
      ) : null}
    </>
  );

  const footer = (
    <>
      {showPopularityLine && popularityLine ? (
        <div className="mt-1.5 text-[10px] text-gray-500 dark:text-gray-400">{popularityLine}</div>
      ) : null}

      {extraFormatsCount > 0 ? (
        <div className="mt-1 text-[10px] text-gray-500 dark:text-gray-400 uppercase">+{extraFormatsCount} more</div>
      ) : null}
    </>
  );

  return (
    <MediaCompactTileBase
      title={title}
      media={thumbnail}
      onOpen={onOpenDetails}
      overflowMenu={overflowMenu}
      topLeftOverlay={topLeftOverlay}
      topRightOverlay={topRightOverlay}
      subtitle={showSeriesName ? seriesLabel : undefined}
      metaLine={showMetaLine ? metaLine : undefined}
      footer={footer}
    />
  );
};
