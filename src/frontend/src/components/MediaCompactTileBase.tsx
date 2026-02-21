import { ReactNode } from 'react';

interface MediaCompactTileBaseProps {
  title: string;
  media: ReactNode;
  onOpen: () => void;
  overflowMenu?: ReactNode;
  topLeftOverlay?: ReactNode;
  topRightBadge?: string;
  subtitle?: string;
  metaLine?: string;
  footer?: ReactNode;
}

export const MediaCompactTileBase = ({
  title,
  media,
  onOpen,
  overflowMenu,
  topLeftOverlay,
  topRightBadge,
  subtitle,
  metaLine,
  footer,
}: MediaCompactTileBaseProps) => {
  return (
    <div className="relative self-start h-fit rounded-xl border border-[var(--border-muted)] bg-[var(--bg)] p-2">
      {topLeftOverlay ? (
        <div className="absolute left-2 top-2 z-20">
          {topLeftOverlay}
        </div>
      ) : null}

      <button type="button" onClick={onOpen} className="block w-full text-left">
        <div className="relative w-full rounded overflow-hidden">
          {media}
          {topRightBadge ? (
            <div className="absolute right-1.5 top-1.5 inline-flex items-center px-1.5 py-0.5 rounded bg-emerald-600/90 text-white text-[9px] font-semibold uppercase shadow">
              {topRightBadge}
            </div>
          ) : null}
        </div>
        <div className="mt-1 flex items-center gap-1">
          <p className="flex-1 min-w-0 text-xs font-semibold leading-snug line-clamp-2">{title || 'Untitled'}</p>
          {overflowMenu ? (
            <div className="flex-shrink-0" onClick={(e) => e.stopPropagation()}>
              {overflowMenu}
            </div>
          ) : null}
        </div>
        {subtitle ? (
          <p className="-mt-0.5 text-[10px] leading-tight text-gray-500 dark:text-gray-400 truncate">{subtitle}</p>
        ) : null}
        {metaLine ? (
          <p className="-mt-0.5 text-[10px] leading-tight text-gray-500 dark:text-gray-400 truncate">{metaLine}</p>
        ) : null}
      </button>

      {footer}
    </div>
  );
};
