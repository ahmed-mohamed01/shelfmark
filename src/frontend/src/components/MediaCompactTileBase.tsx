import { type ReactNode } from 'react';

interface MediaCompactTileBaseProps {
  title: string;
  media: ReactNode;
  onOpen?: (() => void) | undefined;
  overflowMenu?: ReactNode;
  topLeftOverlay?: ReactNode;
  topRightOverlay?: ReactNode;
  bottomRightOverlay?: ReactNode;
  seriesLine?: string;
  subtitle?: string;
  metaLine?: ReactNode;
  footer?: ReactNode;
  tooltip?: string;
  isDimmed?: boolean;
  isSelected?: boolean;
}

export const MediaCompactTileBase = ({
  title,
  media,
  onOpen,
  overflowMenu,
  topLeftOverlay,
  topRightOverlay,
  bottomRightOverlay,
  seriesLine,
  subtitle,
  metaLine,
  footer,
  tooltip,
  isDimmed = false,
  isSelected = false,
}: MediaCompactTileBaseProps) => {
  const computedTooltip = tooltip || [title, seriesLine, subtitle, typeof metaLine === 'string' ? metaLine : undefined].filter(Boolean).join('\n');
  const mediaContent = (
    <div className={`relative w-full overflow-hidden rounded-t-xl flex flex-col ${isDimmed ? 'opacity-50' : ''}`}>
      {media}
      {topRightOverlay ? (
        <div className="absolute right-1.5 top-1.5 z-20 flex flex-col items-end gap-1 leading-normal">
          {topRightOverlay}
        </div>
      ) : null}
      {bottomRightOverlay ? (
        <div className="absolute right-1.5 bottom-1.5 z-20 flex flex-col items-end gap-1 leading-normal">
          {bottomRightOverlay}
        </div>
      ) : null}
    </div>
  );

  return (
    <div className={`group relative self-start h-fit rounded-xl bg-[var(--bg)] ${isSelected ? 'ring-2 ring-emerald-500 dark:ring-emerald-400' : 'border border-[var(--border-muted)]'}`} title={computedTooltip}>
      {topLeftOverlay ? (
        <div className={`absolute left-2 top-2 z-20 ${isDimmed ? 'opacity-50' : ''}`}>
          {topLeftOverlay}
        </div>
      ) : null}
      {onOpen ? (
        <button type="button" onClick={onOpen} className="block w-full text-left leading-[0]">
          {mediaContent}
        </button>
      ) : (
        <div className="leading-[0]">{mediaContent}</div>
      )}

      <div className="flex items-start gap-1 pl-2 pr-0.5 pt-1">
        {onOpen ? (
          <button type="button" onClick={onOpen} className="min-w-0 flex-1 text-left">
            <p className={`text-xs font-semibold leading-snug truncate ${isDimmed ? 'opacity-50' : ''}`}>{title || 'Untitled'}</p>
          </button>
        ) : (
          <p className={`min-w-0 flex-1 text-xs font-semibold leading-snug truncate ${isDimmed ? 'opacity-50' : ''}`}>{title || 'Untitled'}</p>
        )}
        {overflowMenu ? (
          <div className="flex-shrink-0 z-30 -my-1">
            {overflowMenu}
          </div>
        ) : null}
      </div>

      {(seriesLine || subtitle || metaLine) ? (
        <div className={`px-2 space-y-px ${isDimmed ? 'opacity-50' : ''}`}>
          {subtitle ? <div className="text-[10px] leading-none font-medium text-gray-600 dark:text-gray-300 truncate">{subtitle}</div> : null}
          {metaLine ? <div className="text-[10px] leading-none text-gray-500 dark:text-gray-400 truncate">{metaLine}</div> : null}
          {seriesLine ? <div className="text-[9px] leading-none text-gray-400 dark:text-gray-500 truncate">{seriesLine}</div> : null}
        </div>
      ) : null}

      {footer ? <div className={`px-2 ${isDimmed ? 'opacity-50' : ''}`}>{footer}</div> : null}
      <div className="h-1.5" />
    </div>
  );
};
