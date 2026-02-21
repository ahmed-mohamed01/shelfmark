import { ReactNode } from 'react';
import { MediaCompactTileBase } from './MediaCompactTileBase';

interface MonitoredAuthorCompactTileProps {
  name: string;
  thumbnail: ReactNode;
  onOpenDetails: () => void;
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
  overflowMenu,
  badge,
  subtitle,
  metaLine,
  footer,
  onToggleSelect,
  isSelected = false,
  hasActiveSelection = false,
}: MonitoredAuthorCompactTileProps) => {
  const topLeftOverlay = onToggleSelect ? (
    <button
      type="button"
      onClick={onToggleSelect}
      className={`${isSelected ? 'text-emerald-500 dark:text-emerald-400' : 'text-white/80'} ${!hasActiveSelection && !isSelected ? 'opacity-65' : 'opacity-100'} hover-action rounded-full p-0.5 bg-black/30 backdrop-blur-[1px]`}
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
  ) : undefined;

  return (
    <MediaCompactTileBase
      title={name}
      media={thumbnail}
      onOpen={onOpenDetails}
      overflowMenu={overflowMenu}
      topLeftOverlay={topLeftOverlay}
      topRightBadge={badge}
      subtitle={subtitle}
      metaLine={metaLine}
      footer={footer}
    />
  );
};
