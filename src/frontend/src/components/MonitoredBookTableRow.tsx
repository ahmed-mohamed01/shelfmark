import { ReactNode } from 'react';
import { MonitoredTableRowBase } from './MonitoredTableRowBase';

interface MonitoredBookTableRowProps {
  leadingControl: ReactNode;
  thumbnail: ReactNode;
  onOpen: () => void;
  titleRow: ReactNode;
  subtitleRow: ReactNode;
  metaRow?: ReactNode;
  availabilitySlot: ReactNode;
  trailingSlot: ReactNode;
  isDimmed?: boolean;
  hasActiveSelection?: boolean;
}

export const MonitoredBookTableRow = ({
  leadingControl,
  thumbnail,
  onOpen,
  titleRow,
  subtitleRow,
  metaRow,
  availabilitySlot,
  trailingSlot,
  isDimmed = false,
  hasActiveSelection = false,
}: MonitoredBookTableRowProps) => {
  const mainSlot = (
    <button
      type="button"
      className="w-full min-w-0 flex flex-col justify-center sm:pl-3 text-left"
      onClick={onOpen}
    >
      {titleRow}
      {subtitleRow}
      {metaRow}
    </button>
  );

  const gridClassName = hasActiveSelection
    ? 'grid-cols-[auto_auto_minmax(0,1fr)_auto] md:grid-cols-[auto_auto_minmax(0,2fr)_minmax(0,190px)_minmax(90px,90px)]'
    : 'grid-cols-[0px_auto_minmax(0,1fr)_auto] md:grid-cols-[0px_auto_minmax(0,2fr)_minmax(0,190px)_minmax(90px,90px)]';

  return (
    <MonitoredTableRowBase
      gridClassName={gridClassName}
      leftSlot={leadingControl}
      mediaSlot={thumbnail}
      mainSlot={mainSlot}
      mainClassName="min-w-0 overflow-hidden"
      leftClassName="flex items-center justify-center overflow-hidden"
      middleSlot={availabilitySlot}
      middleClassName="hidden md:flex w-full items-center justify-center gap-1"
      rightSlot={trailingSlot}
      isDimmed={isDimmed}
    />
  );
};
