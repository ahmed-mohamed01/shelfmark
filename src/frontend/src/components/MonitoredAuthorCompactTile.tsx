import { ReactNode } from 'react';
import { MediaCompactTileBase } from './MediaCompactTileBase';

interface MonitoredAuthorCompactTileProps {
  name: string;
  thumbnail: ReactNode;
  onOpenDetails: () => void;
  onEdit?: () => void;
  overflowMenu?: ReactNode;
  badge?: string;
  subtitle?: string;
  metaLine?: string;
  footer?: ReactNode;
  onToggleSelect?: () => void;
  isSelected?: boolean;
  hasActiveSelection?: boolean;
}

export const MonitoredAuthorCompactTile = ({
  name,
  thumbnail,
  onOpenDetails,
  onEdit,
  overflowMenu,
  badge,
  subtitle,
  metaLine,
  footer,
  onToggleSelect,
  isSelected = false,
  hasActiveSelection = false,
}: MonitoredAuthorCompactTileProps) => {
  const topLeftOverlay = (
    <div className="flex flex-col gap-1">
      {onToggleSelect ? (
        <button
          type="button"
          onClick={onToggleSelect}
          className={`${isSelected ? 'text-emerald-500 dark:text-emerald-400' : 'text-white/80'} ${isSelected || hasActiveSelection ? 'opacity-100' : 'opacity-0 group-hover:opacity-100'} transition-opacity hover-action rounded-full p-0.5 bg-black/30 backdrop-blur-[1px]`}
          role="checkbox"
          aria-checked={isSelected}
          aria-label={`Select ${name || 'author'}`}
        >
          {isSelected ? (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><rect x="4" y="4" width="16" height="16" rx="3" /><path strokeLinecap="round" strokeLinejoin="round" d="m8 12 2.5 2.5L16 9" /></svg>
          ) : (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}><rect x="4" y="4" width="16" height="16" rx="3" /></svg>
          )}
        </button>
      ) : null}
      {onEdit ? (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onEdit(); }}
          className="opacity-0 group-hover:opacity-100 transition-opacity hover-action rounded-full p-0.5 bg-black/30 backdrop-blur-[1px] text-white/80"
          aria-label={`Edit ${name || 'author'}`}
          title="Edit author"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
            <path strokeLinecap="round" strokeLinejoin="round" d="m16.862 4.487 1.687-1.688a1.875 1.875 0 1 1 2.652 2.652L10.582 16.07a4.5 4.5 0 0 1-1.897 1.13L6 18l.8-2.685a4.5 4.5 0 0 1 1.13-1.897l8.932-8.931Zm0 0L19.5 7.125" />
          </svg>
        </button>
      ) : null}
    </div>
  );

  return (
    <MediaCompactTileBase
      title={name}
      media={thumbnail}
      onOpen={onOpenDetails}
      overflowMenu={overflowMenu}
      topLeftOverlay={topLeftOverlay}
      topRightOverlay={badge ? <span className="inline-flex items-center px-1.5 py-0.5 rounded bg-emerald-600/90 text-white text-[9px] font-semibold uppercase shadow">{badge}</span> : undefined}
      subtitle={subtitle}
      metaLine={metaLine}
      footer={footer}
    />
  );
};
