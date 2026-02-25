import { ReactNode } from 'react';

interface MediaCompactTileBaseProps {
  title: string;
  media: ReactNode;
  onOpen: () => void;
  overflowMenu?: ReactNode;
  topLeftOverlay?: ReactNode;
  topRightOverlay?: ReactNode;
  subtitle?: string;
  metaLine?: string;
  footer?: ReactNode;
  tooltip?: string;
  isDimmed?: boolean;
}

export const MediaCompactTileBase = ({
  title,
  media,
  onOpen,
  overflowMenu,
  topLeftOverlay,
  topRightOverlay,
  subtitle,
  metaLine,
  footer,
  tooltip,
  isDimmed = false,
}: MediaCompactTileBaseProps) => {
  const computedTooltip = tooltip || [title, subtitle, metaLine].filter(Boolean).join('\n');

  return (
    <div className="group relative self-start h-fit rounded-xl border border-[var(--border-muted)] bg-[var(--bg)]" title={computedTooltip}>
      {topLeftOverlay ? (
        <div className={`absolute left-2 top-2 z-20 ${isDimmed ? 'opacity-50' : ''}`}>
          {topLeftOverlay}
        </div>
      ) : null}
      <button type="button" onClick={onOpen} className="block w-full text-left">
        <div className={`relative w-full overflow-hidden rounded-t-xl ${isDimmed ? 'opacity-50' : ''}`}>
          {media}
          {topRightOverlay ? (
            <div className="absolute right-1.5 top-1.5 z-20 flex flex-col items-end gap-1">
              {topRightOverlay}
            </div>
          ) : null}
        </div>
        <div className="pl-2 pr-0.5 pt-1.5 flex items-center gap-0.5">
          <p className={`flex-1 min-w-0 text-xs font-semibold leading-snug truncate ${isDimmed ? 'opacity-50' : ''}`}>{title || 'Untitled'}</p>
          {overflowMenu ? (
            <div className="flex-shrink-0 -mr-0.5" onClick={(e) => e.stopPropagation()}>
              {overflowMenu}
            </div>
          ) : null}
        </div>
        {subtitle ? (
          <p className={`px-2 text-[10px] leading-tight text-gray-600 dark:text-gray-300 truncate ${isDimmed ? 'opacity-50' : ''}`}>{subtitle}</p>
        ) : null}
        {metaLine ? (
          <p className={`px-2 text-[10px] leading-tight text-gray-500 dark:text-gray-400 truncate ${isDimmed ? 'opacity-50' : ''}`}>{metaLine}</p>
        ) : null}
      </button>

      {footer ? <div className={`px-2 ${isDimmed ? 'opacity-50' : ''}`}>{footer}</div> : null}
      <div className="h-2" />
    </div>
  );
};
