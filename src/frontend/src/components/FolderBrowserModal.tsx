import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { fsListDirectories, fsMkdir, FsDirectoryEntry } from '../services/monitoredApi';

interface FolderBrowserModalProps {
  open: boolean;
  title: string;
  initialPath?: string | null;
  onClose: () => void;
  onSelect: (path: string) => void;
  overlayZIndex?: number;
  /** Known root folders shown as quick-jump buttons (e.g. previously used roots) */
  quickRoots?: string[];
}

export const FolderBrowserModal = ({ open, title, initialPath, onClose, onSelect, overlayZIndex, quickRoots }: FolderBrowserModalProps) => {
  const [currentPath, setCurrentPath] = useState<string | null>(initialPath ?? null);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [directories, setDirectories] = useState<FsDirectoryEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [newFolderMode, setNewFolderMode] = useState(false);
  const [newFolderName, setNewFolderName] = useState('');
  const [newFolderError, setNewFolderError] = useState<string | null>(null);
  const [newFolderSaving, setNewFolderSaving] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const newFolderInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!open) {
      setNewFolderMode(false);
      setNewFolderName('');
      setNewFolderError(null);
      setSearchQuery('');
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

    setSearchQuery('');
    void load();
    return () => {
      alive = false;
    };
  }, [open, currentPath]);

  const filteredDirectories = useMemo(() => {
    if (!searchQuery.trim()) return directories;
    const q = searchQuery.trim().toLowerCase();
    return directories.filter((d) => d.name.toLowerCase().includes(q));
  }, [directories, searchQuery]);

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

  const handleCreateFolder = useCallback(async () => {
    if (!currentPath || !newFolderName.trim() || newFolderSaving) return;
    setNewFolderSaving(true);
    setNewFolderError(null);
    try {
      const result = await fsMkdir(currentPath, newFolderName.trim());
      setNewFolderMode(false);
      setNewFolderName('');
      setCurrentPath(result.path);
    } catch (e) {
      setNewFolderError(e instanceof Error ? e.message : 'Failed to create folder');
    } finally {
      setNewFolderSaving(false);
    }
  }, [currentPath, newFolderName, newFolderSaving]);

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
            {/* Quick root folder buttons */}
            {quickRoots && quickRoots.length > 0 ? (
              <div className="space-y-1.5">
                <div className="text-xs text-gray-500 dark:text-gray-400">Quick jump</div>
                <div className="flex flex-wrap gap-1.5">
                  {quickRoots.map((root) => {
                    const label = root.split('/').filter(Boolean).slice(-2).join('/');
                    const isActive = currentPath === root;
                    return (
                      <button
                        key={root}
                        type="button"
                        onClick={() => setCurrentPath(root)}
                        className={`px-2.5 py-1 rounded-lg text-xs font-medium transition-colors ${
                          isActive
                            ? 'bg-emerald-600 text-white'
                            : 'border border-[var(--border-muted)] hover:bg-[var(--hover-surface)]'
                        }`}
                        title={root}
                      >
                        {label || root}
                      </button>
                    );
                  })}
                </div>
              </div>
            ) : null}

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
              <div className="flex items-center gap-2">
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
                {currentPath ? (
                  <button
                    type="button"
                    onClick={() => {
                      setNewFolderMode(true);
                      setNewFolderName('');
                      setNewFolderError(null);
                      window.setTimeout(() => newFolderInputRef.current?.focus(), 50);
                    }}
                    className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                  >
                    + New folder
                  </button>
                ) : null}
              </div>
              <button
                type="button"
                onClick={handleSelectCurrent}
                disabled={!currentPath}
                className="px-3 py-1.5 rounded-full bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-medium disabled:opacity-50"
              >
                Select this folder
              </button>
            </div>

            {newFolderMode ? (
              <div className="flex items-center gap-2">
                <input
                  ref={newFolderInputRef}
                  value={newFolderName}
                  onChange={(e) => { setNewFolderName(e.target.value); setNewFolderError(null); }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') void handleCreateFolder();
                    if (e.key === 'Escape') { setNewFolderMode(false); setNewFolderName(''); setNewFolderError(null); }
                  }}
                  placeholder="New folder name"
                  className="flex-1 px-3 py-1.5 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm outline-none"
                  disabled={newFolderSaving}
                />
                <button
                  type="button"
                  onClick={() => void handleCreateFolder()}
                  disabled={!newFolderName.trim() || newFolderSaving}
                  className="px-3 py-1.5 rounded-full bg-emerald-600 hover:bg-emerald-700 text-white text-xs font-medium disabled:opacity-50"
                >
                  {newFolderSaving ? 'Creating…' : 'Create'}
                </button>
                <button
                  type="button"
                  onClick={() => { setNewFolderMode(false); setNewFolderName(''); setNewFolderError(null); }}
                  className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
                >
                  Cancel
                </button>
              </div>
            ) : null}
            {newFolderError ? <div className="text-xs text-red-500">{newFolderError}</div> : null}

            {error ? <div className="text-sm text-red-500">{error}</div> : null}

            {/* Search input — shown when there are enough folders to warrant filtering */}
            {!isLoading && directories.length > 5 ? (
              <div className="relative">
                <input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Filter folders…"
                  className="w-full px-3 py-1.5 pl-8 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm outline-none"
                />
                <svg className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
                </svg>
              </div>
            ) : null}

            <div className="rounded-xl border border-[var(--border-muted)] overflow-hidden">
              <div className="max-h-[320px] overflow-y-auto divide-y divide-black/10 dark:divide-white/10">
                {isLoading ? (
                  <div className="px-3 py-3 text-sm text-gray-500 dark:text-gray-400">Loading…</div>
                ) : filteredDirectories.length === 0 ? (
                  <div className="px-3 py-3 text-sm text-gray-500 dark:text-gray-400">
                    {searchQuery.trim() && directories.length > 0 ? `No folders matching "${searchQuery.trim()}"` : 'No folders found.'}
                  </div>
                ) : (
                  filteredDirectories.map((dir) => (
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
