import { ReactNode, useEffect, useRef } from 'react';
import { createPortal } from 'react-dom';

interface SlideSheetProps {
  isOpen: boolean;
  onClose: () => void;
  children: ReactNode;
  /** Tailwind width class, default 'w-72 sm:w-80' */
  widthClassName?: string;
  /** Accessible label for the dialog */
  label?: string;
}

export const SlideSheet = ({
  isOpen,
  onClose,
  children,
  widthClassName = 'w-72 sm:w-80',
  label = 'Navigation',
}: SlideSheetProps) => {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    document.addEventListener('keydown', handleKeyDown);

    // Prevent body scroll while open
    const prev = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', handleKeyDown);
      document.body.style.overflow = prev;
    };
  }, [isOpen, onClose]);

  // Focus the panel when it opens
  useEffect(() => {
    if (isOpen) {
      panelRef.current?.focus();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-[9000] flex justify-end" aria-modal="true" role="dialog" aria-label={label}>
      {/* Backdrop */}
      <div
        className="absolute inset-0 bg-black/40 backdrop-blur-sm"
        onClick={onClose}
        aria-hidden="true"
      />
      {/* Panel */}
      <div
        ref={panelRef}
        tabIndex={-1}
        className={`relative ${widthClassName} h-full flex flex-col outline-none shadow-2xl animate-slide-in-right`}
        style={{
          background: 'var(--bg)',
          borderLeft: '1px solid var(--border-muted)',
        }}
      >
        {children}
      </div>
    </div>,
    document.body
  );
};
