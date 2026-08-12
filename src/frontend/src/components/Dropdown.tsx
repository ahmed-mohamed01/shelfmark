import type { ReactNode } from 'react';
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';

// Find the closest scrollable ancestor element
function getScrollableAncestor(element: HTMLElement | null): HTMLElement | null {
  let current = element?.parentElement;
  while (current) {
    const style = getComputedStyle(current);
    const overflowY = style.overflowY;
    if (overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'hidden') {
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
  align?: 'left' | 'right' | 'auto';
  widthClassName?: string;
  buttonClassName?: string;
  panelClassName?: string;
  disabled?: boolean;
  renderTrigger?: (props: { isOpen: boolean; toggle: () => void }) => ReactNode;
  /** Disable max-height and overflow scrolling (for panels with nested dropdowns) */
  noScrollLimit?: boolean;
  /** Render the panel in a portal to escape overflow:hidden containers */
  usePortal?: boolean;
  triggerChrome?: 'default' | 'minimal';
  onOpenChange?: (isOpen: boolean) => void;
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
  triggerChrome = 'default',
  onOpenChange,
}: DropdownProps) => {
  const [isOpen, setIsOpen] = useState(false);
  const dropdownId = useId();
  const containerRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const [panelDirection, setPanelDirection] = useState<'down' | 'up'>('down');
  const [portalPosition, setPortalPosition] = useState<{
    top: number;
    left: number;
    width: number;
    caretLeft: number;
  } | null>(null);
  // Computed horizontal offset for non-portal panels (relative to containerRef left edge)
  const [nonPortalLeft, setNonPortalLeft] = useState<number | null>(null);
  const [nonPortalCaretLeft, setNonPortalCaretLeft] = useState<number>(16);

  const toggleOpen = () => {
    if (disabled) return;
    setIsOpen((prev) => {
      const next = !prev;
      onOpenChange?.(next);
      return next;
    });
  };

  const close = () => {
    setIsOpen(false);
    onOpenChange?.(false);
  };

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
    const containerTop = scrollableAncestor ? scrollableAncestor.getBoundingClientRect().top : 0;

    const spaceBelow = containerBottom - rect.bottom - 8;
    const spaceAbove = rect.top - containerTop - 8;
    const shouldOpenUp = spaceBelow < panelHeight && spaceAbove >= panelHeight;

    setPanelDirection(shouldOpenUp ? 'up' : 'down');

    const panelWidth = panelRef.current.offsetWidth || 200;
    const viewportWidth = window.innerWidth;
    const triggerCenter = rect.left + rect.width / 2;

    // Ideal panel left in viewport coords
    const idealLeft = align === 'right' ? rect.right - panelWidth : rect.left;
    // Clamp to viewport with 8px margin
    const clampedLeft = Math.max(8, Math.min(viewportWidth - panelWidth - 8, idealLeft));
    // Caret offset relative to the panel's left edge, pointing at trigger centre
    const computedCaretLeft = Math.max(10, Math.min(panelWidth - 10, triggerCenter - clampedLeft));

    if (usePortal) {
      setPortalPosition({
        top: shouldOpenUp ? rect.top - panelHeight - 8 : rect.bottom + 8,
        left: clampedLeft,
        width: panelWidth,
        caretLeft: computedCaretLeft,
      });
    } else if (containerRef.current) {
      // Express clamped position relative to the container div
      const containerRect = containerRef.current.getBoundingClientRect();
      setNonPortalLeft(clampedLeft - containerRect.left);
      setNonPortalCaretLeft(computedCaretLeft);
    }
  }, [usePortal, align]);

  useLayoutEffect(() => {
    if (!isOpen) return;

    // Throttle scroll/resize handlers to reduce layout thrashing
    const throttledUpdate = throttle(updatePanelDirection, 100);

    updatePanelDirection();
    window.addEventListener('resize', throttledUpdate);

    // Close on page scroll; only update position when scrolling within the panel itself
    const handleScroll = (event: Event) => {
      if (panelRef.current?.contains(event.target as Node)) {
        throttledUpdate();
        return;
      }
      close();
    };
    window.addEventListener('scroll', handleScroll, true);

    return () => {
      window.removeEventListener('resize', throttledUpdate);
      window.removeEventListener('scroll', handleScroll, true);
    };
  }, [isOpen, updatePanelDirection]);

  // Reset computed position when closed so next open recalculates
  useEffect(() => {
    if (!isOpen) {
      setNonPortalLeft(null);
    }
  }, [isOpen]);

  // Rotated-square caret for "renderTrigger" dropdowns (seamless border, no seam line)
  const renderCaret = (direction: 'up' | 'down', offsetLeft: number) => {
    if (!renderTrigger) return null;
    // A 16×16 square rotated 45° — only the two outward-facing edges have a border.
    // The other two edges are buried inside the panel, so the join is seamless.
    return (
      <span
        className="pointer-events-none absolute z-10"
        aria-hidden="true"
        style={{
          width: 16,
          height: 16,
          transform: 'rotate(45deg)',
          background: 'var(--bg)',
          ...(direction === 'down'
            ? {
                top: -8,
                left: offsetLeft - 8,
                borderTop: '1px solid var(--border-muted)',
                borderLeft: '1px solid var(--border-muted)',
              }
            : {
                bottom: -8,
                left: offsetLeft - 8,
                borderBottom: '1px solid var(--border-muted)',
                borderRight: '1px solid var(--border-muted)',
              }),
        }}
      />
    );
  };

  return (
    <div className={`${widthClassName} relative ${isOpen ? 'z-[2600]' : 'z-0'}`} ref={containerRef}>
      {label && (
        <label
          htmlFor={dropdownId}
          className="mb-1.5 block cursor-pointer text-xs font-medium text-gray-500 dark:text-gray-400"
        >
          {label}
        </label>
      )}
      <div className="relative" ref={triggerRef}>
        {renderTrigger ? (
          renderTrigger({ isOpen, toggle: toggleOpen })
        ) : (
          <button
            id={dropdownId}
            type="button"
            onClick={toggleOpen}
            disabled={disabled}
            className={`flex w-full items-center justify-between gap-2 border px-3 py-2 text-left text-sm focus:outline-hidden focus-visible:ring-0 focus-visible:ring-offset-0 focus-visible:outline-hidden ${triggerChrome !== 'minimal' ? 'dropdown-trigger' : ''} ${buttonClassName}`}
            style={{
              color: 'var(--text)',
              borderColor: triggerChrome === 'minimal' ? 'transparent' : 'var(--border-muted)',
              borderWidth: triggerChrome === 'minimal' ? 0 : undefined,
              borderRadius: isOpen
                ? triggerChrome === 'minimal'
                  ? '0'
                  : panelDirection === 'down'
                    ? '0.5rem 0.5rem 0 0'
                    : '0 0 0.5rem 0.5rem'
                : triggerChrome === 'minimal'
                  ? '0'
                  : '0.5rem',
            }}
          >
            <span className="min-w-0 flex-1 truncate">
              {summary ?? <span className="opacity-60">Select an option</span>}
            </span>
            <svg
              className={`h-4 w-4 shrink-0 transition-transform ${isOpen ? 'rotate-180' : ''}`}
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
            className={`absolute ${
              panelDirection === 'down'
                ? renderTrigger
                  ? 'mt-2'
                  : ''
                : renderTrigger
                  ? 'bottom-full mb-2'
                  : 'bottom-full'
            } z-20 border ${panelDirection === 'down' ? 'shadow-lg' : ''} ${panelClassName || widthClassName}`}
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
              // Viewport-clamped horizontal position (falls back to align until computed)
              left: nonPortalLeft !== null ? nonPortalLeft : align === 'right' ? undefined : 0,
              right: nonPortalLeft !== null ? undefined : align === 'right' ? 0 : undefined,
            }}
          >
            {renderCaret(panelDirection, nonPortalCaretLeft)}
            <div className={noScrollLimit ? '' : 'max-h-64 overflow-auto'}>
              {children({ close })}
            </div>
          </div>
        )}
      </div>
      {isOpen &&
        usePortal &&
        createPortal(
          <div
            ref={panelRef}
            className={`fixed inline-block border shadow-xl ${panelClassName || `z-[9999] ${widthClassName}`}`}
            style={{
              background: 'var(--bg)',
              borderColor: 'var(--border-muted)',
              borderRadius: '0.5rem',
              top: portalPosition?.top ?? 0,
              left: portalPosition?.left ?? 0,
              width: 'fit-content',
              maxWidth: 'min(90vw, 28rem)',
            }}
          >
            {portalPosition &&
              renderCaret(
                portalPosition.top > (triggerRef.current?.getBoundingClientRect().bottom ?? 0)
                  ? 'down'
                  : 'up',
                portalPosition.caretLeft,
              )}
            <div className={noScrollLimit ? '' : 'max-h-64 overflow-auto'}>
              {children({ close })}
            </div>
          </div>,
          document.body,
        )}
    </div>
  );
};
