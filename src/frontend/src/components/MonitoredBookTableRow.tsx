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
}: MonitoredBookTableRowProps) => {
  const mainSlot = (
    <button
      type="button"
      className="min-w-0 flex flex-col justify-center sm:pl-3 text-left"
      onClick={onOpen}
    >
      {titleRow}
      {subtitleRow}
      {metaRow}
    </button>
  );

  return (
    <MonitoredTableRowBase
      gridClassName="grid-cols-[auto_auto_minmax(0,1fr)_auto] sm:grid-cols-[auto_auto_minmax(0,2fr)_minmax(164px,164px)_minmax(64px,64px)]"
      leftSlot={leadingControl}
      mediaSlot={thumbnail}
      mainSlot={mainSlot}
      middleSlot={availabilitySlot}
      rightSlot={trailingSlot}
    />
  );
};
