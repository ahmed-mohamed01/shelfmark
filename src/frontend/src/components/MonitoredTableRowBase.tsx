import { ReactNode } from 'react';

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
}

export const MonitoredTableRowBase = ({
  gridClassName,
  leftSlot,
  mediaSlot,
  mainSlot,
  middleSlot,
  rightSlot,
  leftClassName = 'flex items-center justify-center pl-0.5 sm:pl-1',
  mediaClassName = 'flex items-center pl-1 sm:pl-3',
  mainClassName,
  middleClassName = 'hidden sm:flex w-full items-center justify-center gap-1',
  rightClassName = 'relative flex flex-row justify-end gap-1 sm:gap-1.5 sm:pr-3',
  rowClassName = 'group px-1.5 sm:px-2 py-1.5 sm:py-2 transition-colors duration-200 hover-row w-full',
  onRowClick,
  isDimmed = false,
}: MonitoredTableRowBaseProps) => {
  const shouldIgnoreRowClick = (target: EventTarget | null, rowElement: HTMLDivElement): boolean => {
    if (!(target instanceof Element)) return false;
    const interactiveAncestor = target.closest('button,a,input,select,textarea,[role="button"],[role="checkbox"],[role="switch"]');
    if (!interactiveAncestor) return false;
    return interactiveAncestor !== rowElement;
  };

  const handleRowClick = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!onRowClick || shouldIgnoreRowClick(event.target, event.currentTarget)) return;
    onRowClick();
  };

  const handleRowKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (!onRowClick) return;
    if (event.key !== 'Enter' && event.key !== ' ') return;
    if (shouldIgnoreRowClick(event.target, event.currentTarget)) return;
    event.preventDefault();
    onRowClick();
  };

  return (
    <div
      className={`${rowClassName}${onRowClick ? ' cursor-pointer' : ''}${isDimmed ? ' opacity-50' : ''}`}
      onClick={handleRowClick}
      onKeyDown={handleRowKeyDown}
      role={onRowClick ? 'button' : undefined}
      tabIndex={onRowClick ? 0 : undefined}
    >
      <div className={`grid items-center gap-2 sm:gap-y-1 sm:gap-x-2 w-full ${gridClassName}`}>
        {leftSlot ? <div className={leftClassName}>{leftSlot}</div> : null}
        {mediaSlot ? <div className={mediaClassName}>{mediaSlot}</div> : null}
        <div className={mainClassName}>{mainSlot}</div>
        {middleSlot ? <div className={middleClassName}>{middleSlot}</div> : null}
        {rightSlot ? <div className={rightClassName}>{rightSlot}</div> : null}
      </div>
    </div>
  );
};
