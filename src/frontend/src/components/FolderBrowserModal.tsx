import { useCallback, useEffect, useMemo, useState } from 'react';
import { fsListDirectories, FsDirectoryEntry } from '../services/api';

interface FolderBrowserModalProps {
  open: boolean;
  title: string;
  initialPath?: string | null;
  onClose: () => void;
  onSelect: (path: string) => void;
  overlayZIndex?: number;
}

export const FolderBrowserModal = ({ open, title, initialPath, onClose, onSelect, overlayZIndex }: FolderBrowserModalProps) => {
  const [currentPath, setCurrentPath] = useState<string | null>(initialPath ?? null);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [directories, setDirectories] = useState<FsDirectoryEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    setCurrentPath(initialPath ?? null);
  }, [open, initialPath]);

  useEffect(() => {
    if (!open) {
      return;
    }

    let alive = true;
    const load = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const resp = await fsListDirectories(currentPath);
        if (!alive) return;
        setParentPath(resp.parent);
        setDirectories(resp.directories || []);
      } catch (e) {
        if (!alive) return;
        const message = e instanceof Error ? e.message : 'Failed to list directories';
        setError(message);
        setDirectories([]);
        setParentPath(null);
      } finally {
        if (alive) setIsLoading(false);
      }
    };

    void load();
    return () => {
      alive = false;
    };
  }, [open, currentPath]);

  const breadcrumb = useMemo(() => {
    const path = currentPath;
    if (!path) return [];
    const parts = path.split('/').filter(Boolean);
    const crumbs: Array<{ label: string; path: string }> = [];
    let acc = '';
    for (const part of parts) {
      acc += `/${part}`;
      crumbs.push({ label: part, path: acc });
    }
    return crumbs;
  }, [currentPath]);

  const handleSelectCurrent = useCallback(() => {
    if (!currentPath) return;
    onSelect(currentPath);
    onClose();
  }, [currentPath, onClose, onSelect]);

  if (!open) return null;

  return (
    <div
      className="modal-overlay active sm:px-6 sm:py-6"
      style={typeof overlayZIndex === 'number' ? { zIndex: overlayZIndex } : undefined}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="details-container w-full max-w-2xl h-auto settings-modal-enter" role="dialog" aria-modal="true" aria-label={title}>
        <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-2xl overflow-hidden">
          <header className="flex items-start justify-between gap-3 border-b border-[var(--border-muted)] px-5 py-4">
            <div className="min-w-0">
              <div className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Browse</div>
              <div className="mt-1 text-base font-semibold truncate">{title}</div>
              <div className="mt-1 text-xs text-gray-500 dark:text-gray-400 truncate">
                {currentPath || 'Select a root folder'}
              </div>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100"
              aria-label="Close"
            >
              <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </header>

          <div className="px-5 py-4 space-y-3">
            <div className="flex flex-wrap items-center gap-1 text-xs">
              <button
                type="button"
                onClick={() => setCurrentPath(null)}
                className="px-2 py-1 rounded-lg border border-[var(--border-muted)] hover:bg-[var(--hover-surface)]"
              >
                Roots
              </button>
              {breadcrumb.map((c) => (
                <div key={c.path} className="flex items-center gap-1">
                  <span className="opacity-60">/</span>
                  <button
                    type="button"
                    onClick={() => setCurrentPath(c.path)}
                    className="px-2 py-1 rounded-lg border border-[var(--border-muted)] hover:bg-[var(--hover-surface)]"
                  >
                    {c.label}
                  </button>
                </div>
              ))}
            </div>

            <div className="flex items-center justify-between gap-2">
              <button
                type="button"
                onClick={() => {
                  if (parentPath) setCurrentPath(parentPath);
                  else setCurrentPath(null);
                }}
                disabled={!parentPath && currentPath === null}
                className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100 disabled:opacity-50"
              >
                Up
              </button>
              <button
                type="button"
                onClick={handleSelectCurrent}
                disabled={!currentPath}
                className="px-3 py-1.5 rounded-full bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-medium disabled:opacity-50"
              >
                Select this folder
              </button>
            </div>

            {error ? <div className="text-sm text-red-500">{error}</div> : null}

            <div className="rounded-xl border border-[var(--border-muted)] overflow-hidden">
              <div className="max-h-[320px] overflow-y-auto divide-y divide-black/10 dark:divide-white/10">
                {isLoading ? (
                  <div className="px-3 py-3 text-sm text-gray-500 dark:text-gray-400">Loading…</div>
                ) : directories.length === 0 ? (
                  <div className="px-3 py-3 text-sm text-gray-500 dark:text-gray-400">No folders found.</div>
                ) : (
                  directories.map((dir) => (
                    <button
                      key={dir.path}
                      type="button"
                      onClick={() => setCurrentPath(dir.path)}
                      className="w-full flex items-center justify-between gap-3 px-3 py-2 text-left hover:bg-black/5 dark:hover:bg-white/5"
                    >
                      <div className="min-w-0">
                        <div className="text-sm font-medium truncate">{dir.name}</div>
                        <div className="text-xs text-gray-500 dark:text-gray-400 truncate">{dir.path}</div>
                      </div>
                      <svg className="w-4 h-4 opacity-60 flex-shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.8}>
                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                      </svg>
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>

          <footer className="flex items-center justify-end gap-2 border-t border-[var(--border-muted)] px-5 py-4 bg-[var(--bg)] sm:bg-[var(--bg-soft)]">
            <button
              type="button"
              onClick={onClose}
              className="px-4 py-2 rounded-full bg-white/70 hover:bg-white text-gray-900 font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
            >
              Close
            </button>
          </footer>
        </div>
      </div>
    </div>
  );
};
