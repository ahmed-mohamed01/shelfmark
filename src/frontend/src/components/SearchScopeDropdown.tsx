import { useEffect } from 'react';

import { Dropdown } from './Dropdown';

export type SearchScope = 'books' | 'authors';

interface SearchScopeDropdownProps {
  scope: SearchScope;
  onScopeChange: (scope: SearchScope) => void;
  /** Tighter trigger sizing for the small header search pill. */
  compact?: boolean;
  /** Fires as the popover opens/closes, e.g. to accent the pill border while open. */
  onOpenChange?: (open: boolean) => void;
}

const PersonIcon = ({ className }: { className: string }) => (
  <svg
    className={className}
    fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    strokeWidth={1.5}
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M15.75 6a3.75 3.75 0 1 1-7.5 0 3.75 3.75 0 0 1 7.5 0ZM4.501 20.118a7.5 7.5 0 0 1 14.998 0A17.933 17.933 0 0 1 12 21.75c-2.676 0-5.216-.584-7.499-1.632Z"
    />
  </svg>
);

const BookIcon = ({ className }: { className: string }) => (
  <svg
    className={className}
    fill="none"
    stroke="currentColor"
    viewBox="0 0 24 24"
    strokeWidth={1.5}
  >
    <path
      strokeLinecap="round"
      strokeLinejoin="round"
      d="M12 6.042A8.967 8.967 0 0 0 6 3.75c-1.052 0-2.062.18-3 .512v14.25A8.987 8.987 0 0 1 6 18c2.305 0 4.408.867 6 2.292m0-14.25a8.966 8.966 0 0 1 6-2.292c1.052 0 2.062.18 3 .512v14.25A8.987 8.987 0 0 0 18 18a8.967 8.967 0 0 0-6 2.292m0-14.25v14.25"
    />
  </svg>
);

/**
 * Authors/Books scope selector for a search pill — the leading icon + chevron
 * segment with a popover, mirroring the Monitored page's in-page search
 * selector (see the design handoff "Header Search Dropdown"). The chevron
 * rotates while open; use onOpenChange to accent the host pill's border.
 */
export const SearchScopeDropdown = ({
  scope,
  onScopeChange,
  compact = false,
  onOpenChange,
}: SearchScopeDropdownProps) => {
  const iconClass = compact ? 'h-[18px] w-[18px]' : 'h-5 w-5';
  const triggerPadding = compact ? 'py-1.5 pr-2 pl-3' : 'py-2.5 pr-2 pl-4';

  // Dropdown only reports open/close from interactions, so tell the host we're
  // closed if we unmount while open (e.g. the desktop-only header pill leaving
  // the tree on resize) — otherwise its open-state styling sticks.
  useEffect(() => {
    return () => onOpenChange?.(false);
  }, [onOpenChange]);

  const row = (close: () => void, target: SearchScope, label: string, Icon: typeof PersonIcon) => (
    <button
      type="button"
      onClick={() => {
        onScopeChange(target);
        close();
      }}
      className={`hover-surface flex w-full items-center gap-3 rounded-lg px-3 py-2 text-left text-sm font-semibold ${
        scope === target ? 'text-emerald-600 dark:text-emerald-400' : 'text-[var(--text)]'
      }`}
    >
      <Icon className="h-[18px] w-[18px]" />
      {label}
    </button>
  );

  return (
    <div className="flex shrink-0 items-stretch self-stretch">
      <Dropdown
        align="left"
        widthClassName="flex items-stretch"
        usePortal
        // Dropdown's portal panel only gets a z-index when no panelClassName is
        // passed, so one must be restated here: above the sticky header (z-40)
        // so the panel's top edge isn't painted over, but below modal overlays
        // (z-1200+) so the popover can never float over an open dialog.
        panelClassName="z-[100] min-w-[168px] p-1"
        onOpenChange={onOpenChange}
        renderTrigger={({ isOpen, toggle }) => (
          <button
            type="button"
            onClick={toggle}
            className={`hover-action flex h-full shrink-0 items-center gap-1 rounded-l-full border-r border-[var(--border-muted)] text-gray-500 transition-colors hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200 ${triggerPadding}`}
            aria-expanded={isOpen}
            aria-label="Search scope"
            title={scope === 'authors' ? 'Searching authors' : 'Searching books'}
          >
            {scope === 'authors' ? (
              <PersonIcon className={iconClass} />
            ) : (
              <BookIcon className={iconClass} />
            )}
            <svg
              className={`h-3 w-3 transition-transform duration-150 ${isOpen ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth={2}
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
            </svg>
          </button>
        )}
      >
        {({ close }) => (
          <div className="flex flex-col gap-0.5 py-0.5">
            {row(close, 'authors', 'Authors', PersonIcon)}
            {row(close, 'books', 'Books', BookIcon)}
          </div>
        )}
      </Dropdown>
    </div>
  );
};
