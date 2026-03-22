import { type ReactNode } from 'react';
import { Dropdown } from './Dropdown';

export interface FloatingSelectionBarMenuItem {
  label: string;
  onClick: () => void;
}

export interface FloatingSelectionBarAction {
  key: string;
  icon: ReactNode;
  title: string;
  onClick: () => void;
  disabled?: boolean;
  borderColor: 'teal' | 'emerald' | 'orange' | 'red';
  /** Active/highlighted state (e.g. select-all when all are selected) */
  active?: boolean;
  /** Show a vertical divider before this action */
  dividerBefore?: boolean;
  /** If provided, a small chevron appears on the button; clicking the chevron opens a dropdown with these items. The main button area still fires onClick directly. */
  menuItems?: FloatingSelectionBarMenuItem[];
}

interface FloatingSelectionBarProps {
  count: number;
  actions: FloatingSelectionBarAction[];
  onSelectAll?: () => void;
  allSelected?: boolean;
  onDeselectAll: () => void;
}

const borderColorMap = {
  teal: 'border-teal-400/50 dark:border-teal-500/40',
  emerald: 'border-emerald-400/50 dark:border-emerald-500/40',
  orange: 'border-orange-400/50 dark:border-orange-500/40',
  red: 'border-red-400/50 dark:border-red-500/40',
} as const;

const baseBtnClass = 'inline-flex items-center justify-center hover-action disabled:opacity-40 disabled:cursor-not-allowed transition-colors';

function SplitButton({ action }: { action: FloatingSelectionBarAction }) {
  const colorClass = borderColorMap[action.borderColor];
  const stateClass = action.active ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-500' : 'text-gray-600 dark:text-gray-300';

  return (
    <Dropdown
      widthClassName="w-auto"
      align="right"
      panelClassName="z-[2200] min-w-[180px] rounded-xl border border-[var(--border-muted)] shadow-2xl"
      noScrollLimit={true}
      usePortal={true}
      renderTrigger={({ isOpen, toggle }) => (
        <div className={`inline-flex items-center rounded-lg border ${colorClass} overflow-hidden ${action.disabled ? 'opacity-40 cursor-not-allowed' : ''}`}>
          {/* Main action area */}
          <button
            type="button"
            onClick={action.onClick}
            disabled={action.disabled}
            className={`h-9 w-8 ${baseBtnClass} ${stateClass}`}
            title={action.title}
            aria-label={action.title}
          >
            {action.icon}
          </button>
          {/* Chevron / menu trigger */}
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); toggle(); }}
            disabled={action.disabled}
            className={`h-9 w-4 ${baseBtnClass} border-l ${colorClass} ${isOpen ? 'bg-gray-100 dark:bg-gray-700' : ''} ${stateClass}`}
            aria-label={`${action.title} options`}
            aria-haspopup="menu"
            aria-expanded={isOpen}
          >
            <svg className="w-2.5 h-2.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2.5} aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" /></svg>
          </button>
        </div>
      )}
    >
      {({ close }) => (
        <div className="py-1">
          {action.menuItems!.map((item) => (
            <button
              key={item.label}
              type="button"
              onClick={() => { item.onClick(); close(); }}
              className="w-full px-3 py-2 text-left text-sm hover-surface whitespace-nowrap"
            >
              {item.label}
            </button>
          ))}
        </div>
      )}
    </Dropdown>
  );
}

export function FloatingSelectionBar({ count, actions, onSelectAll, allSelected, onDeselectAll }: FloatingSelectionBarProps) {
  if (count === 0) return null;

  return (
    <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[100] animate-slide-up max-w-[calc(100vw-2rem)]" style={{ paddingBottom: 'env(safe-area-inset-bottom)' }} role="toolbar" aria-label={`${count} item${count !== 1 ? 's' : ''} selected`}>
      <div className="inline-flex items-center gap-1.5 rounded-2xl border border-[var(--border-muted)] bg-white/90 dark:bg-gray-800/90 backdrop-blur-md shadow-xl px-3 py-2">
        {/* Selection count */}
        <div className="flex items-center gap-1.5 text-xs font-medium text-gray-500 dark:text-gray-400 pr-1.5 select-none" aria-live="polite">
          <svg className="w-4 h-4 text-emerald-500" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2} aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="m4.5 12.75 6 6 9-13.5" /></svg>
          <span>{count} selected</span>
        </div>

        {/* Action buttons */}
        {actions.map((action) => (
          <span key={action.key} className="inline-flex items-center gap-1.5">
            {action.dividerBefore ? <div className="w-px h-5 bg-gray-300 dark:bg-gray-600 mx-0.5" aria-hidden="true" /> : null}
            {action.menuItems ? (
              <SplitButton action={action} />
            ) : (
              <button
                type="button"
                onClick={action.onClick}
                disabled={action.disabled}
                className={`w-9 h-9 rounded-lg border ${borderColorMap[action.borderColor]} ${baseBtnClass} ${action.active ? 'bg-emerald-50 dark:bg-emerald-900/20 text-emerald-500' : 'text-gray-600 dark:text-gray-300'}`}
                title={action.title}
                aria-label={action.title}
              >
                {action.icon}
              </button>
            )}
          </span>
        ))}

        {/* Divider */}
        <div className="w-px h-5 bg-gray-300 dark:bg-gray-600 mx-0.5" aria-hidden="true" />

        {/* Select all */}
        {onSelectAll ? (
          <button
            type="button"
            onClick={() => onSelectAll!()}
            className={`w-9 h-9 rounded-lg border ${borderColorMap.emerald} ${baseBtnClass} ${allSelected ? 'text-emerald-500 bg-emerald-50 dark:bg-emerald-900/20' : 'text-gray-600 dark:text-gray-300'}`}
            title={allSelected ? 'Deselect all' : 'Select all visible'}
            aria-label={allSelected ? 'Deselect all' : 'Select all visible'}
          >
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8} aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z" /></svg>
          </button>
        ) : null}

        {/* Deselect all */}
        <button
          type="button"
          onClick={onDeselectAll}
          className={`w-9 h-9 rounded-lg border ${borderColorMap.orange} ${baseBtnClass} text-gray-600 dark:text-gray-300`}
          title="Deselect all"
          aria-label="Deselect all"
        >
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8} aria-hidden="true"><path strokeLinecap="round" strokeLinejoin="round" d="M6 18 18 6M6 6l12 12" /></svg>
        </button>
      </div>
    </div>
  );
}
