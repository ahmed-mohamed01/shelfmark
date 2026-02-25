import { ReactNode, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

// Find the closest scrollable ancestor element
function getScrollableAncestor(element: HTMLElement | null): HTMLElement | null {
  let current = element?.parentElement;
  while (current) {
    const style = getComputedStyle(current);
    const overflowY = style.overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll') {
      return current;
    }
    current = current.parentElement;
  }
  return null;
}

// Simple throttle function to limit how often a function can be called
function throttle<T extends (...args: unknown[]) => void>(fn: T, delay: number): T {
  let lastCall = 0;
  let timeoutId: ReturnType<typeof setTimeout> | null = null;

  return ((...args: unknown[]) => {
    const now = Date.now();
    const timeSinceLastCall = now - lastCall;

    if (timeSinceLastCall >= delay) {
      lastCall = now;
      fn(...args);
    } else if (!timeoutId) {
      // Schedule a trailing call
      timeoutId = setTimeout(() => {
        lastCall = Date.now();
        timeoutId = null;
        fn(...args);
      }, delay - timeSinceLastCall);
    }
  }) as T;
}

interface DropdownProps {
  label?: string;
  summary?: ReactNode;
  children: (helpers: { close: () => void }) => ReactNode;
  align?: 'left' | 'right';
  widthClassName?: string;
  buttonClassName?: string;
  panelClassName?: string;
  disabled?: boolean;
  renderTrigger?: (props: { isOpen: boolean; toggle: () => void }) => ReactNode;
  /** Disable max-height and overflow scrolling (for panels with nested dropdowns) */
  noScrollLimit?: boolean;
  /** Render the panel in a portal to escape overflow:hidden containers */
  usePortal?: boolean;
}

export const Dropdown = ({
  label,
  summary,
  children,
  align = 'left',
  widthClassName = 'w-full',
  buttonClassName = '',
  panelClassName = '',
  disabled = false,
  renderTrigger,
  noScrollLimit = false,
  usePortal = false,
}: DropdownProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [panelDirection, setPanelDirection] = useState<'down' | 'up'>('down');
  const [portalPosition, setPortalPosition] = useState<{ top: number; left: number; width: number } | null>(null);

  const toggleOpen = () => {
    if (disabled) return;
    setIsOpen(prev => !prev);
  };

  const close = () => setIsOpen(false);

  useEffect(() => {
    if (!isOpen) return;

    const handleClick = (event: MouseEvent) => {
      const target = event.target as Node;
      // Check if click is inside container or inside the portal panel
      const isInsideContainer = containerRef.current?.contains(target);
      const isInsidePanel = panelRef.current?.contains(target);
      if (!isInsideContainer && !isInsidePanel) {
        close();
      }
    };

    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        close();
      }
    };

    document.addEventListener('mousedown', handleClick);
    document.addEventListener('keydown', handleEscape);

    return () => {
      document.removeEventListener('mousedown', handleClick);
      document.removeEventListener('keydown', handleEscape);
    };
  }, [isOpen]);

  // Memoize the panel direction calculation
  const updatePanelDirection = useCallback(() => {
    const triggerEl = usePortal ? triggerRef.current : containerRef.current;
    if (!triggerEl || !panelRef.current) {
      return;
    }

    const rect = triggerEl.getBoundingClientRect();
    const panelHeight = panelRef.current.offsetHeight || panelRef.current.scrollHeight;

    // Check if we're inside a scrollable container and use its bounds
    const scrollableAncestor = getScrollableAncestor(triggerEl);
    const containerBottom = scrollableAncestor
      ? scrollableAncestor.getBoundingClientRect().bottom
      : window.innerHeight;
    const containerTop = scrollableAncestor
      ? scrollableAncestor.getBoundingClientRect().top
      : 0;

    const spaceBelow = containerBottom - rect.bottom - 8;
    const spaceAbove = rect.top - containerTop - 8;
    const shouldOpenUp = spaceBelow < panelHeight && spaceAbove >= panelHeight;

    setPanelDirection(shouldOpenUp ? 'up' : 'down');

    // Update portal position
    if (usePortal) {
      const panelWidth = panelRef.current.offsetWidth || 200;
      setPortalPosition({
        top: shouldOpenUp ? rect.top - panelHeight - 8 : rect.bottom + 8,
        left: align === 'right' ? rect.right - panelWidth : rect.left,
        width: panelWidth,
      });
    }
  }, [usePortal, align]);

  useLayoutEffect(() => {
    if (!isOpen) return;

    // Throttle scroll/resize handlers to reduce layout thrashing
    const throttledUpdate = throttle(updatePanelDirection, 100);

    updatePanelDirection();
    window.addEventListener('resize', throttledUpdate);
    window.addEventListener('scroll', throttledUpdate, true);

    return () => {
      window.removeEventListener('resize', throttledUpdate);
      window.removeEventListener('scroll', throttledUpdate, true);
    };
  }, [isOpen, updatePanelDirection]);

  return (
    <div className={`${widthClassName} relative ${isOpen ? 'z-[2600]' : 'z-0'}`} ref={containerRef}>
      {label && (
        <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-1.5" onClick={toggleOpen}>
          {label}
        </label>
      )}
      <div className="relative" ref={triggerRef}>
        {renderTrigger ? (
          renderTrigger({ isOpen, toggle: toggleOpen })
        ) : (
          <button
            type="button"
            onClick={toggleOpen}
            disabled={disabled}
            className={`w-full px-3 py-2 text-sm border flex items-center justify-between text-left focus:outline-none focus-visible:outline-none focus-visible:ring-0 focus-visible:ring-offset-0 transition-[border-radius] duration-150 ${buttonClassName}`}
            style={{
              background: 'var(--bg-soft)',
              color: 'var(--text)',
              borderColor: 'var(--border-muted)',
              borderRadius: isOpen
                ? panelDirection === 'down'
                  ? '0.5rem 0.5rem 0 0'
                  : '0 0 0.5rem 0.5rem'
                : '0.5rem',
            }}
          >
            <span className="truncate">
              {summary ?? <span className="opacity-60">Select an option</span>}
            </span>
            <svg
              className={`w-4 h-4 transition-transform ${isOpen ? 'rotate-180' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
              strokeWidth="1.5"
            >
              <path strokeLinecap="round" strokeLinejoin="round" d="m19.5 8.25-7.5 7.5-7.5-7.5" />
            </svg>
          </button>
        )}

        {isOpen && !usePortal && (
          <div
            ref={panelRef}
            className={`absolute ${align === 'right' ? 'right-0' : 'left-0'} ${
              panelDirection === 'down'
                ? renderTrigger ? 'mt-2' : ''
                : renderTrigger ? 'bottom-full mb-2' : 'bottom-full'
            } border z-20 ${panelDirection === 'down' ? 'shadow-lg' : ''} ${panelClassName || widthClassName}`}
            style={{
              background: 'var(--bg)',
              borderColor: 'var(--border-muted)',
              borderRadius: renderTrigger
                ? '0.5rem'
                : panelDirection === 'down'
                  ? '0 0 0.5rem 0.5rem'
                  : '0.5rem 0.5rem 0 0',
              marginTop: !renderTrigger && panelDirection === 'down' ? '-1px' : undefined,
              marginBottom: !renderTrigger && panelDirection === 'up' ? '-1px' : undefined,
            }}
          >
            <div className={noScrollLimit ? '' : 'max-h-64 overflow-auto'}>
              {children({ close })}
            </div>
          </div>
        )}
      </div>
      {isOpen && usePortal && createPortal(
        <div
          ref={panelRef}
          className={`fixed border z-[9999] shadow-xl ${panelClassName || widthClassName}`}
          style={{
            background: 'var(--bg)',
            borderColor: 'var(--border-muted)',
            borderRadius: '0.5rem',
            top: portalPosition?.top ?? 0,
            left: portalPosition?.left ?? 0,
          }}
        >
          <div className={noScrollLimit ? '' : 'max-h-64 overflow-auto'}>
            {children({ close })}
          </div>
        </div>,
        document.body
      )}
    </div>
  );
};

