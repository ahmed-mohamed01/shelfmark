import { ReactNode } from 'react';
import { MonitoredTableRowBase } from './MonitoredTableRowBase';

interface MonitoredAuthorTableRowProps {
  name: string;
  subtitle: string;
  thumbnail: ReactNode;
  onOpen: () => void;
  trailingAction?: ReactNode;
}

export const MonitoredAuthorTableRow = ({
  name,
  subtitle,
  thumbnail,
  onOpen,
  trailingAction,
}: MonitoredAuthorTableRowProps) => {
  const mainSlot = (
    <div className="min-w-0 flex flex-col justify-center sm:pl-3 text-left">
      <h3 className="font-semibold text-xs min-[400px]:text-sm sm:text-base leading-tight truncate" title={name || 'Unknown author'}>
        {name || 'Unknown author'}
      </h3>
      <p className="text-[10px] min-[400px]:text-xs sm:text-sm text-gray-600 dark:text-gray-300 truncate">
        {subtitle}
      </p>
    </div>
  );

  return (
    <MonitoredTableRowBase
      gridClassName={trailingAction ? 'grid-cols-[auto_minmax(0,1fr)_auto]' : 'grid-cols-[auto_minmax(0,1fr)]'}
      mediaSlot={thumbnail}
      mainSlot={mainSlot}
      rightSlot={trailingAction}
      onRowClick={onOpen}
    />
  );
};
