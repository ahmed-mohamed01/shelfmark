import { ReactNode, useCallback, useRef } from 'react';
import { hapticTap } from '../utils/haptics';

interface MonitoredTableRowBaseProps {
  gridClassName: string;
  leftSlot?: ReactNode;
  mediaSlot?: ReactNode;
  mainSlot: ReactNode;
  middleSlot?: ReactNode;
  rightSlot?: ReactNode;
  leftClassName?: string;
  mediaClassName?: string;
  mainClassName?: string;
  middleClassName?: string;
  rightClassName?: string;
  rowClassName?: string;
  onRowClick?: () => void;
  isDimmed?: boolean;
  /** When true and onToggleSelect is provided, clicking the row toggles selection instead of onRowClick */
  hasActiveSelection?: boolean;
  onToggleSelect?: () => void;
}

export const MonitoredTableRowBase = ({
  gridClassName,
  leftSlot,
  mediaSlot,
  mainSlot,
  middleSlot,
  rightSlot,
  leftClassName = 'flex items-center justify-center pl-0.5 sm:pl-1 overflow-hidden',
  mediaClassName = 'flex items-center pl-1 sm:pl-3',
  mainClassName,
  middleClassName = 'hidden sm:flex w-full items-center justify-center gap-1',
  rightClassName = 'relative flex flex-row justify-end gap-1 sm:gap-1.5 sm:pr-3',
  rowClassName = 'group px-1.5 sm:px-2 py-1.5 sm:py-2 transition-colors duration-200 hover-row w-full',
  onRowClick,
  isDimmed = false,
  hasActiveSelection = false,
  onToggleSelect,
}: MonitoredTableRowBaseProps) => {
  const toggleSelectWithHaptic = useCallback(() => {
    if (!onToggleSelect) return;
    hapticTap();
    onToggleSelect();
  }, [onToggleSelect]);

  const effectiveRowClick = hasActiveSelection && onToggleSelect ? toggleSelectWithHaptic : onRowClick;

  // Long-press to enter selection mode
  const longPressTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const didLongPress = useRef(false);

  const onPointerDown = useCallback(() => {
    if (!onToggleSelect) return;
    didLongPress.current = false;
    longPressTimer.current = setTimeout(() => {
      didLongPress.current = true;
      toggleSelectWithHaptic();
    }, 500);
  }, [onToggleSelect, toggleSelectWithHaptic]);

  const clearLongPress = useCallback(() => {
    if (longPressTimer.current) { clearTimeout(longPressTimer.current); longPressTimer.current = null; }
  }, []);

  const shouldIgnoreRowClick = (target: EventTarget | null, rowElement: HTMLDivElement): boolean => {
    if (!(target instanceof Element)) return false;
    const interactiveAncestor = target.closest('button,a,input,select,textarea,[role="button"],[role="checkbox"],[role="switch"]');
    if (!interactiveAncestor) return false;
    return interactiveAncestor !== rowElement;
  };

  const handleRowClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (didLongPress.current) { didLongPress.current = false; return; }
    if (!effectiveRowClick || shouldIgnoreRowClick(event.target, event.currentTarget)) return;
    effectiveRowClick();
  };

  const handleRowKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!effectiveRowClick) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (shouldIgnoreRowClick(event.target, event.currentTarget)) return;
    event.preventDefault();
    effectiveRowClick();
  };

  return (
    <div
      className={`${rowClassName}${effectiveRowClick ? ' cursor-pointer' : ''}${isDimmed ? ' opacity-50' : ''}`}
      style={onToggleSelect ? { WebkitTouchCallout: 'none', userSelect: 'none' } as React.CSSProperties : undefined}
      onClick={handleRowClick}
      onKeyDown={handleRowKeyDown}
      onContextMenu={onToggleSelect ? (e) => e.preventDefault() : undefined}
      onPointerDown={onToggleSelect ? onPointerDown : undefined}
      onPointerUp={onToggleSelect ? clearLongPress : undefined}
      onPointerCancel={onToggleSelect ? clearLongPress : undefined}
      onPointerLeave={onToggleSelect ? clearLongPress : undefined}
      role={effectiveRowClick ? 'button' : undefined}
      tabIndex={effectiveRowClick ? 0 : undefined}
    >
      <div className={`grid items-center gap-2 sm:gap-y-1 sm:gap-x-2 w-full ${gridClassName}`}>
        <div className={leftClassName}>{leftSlot}</div>
        {mediaSlot ? <div className={mediaClassName}>{mediaSlot}</div> : null}
        <div className={mainClassName}>{mainSlot}</div>
        {middleSlot ? <div className={middleClassName}>{middleSlot}</div> : null}
        {rightSlot ? <div className={rightClassName}>{rightSlot}</div> : null}
      </div>
    </div>
  );
};
