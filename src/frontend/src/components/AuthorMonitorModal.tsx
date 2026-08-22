import { useCallback, useEffect, useRef, useState } from 'react';

import { getSelfUserEditContext, updateSelfUser } from '../services/api';
import {
  createMonitoredEntity,
  fsListDirectories,
  MonitoredEntity,
} from '../services/monitoredApi';
import { joinPath, normalizeAbsolutePath, stripTrailingAuthorName } from '../utils/monitoredPaths';
import { FolderBrowserModal } from './FolderBrowserModal';

// ---------------------------------------------------------------------------
// Helpers (kept local so the component stays self-contained)
// ---------------------------------------------------------------------------

const normalizeAuthor = (value: string): string => value.split(/\s+/).join(' ').trim();

const deriveRootFromAuthorDir = (authorDir: string): string => {
  const normalized = normalizeAbsolutePath(authorDir);
  if (!normalized || !normalized.startsWith('/')) return '';
  const idx = normalized.lastIndexOf('/');
  if (idx <= 0) return '';
  return normalized.slice(0, idx);
};

const splitPathForSuggest = (raw: string): { parent: string | null; prefix: string } => {
  const value = raw || '';
  if (!value.startsWith('/')) {
    return { parent: null, prefix: '' };
  }
  const lastSlash = value.lastIndexOf('/');
  if (lastSlash <= 0) {
    return { parent: '/', prefix: value.slice(1) };
  }
  const parent = value.slice(0, lastSlash) || '/';
  const prefix = value.slice(lastSlash + 1);
  return { parent, prefix };
};

type MonitorMode = 'none' | 'all' | 'missing' | 'upcoming';

const EMPTY_SUGGEST_STATE: {
  kind: 'ebook' | 'audiobook' | null;
  open: boolean;
  loading: boolean;
  parent: string | null;
  entries: { name: string; path: string }[];
  error: string | null;
} = {
  kind: null,
  open: false,
  loading: false,
  parent: null,
  entries: [],
  error: null,
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface AuthorMonitorTarget {
  name: string;
  provider?: string | null;
  provider_id?: string | null;
  photo_url?: string | null;
  books_count?: number | null;
}

export interface AuthorMonitorModalProps {
  /**
   * Author to monitor; `null` renders nothing (the component stays mounted).
   * Hold this in caller state so the identity is stable while open — the form
   * resets to defaults whenever a new object arrives.
   */
  author: AuthorMonitorTarget | null;
  onClose: () => void;
  /** Called with the created entity right before `onClose()`. */
  onMonitored: (entity: MonitoredEntity) => void;
  /** Show the public/private visibility toggle (multi-user installs only). */
  authRequired?: boolean;
  overlayZIndex?: number;
}

export const AuthorMonitorModal = ({
  author,
  onClose,
  onMonitored,
  authRequired = false,
  overlayZIndex = 1200,
}: AuthorMonitorModalProps) => {
  const [ebookAuthorDir, setEbookAuthorDir] = useState('');
  const [audiobookAuthorDir, setAudiobookAuthorDir] = useState('');
  const [monitorEbookMode, setMonitorEbookMode] = useState<MonitorMode>('none');
  const [monitorAudiobookMode, setMonitorAudiobookMode] = useState<MonitorMode>('none');
  const [visibility, setVisibility] = useState<'public' | 'private'>('public');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [rootsError, setRootsError] = useState<string | null>(null);
  // Identifies one open-dialog "session". Bumped whenever the dialog opens on a
  // new author target, so an in-flight create from an earlier session can never
  // close, error, or un-disable a later one.
  const sessionRef = useRef(0);
  // Whether the user has edited either path this session. Late-arriving root
  // suggestions must never overwrite an actively edited form.
  const pathsDirtyRef = useRef(false);

  const [ebookRoots, setEbookRoots] = useState<string[]>([]);
  const [audiobookRoots, setAudiobookRoots] = useState<string[]>([]);

  const [folderBrowserState, setFolderBrowserState] = useState<{
    open: boolean;
    kind: 'ebook' | 'audiobook' | null;
    initialPath: string | null;
  }>({ open: false, kind: null, initialPath: null });

  const [pathSuggestState, setPathSuggestState] = useState(EMPTY_SUGGEST_STATE);

  // Load root folder suggestions from user settings, falling back to system destinations.
  useEffect(() => {
    let alive = true;

    const loadRoots = async () => {
      setRootsError(null);
      try {
        const ctx = await getSelfUserEditContext();
        const overrides = ctx?.deliveryPreferences?.userOverrides ?? {};
        const ebook = overrides.MONITORED_EBOOK_ROOTS;
        const audio = overrides.MONITORED_AUDIOBOOK_ROOTS;
        let ebookArr = Array.isArray(ebook)
          ? ebook.filter((v): v is string => typeof v === 'string' && Boolean(v.trim()))
          : [];
        let audioArr = Array.isArray(audio)
          ? audio.filter((v): v is string => typeof v === 'string' && Boolean(v.trim()))
          : [];

        // Fall back to system-configured root folders (DESTINATION / DESTINATION_AUDIOBOOK)
        // when no user-specific roots exist yet, so the first monitor gets auto-populated paths.
        if (ebookArr.length === 0 || audioArr.length === 0) {
          try {
            const fsRoots = await fsListDirectories();
            const systemRoots = (fsRoots.directories || []).map((d) => d.path).filter(Boolean);
            if (ebookArr.length === 0 && systemRoots.length > 0) {
              ebookArr = [systemRoots[0]];
            }
            if (audioArr.length === 0 && systemRoots.length > 1) {
              audioArr = [systemRoots[1]];
            } else if (audioArr.length === 0 && systemRoots.length > 0) {
              audioArr = [systemRoots[0]];
            }
          } catch {
            // best-effort
          }
        }

        if (!alive) return;
        setEbookRoots(ebookArr);
        setAudiobookRoots(audioArr);
      } catch (e) {
        if (!alive) return;
        const message = e instanceof Error ? e.message : 'Failed to load folder suggestions';
        setRootsError(message);
        setEbookRoots([]);
        setAudiobookRoots([]);
      }
    };

    void loadRoots();
    return () => {
      alive = false;
    };
  }, []);

  const authorName = author ? normalizeAuthor(author.name) : '';

  // Roots are read through a ref here so the reset below can be keyed on the
  // author identity alone — re-running it when roots load would wipe an
  // actively edited form.
  const rootsRef = useRef<{ ebook: string[]; audiobook: string[] }>({ ebook: [], audiobook: [] });
  rootsRef.current = { ebook: ebookRoots, audiobook: audiobookRoots };

  // Reset to defaults whenever the dialog is opened. Keyed on the object, not
  // the name, so reopening the same author still starts from a clean form.
  useEffect(() => {
    if (!author) return;
    const name = normalizeAuthor(author.name);
    if (!name) return;
    sessionRef.current += 1;
    pathsDirtyRef.current = false;
    const roots = rootsRef.current;
    setEbookAuthorDir(roots.ebook.length > 0 ? joinPath(roots.ebook[0], name) : '');
    setAudiobookAuthorDir(roots.audiobook.length > 0 ? joinPath(roots.audiobook[0], name) : '');
    setMonitorEbookMode('none');
    setMonitorAudiobookMode('none');
    setVisibility('public');
    setError(null);
    setSaving(false);
    setPathSuggestState(EMPTY_SUGGEST_STATE);
    setFolderBrowserState({ open: false, kind: null, initialPath: null });
  }, [author]);

  // Roots arriving after the dialog opened: refresh the path prefill, but only
  // while the user hasn't touched the paths yet.
  useEffect(() => {
    if (!author || pathsDirtyRef.current) return;
    const name = normalizeAuthor(author.name);
    if (!name) return;
    if (ebookRoots.length > 0) {
      setEbookAuthorDir(joinPath(ebookRoots[0], name));
    }
    if (audiobookRoots.length > 0) {
      setAudiobookAuthorDir(joinPath(audiobookRoots[0], name));
    }
  }, [ebookRoots, audiobookRoots, author]);

  // Path setters used by user-driven edits (typing, suggestions, Browse) — they
  // mark the form dirty so late root arrival stops rewriting it.
  const editEbookAuthorDir = useCallback((value: string) => {
    pathsDirtyRef.current = true;
    setEbookAuthorDir(value);
  }, []);
  const editAudiobookAuthorDir = useCallback((value: string) => {
    pathsDirtyRef.current = true;
    setAudiobookAuthorDir(value);
  }, []);

  const refreshPathSuggestions = useCallback(
    async (kind: 'ebook' | 'audiobook', rawValue: string) => {
      const { parent, prefix } = splitPathForSuggest(rawValue);
      if (!parent) {
        setPathSuggestState((prev) => ({
          ...prev,
          kind,
          open: false,
          loading: false,
          parent: null,
          entries: [],
          error: null,
        }));
        return;
      }

      setPathSuggestState((prev) => ({
        ...prev,
        kind,
        open: true,
        loading: true,
        parent,
        entries: [],
        error: null,
      }));
      try {
        const res = await fsListDirectories(parent);
        const entries = (res.directories || [])
          .filter((d) => !prefix || d.name.toLowerCase().startsWith(prefix.toLowerCase()))
          .slice(0, 12);
        setPathSuggestState((prev) => ({
          ...prev,
          kind,
          open: true,
          loading: false,
          parent,
          entries,
          error: null,
        }));
      } catch (e) {
        const message = e instanceof Error ? e.message : 'Failed to list folders';
        setPathSuggestState((prev) => ({
          ...prev,
          kind,
          open: true,
          loading: false,
          parent,
          entries: [],
          error: message,
        }));
      }
    },
    [],
  );

  const persistLearnedRoots = useCallback(
    async (nextEbookRoot: string, nextAudiobookRoot: string) => {
      const ebookRoot = normalizeAbsolutePath(nextEbookRoot);
      const audioRoot = normalizeAbsolutePath(nextAudiobookRoot);

      if (!ebookRoot && !audioRoot) {
        return;
      }

      const nextSettings: Record<string, unknown> = {};

      if (ebookRoot) {
        const unique = Array.from(new Set([ebookRoot, ...ebookRoots].filter(Boolean)));
        nextSettings.MONITORED_EBOOK_ROOTS = unique;
        setEbookRoots(unique);
      }

      if (audioRoot) {
        const unique = Array.from(new Set([audioRoot, ...audiobookRoots].filter(Boolean)));
        nextSettings.MONITORED_AUDIOBOOK_ROOTS = unique;
        setAudiobookRoots(unique);
      }

      try {
        await updateSelfUser({ settings: nextSettings });
      } catch {
        // Best-effort persistence; ignore.
      }
    },
    [ebookRoots, audiobookRoots],
  );

  const handleConfirm = useCallback(async () => {
    if (!author || saving) return;

    const normalized = normalizeAuthor(author.name);
    if (!normalized) return;

    const finalEbookDir = normalizeAbsolutePath(ebookAuthorDir);
    const finalAudiobookDir = normalizeAbsolutePath(audiobookAuthorDir);

    if (!finalEbookDir && !finalAudiobookDir) {
      setError('Please set an Ebook folder or Audiobook folder.');
      return;
    }

    // Snapshot the session so a create that settles after the dialog moved on
    // (closed and reopened for another author) can't close or error that one.
    const session = sessionRef.current;
    setSaving(true);
    setError(null);
    try {
      const created = await createMonitoredEntity({
        kind: 'author',
        name: normalized,
        provider: author.provider || undefined,
        provider_id: author.provider_id || undefined,
        settings: {
          photo_url: author.photo_url ?? undefined,
          books_count: author.books_count ?? undefined,
          ebook_author_dir: finalEbookDir || undefined,
          audiobook_author_dir: finalAudiobookDir || undefined,
          monitor_ebook_mode: monitorEbookMode,
          monitor_audiobook_mode: monitorAudiobookMode,
        },
        visibility,
      });

      const learnedEbookRoot = finalEbookDir ? deriveRootFromAuthorDir(finalEbookDir) : '';
      const learnedAudioRoot = finalAudiobookDir ? deriveRootFromAuthorDir(finalAudiobookDir) : '';
      void persistLearnedRoots(learnedEbookRoot, learnedAudioRoot);

      // The entity was created server-side, so the page must learn about it even
      // if this dialog session is stale — but only the live session may close.
      onMonitored(created);
      if (session === sessionRef.current) {
        onClose();
      }
    } catch (e) {
      if (session === sessionRef.current) {
        const message = e instanceof Error ? e.message : 'Failed to monitor author';
        setError(message);
      }
    } finally {
      if (session === sessionRef.current) {
        setSaving(false);
      }
    }
  }, [
    author,
    saving,
    ebookAuthorDir,
    audiobookAuthorDir,
    monitorEbookMode,
    monitorAudiobookMode,
    visibility,
    persistLearnedRoots,
    onMonitored,
    onClose,
  ]);

  if (!author) return null;

  const renderPathInput = (
    kind: 'ebook' | 'audiobook',
    value: string,
    setValue: (next: string) => void,
    placeholder: string,
  ) => {
    const rootValue = stripTrailingAuthorName(value, authorName);
    return (
      <>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setFolderBrowserState({
                open: true,
                kind,
                initialPath: rootValue || null,
              });
            }}
            className="rounded-full bg-white/70 px-3 py-1.5 text-xs font-medium text-gray-900 hover:bg-white dark:bg-white/10 dark:text-gray-100 dark:hover:bg-white/20"
          >
            Browse
          </button>
          <div className="truncate text-xs text-gray-500 dark:text-gray-400">
            Type or browse to set the root author folder.
          </div>
        </div>
        <div className="relative">
          <div className="flex items-stretch overflow-hidden rounded-xl border border-black/10 bg-white/80 dark:border-white/10 dark:bg-white/10">
            <input
              value={value}
              onChange={(e) => {
                setValue(e.target.value);
                void refreshPathSuggestions(kind, e.target.value);
              }}
              onFocus={() => void refreshPathSuggestions(kind, value)}
              onBlur={() => {
                window.setTimeout(() => {
                  setPathSuggestState((prev) => ({ ...prev, open: false }));
                }, 150);
              }}
              placeholder={placeholder}
              className="min-w-0 flex-1 bg-transparent px-3 py-2 text-sm outline-none"
            />
          </div>
          {pathSuggestState.open && pathSuggestState.kind === kind ? (
            <div className="absolute z-10 mt-1 w-full overflow-hidden rounded-xl border border-[var(--border-muted)] bg-[var(--bg)] shadow-lg">
              {pathSuggestState.loading ? (
                <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">Loading…</div>
              ) : pathSuggestState.error ? (
                <div className="px-3 py-2 text-xs text-red-500">{pathSuggestState.error}</div>
              ) : pathSuggestState.entries.length === 0 ? (
                <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">No folders</div>
              ) : (
                <div className="max-h-56 overflow-auto">
                  {pathSuggestState.entries.map((entry) => (
                    <button
                      key={entry.path}
                      type="button"
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => {
                        setValue(entry.path);
                        setPathSuggestState((prev) => ({ ...prev, open: false }));
                      }}
                      className="w-full px-3 py-2 text-left text-sm hover:bg-black/5 dark:hover:bg-white/10"
                    >
                      {entry.path}
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : null}
        </div>
      </>
    );
  };

  return (
    <>
      <div
        className="modal-overlay active sm:px-6 sm:py-6"
        style={{ zIndex: overlayZIndex }}
        onClick={(e) => {
          if (e.target === e.currentTarget) {
            onClose();
          }
        }}
      >
        <div
          className="details-container settings-modal-enter h-auto w-full max-w-lg"
          role="dialog"
          aria-modal="true"
          aria-label="Monitor author folders"
        >
          <div className="overflow-hidden rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] text-[var(--text)] shadow-2xl sm:bg-[var(--bg-soft)]">
            <header className="flex items-start justify-between gap-3 border-b border-[var(--border-muted)] px-5 py-4">
              <div className="min-w-0">
                <div className="text-xs tracking-wide text-gray-500 uppercase dark:text-gray-400">
                  Monitor author
                </div>
                <div className="mt-1 truncate text-base font-semibold">{authorName}</div>
              </div>
              <button
                type="button"
                onClick={onClose}
                className="hover-action rounded-full p-2 text-gray-500 transition-colors hover:text-gray-900 dark:hover:text-gray-100"
                aria-label="Close"
              >
                <svg
                  className="h-5 w-5"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                  strokeWidth={1.5}
                >
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </header>

            <div className="space-y-4 px-5 py-4">
              {error || rootsError ? (
                <div className="flex flex-col gap-1">
                  {error ? <div className="text-sm text-red-500">{error}</div> : null}
                  {rootsError ? <div className="text-sm text-red-500">{rootsError}</div> : null}
                </div>
              ) : null}

              <div className="space-y-2">
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  Ebook folder
                </div>
                <div className="space-y-2">
                  {renderPathInput(
                    'ebook',
                    ebookAuthorDir,
                    editEbookAuthorDir,
                    authorName ? `/books/ebooks/fiction/${authorName}` : '/books/ebooks',
                  )}
                </div>
              </div>

              <div className="space-y-2">
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  Audiobook folder
                </div>
                <div className="space-y-2">
                  {renderPathInput(
                    'audiobook',
                    audiobookAuthorDir,
                    editAudiobookAuthorDir,
                    authorName ? `/books/audiobooks/Fiction/${authorName}` : '/books/audiobooks',
                  )}
                </div>
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <label className="space-y-1">
                  <div className="text-xs font-medium text-gray-700 dark:text-gray-300">
                    eBook monitoring
                  </div>
                  <select
                    value={monitorEbookMode}
                    onChange={(e) => {
                      setMonitorEbookMode(e.target.value as MonitorMode);
                    }}
                    className="w-full rounded-xl border border-black/10 bg-white/80 px-3 py-2 text-sm dark:border-white/10 dark:bg-white/10"
                  >
                    <option value="all">Monitor all books</option>
                    <option value="missing">Monitor missing only</option>
                    <option value="upcoming">Monitor upcoming only</option>
                    <option value="none">No monitoring</option>
                  </select>
                </label>

                <label className="space-y-1">
                  <div className="text-xs font-medium text-gray-700 dark:text-gray-300">
                    Audiobook monitoring
                  </div>
                  <select
                    value={monitorAudiobookMode}
                    onChange={(e) => {
                      setMonitorAudiobookMode(e.target.value as MonitorMode);
                    }}
                    className="w-full rounded-xl border border-black/10 bg-white/80 px-3 py-2 text-sm dark:border-white/10 dark:bg-white/10"
                  >
                    <option value="all">Monitor all books</option>
                    <option value="missing">Monitor missing only</option>
                    <option value="upcoming">Monitor upcoming only</option>
                    <option value="none">No monitoring</option>
                  </select>
                </label>
              </div>

              {authRequired && (
                <label className="mt-1 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() =>
                      setVisibility((prev) => (prev === 'public' ? 'private' : 'public'))
                    }
                    className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors ${
                      visibility === 'public' ? 'bg-emerald-500' : 'bg-gray-400 dark:bg-gray-600'
                    }`}
                  >
                    <span
                      className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow transition-transform ${
                        visibility === 'public' ? 'translate-x-4' : 'translate-x-0'
                      }`}
                    />
                  </button>
                  <span className="text-xs text-gray-600 dark:text-gray-300">
                    {visibility === 'public' ? 'Shared with all users' : 'Private (only you)'}
                  </span>
                </label>
              )}
            </div>

            <footer className="flex items-center justify-end gap-2 border-t border-[var(--border-muted)] bg-[var(--bg)] px-5 py-4 sm:bg-[var(--bg-soft)]">
              <button
                type="button"
                onClick={onClose}
                className="rounded-full bg-white/70 px-4 py-2 font-medium text-gray-900 hover:bg-white dark:bg-white/10 dark:text-gray-100 dark:hover:bg-white/20"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleConfirm()}
                disabled={saving}
                className="rounded-full bg-emerald-600 px-4 py-2 font-medium text-white hover:bg-emerald-700 disabled:opacity-60"
              >
                Monitor
              </button>
            </footer>
          </div>
        </div>
      </div>

      <FolderBrowserModal
        open={folderBrowserState.open}
        title={
          folderBrowserState.kind === 'audiobook'
            ? 'Select audiobook folder'
            : 'Select ebook folder'
        }
        initialPath={folderBrowserState.initialPath}
        overlayZIndex={overlayZIndex + 100}
        quickRoots={folderBrowserState.kind === 'audiobook' ? audiobookRoots : ebookRoots}
        onClose={() => setFolderBrowserState({ open: false, kind: null, initialPath: null })}
        onSelect={(path) => {
          const normalizedPath = normalizeAbsolutePath(path);
          // If the selected folder already ends with the author name, use it directly
          const alreadyHasAuthor = authorName && normalizedPath.endsWith(`/${authorName}`);
          const suggested = alreadyHasAuthor
            ? normalizedPath
            : authorName
              ? joinPath(path, authorName)
              : path;
          if (folderBrowserState.kind === 'audiobook') {
            editAudiobookAuthorDir(suggested);
          } else {
            editEbookAuthorDir(suggested);
          }
        }}
      />
    </>
  );
};
