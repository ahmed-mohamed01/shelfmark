import { ReactNode } from 'react';
import { MonitoredTableRowBase } from './MonitoredTableRowBase';

interface MonitoredAuthorTableRowProps {
  name: string;
  subtitle: string;
  thumbnail: ReactNode;
  onOpen: () => void;
  onEdit?: () => void;
  trailingAction?: ReactNode;
  onToggleSelect?: () => void;
  isSelected?: boolean;
  hasActiveSelection?: boolean;
}

export const MonitoredAuthorTableRow = ({
  name,
  subtitle,
  thumbnail,
  onOpen,
  onEdit,
  trailingAction,
  onToggleSelect,
  isSelected = false,
  hasActiveSelection = false,
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

  const leftSlot = (
    <div className="flex items-center gap-1">
      {onToggleSelect ? (
        <button
          type="button"
          onClick={onToggleSelect}
          className={`${isSelected ? 'text-emerald-500 dark:text-emerald-400 opacity-100' : hasActiveSelection ? 'text-gray-500 dark:text-gray-300 opacity-100' : 'text-gray-500 dark:text-gray-300 opacity-0 group-hover:opacity-100'} transition-opacity hover-action rounded-full p-0.5`}
          role="checkbox"
          aria-checked={isSelected}
          aria-label={`Select ${name || 'author'}`}
        >
          {isSelected ? (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
              <rect x="4" y="4" width="16" height="16" rx="3" />
              <path strokeLinecap="round" strokeLinejoin="round" d="m8 12 2.5 2.5L16 9" />
            </svg>
          ) : (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
              <rect x="4" y="4" width="16" height="16" rx="3" />
            </svg>
          )}
        </button>
      ) : null}
      {onEdit ? (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onEdit(); }}
          className="text-gray-500 dark:text-gray-300 opacity-0 group-hover:opacity-100 transition-opacity hover-action rounded-full p-0.5"
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
    <MonitoredTableRowBase
      gridClassName={onToggleSelect
        ? (trailingAction ? 'grid-cols-[auto_auto_minmax(0,1fr)_auto]' : 'grid-cols-[auto_auto_minmax(0,1fr)]')
        : (trailingAction ? 'grid-cols-[auto_minmax(0,1fr)_auto]' : 'grid-cols-[auto_minmax(0,1fr)]')}
      leftSlot={leftSlot}
      mediaSlot={thumbnail}
      mainSlot={mainSlot}
      rightSlot={trailingAction}
      onRowClick={onOpen}
    />
  );
};
