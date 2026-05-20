import { useCallback, useEffect, useState } from 'react';
import { createRoot, Root } from 'react-dom/client';

export interface ConfirmDialogOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
}

interface ConfirmDialogProps extends ConfirmDialogOptions {
  onResolve: (value: boolean) => void;
}

const ConfirmDialog = ({
  title = 'Confirm',
  message,
  confirmLabel = 'OK',
  cancelLabel = 'Cancel',
  destructive = false,
  onResolve,
}: ConfirmDialogProps) => {
  const [isClosing, setIsClosing] = useState(false);

  const close = useCallback((value: boolean) => {
    setIsClosing(true);
    setTimeout(() => onResolve(value), 150);
  }, [onResolve]);

  useEffect(() => {
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = previousOverflow;
    };
  }, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close(false);
      else if (event.key === 'Enter') close(true);
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [close]);

  const titleId = 'confirm-dialog-title';
  const confirmClass = destructive
    ? 'bg-red-600 hover:bg-red-700 text-white'
    : 'bg-sky-600 hover:bg-sky-700 text-white';

  return (
    <div
      className="fixed inset-0 z-[2200] flex items-center justify-center p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby={titleId}
    >
      <div
        className={`absolute inset-0 bg-black/50 backdrop-blur-xs transition-opacity duration-150 ${isClosing ? 'opacity-0' : 'opacity-100'}`}
        onClick={() => close(false)}
      />
      <div
        className={`relative w-full max-w-md rounded-xl border border-(--border-muted) shadow-2xl ${isClosing ? 'settings-modal-exit' : 'settings-modal-enter'}`}
        style={{ background: 'var(--bg)' }}
      >
        <header className="border-b border-(--border-muted) px-5 py-3">
          <h3 id={titleId} className="text-base font-semibold">{title}</h3>
        </header>
        <div className="px-5 py-4 text-sm whitespace-pre-line">{message}</div>
        <footer className="flex items-center justify-end gap-2 border-t border-(--border-muted) px-5 py-3">
          <button
            type="button"
            onClick={() => close(false)}
            className="px-4 py-2 rounded-lg text-sm font-medium bg-(--bg-soft) border border-(--border-muted) hover:bg-(--hover-surface) transition-colors"
            autoFocus
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            onClick={() => close(true)}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition-colors ${confirmClass}`}
          >
            {confirmLabel}
          </button>
        </footer>
      </div>
    </div>
  );
};

export const showConfirm = (options: ConfirmDialogOptions): Promise<boolean> => {
  return new Promise((resolve) => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    let root: Root | null = createRoot(container);

    const cleanup = (value: boolean) => {
      if (root) {
        root.unmount();
        root = null;
      }
      if (container.parentNode) container.parentNode.removeChild(container);
      resolve(value);
    };

    root.render(<ConfirmDialog {...options} onResolve={cleanup} />);
  });
};
