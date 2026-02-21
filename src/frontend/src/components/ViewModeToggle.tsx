import { ReactNode } from 'react';

export interface ViewModeToggleOption {
  value: string;
  label: string;
  icon: ReactNode;
  title?: string;
}

interface ViewModeToggleProps {
  value: string;
  options: ViewModeToggleOption[];
  onChange: (value: string) => void;
  className?: string;
}

export const ViewModeToggle = ({
  value,
  options,
  onChange,
  className,
}: ViewModeToggleProps) => {
  return (
    <div className={`items-center gap-1 rounded-full border border-[var(--border-muted)] p-0.5 ${className || 'inline-flex'}`}>
      {options.map((option) => {
        const isActive = option.value === value;
        return (
          <button
            key={option.value}
            type="button"
            onClick={() => onChange(option.value)}
            className={`h-7 w-7 rounded-full inline-flex items-center justify-center transition-colors ${isActive ? 'bg-emerald-600 text-white' : 'text-gray-500 hover:text-gray-900 dark:hover:text-gray-100 hover-action'}`}
            aria-label={option.label}
            title={option.title || option.label}
            aria-pressed={isActive}
          >
            {option.icon}
          </button>
        );
      })}
    </div>
  );
};
