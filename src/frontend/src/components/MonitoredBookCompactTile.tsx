import { ReactNode } from 'react';

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
  return (
    <div className="relative self-start h-fit rounded-xl border border-[var(--border-muted)] bg-[var(--bg)] p-2">
      <div className="absolute left-2 top-2 z-20">
        <button
          type="button"
          onClick={onToggleSelect}
          className={`${isSelected ? 'text-emerald-500 dark:text-emerald-400' : 'text-white/80'} ${!hasActiveSelection && !isSelected ? 'opacity-65' : 'opacity-100'} hover-action rounded-full p-0.5 bg-black/30 backdrop-blur-[1px]`}
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
        {seriesPosition != null ? (
          <div className="mt-1 text-[10px] leading-none font-semibold text-white/90 text-center bg-black/30 rounded px-1 py-0.5">
            #{seriesPosition}{seriesCount != null ? `/${seriesCount}` : ''}
          </div>
        ) : null}
      </div>

      <button type="button" onClick={onOpenDetails} className="block w-full text-left">
        <div className="relative w-full rounded overflow-hidden">
          {thumbnail}
          {primaryFormat ? (
            <div className="absolute right-1.5 top-1.5 inline-flex items-center px-1.5 py-0.5 rounded bg-emerald-600/90 text-white text-[9px] font-semibold uppercase shadow">
              {primaryFormat}
            </div>
          ) : null}
        </div>
        <div className="mt-1 flex items-center gap-1">
          <p className="flex-1 min-w-0 text-xs font-semibold leading-snug line-clamp-2">{title || 'Untitled'}</p>
          <div className="flex-shrink-0" onClick={(e) => e.stopPropagation()}>
            {overflowMenu}
          </div>
        </div>
        {showSeriesName && seriesLabel ? (
          <p className="-mt-0.5 text-[10px] leading-tight text-gray-500 dark:text-gray-400 truncate">{seriesLabel}</p>
        ) : null}
        {showMetaLine && metaLine ? (
          <p className="-mt-0.5 text-[10px] leading-tight text-gray-500 dark:text-gray-400 truncate">{metaLine}</p>
        ) : null}
      </button>

      {showPopularityLine && popularityLine ? (
        <div className="mt-1.5 text-[10px] text-gray-500 dark:text-gray-400">{popularityLine}</div>
      ) : null}

      {extraFormatsCount > 0 ? (
        <div className="mt-1 text-[10px] text-gray-500 dark:text-gray-400 uppercase">+{extraFormatsCount} more</div>
      ) : null}
    </div>
  );
};
