import { useCallback, useEffect, useState } from 'react';
import { getSelfUserEditContext, updateSelfUser } from '../services/api';
import { createMonitoredEntity, fsListDirectories, MonitoredEntity } from '../services/monitoredApi';
import { Book } from '../types';
import { FolderBrowserModal } from './FolderBrowserModal';

// ---------------------------------------------------------------------------
// Helpers (duplicated from MonitoredPage to keep component self-contained)
// ---------------------------------------------------------------------------

const normalizeAuthor = (value: string): string =>
  value.split(/\s+/).join(' ').trim();

const extractPrimaryAuthorName = (value: string): string =>
  normalizeAuthor((value || '').split(',')[0] || '');

const joinPath = (root: string, authorName: string): string => {
  const r = (root || '').trim().replace(/\/+$/g, '');
  if (!r) return '';
  return `${r}/${authorName}`;
};

const normalizeAbsolutePath = (value: string): string =>
  (value || '').trim().replace(/\/+$/g, '');

const stripTrailingAuthorName = (fullPath: string, authorName: string): string => {
  const normalized = normalizeAbsolutePath(fullPath);
  const a = (authorName || '').trim();
  if (!normalized || !a) return normalized;
  const suffix = `/${a}`;
  if (normalized.endsWith(suffix)) {
    return normalized.slice(0, -suffix.length) || '/';
  }
  return normalized;
};

const splitPathForSuggest = (raw: string): { parent: string | null; prefix: string } => {
  const value = raw || '';
  if (!value.startsWith('/')) return { parent: null, prefix: '' };
  const lastSlash = value.lastIndexOf('/');
  if (lastSlash <= 0) return { parent: '/', prefix: value.slice(1) };
  return { parent: value.slice(0, lastSlash) || '/', prefix: value.slice(lastSlash + 1) };
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export interface BookMonitorModalProps {
  book: Book | null;
  onClose: () => void;
  onMonitored: (entity: MonitoredEntity) => void;
}

export const BookMonitorModal = ({ book, onClose, onMonitored }: BookMonitorModalProps) => {
  const [ebookAuthorDir, setEbookAuthorDir] = useState('');
  const [audiobookAuthorDir, setAudiobookAuthorDir] = useState('');
  const [monitorEbook, setMonitorEbook] = useState(true);
  const [monitorAudiobook, setMonitorAudiobook] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const [ebookRoots, setEbookRoots] = useState<string[]>([]);
  const [audiobookRoots, setAudiobookRoots] = useState<string[]>([]);

  const [folderBrowserState, setFolderBrowserState] = useState<{
    open: boolean;
    kind: 'ebook' | 'audiobook' | null;
    initialPath: string | null;
  }>({ open: false, kind: null, initialPath: null });

  const [pathSuggestState, setPathSuggestState] = useState<{
    kind: 'ebook' | 'audiobook' | null;
    open: boolean;
    loading: boolean;
    parent: string | null;
    entries: { name: string; path: string }[];
    error: string | null;
  }>({ kind: null, open: false, loading: false, parent: null, entries: [], error: null });

  // Load root folder suggestions from user settings, falling back to system destinations
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const ctx = await getSelfUserEditContext();
        if (!alive) return;
        const overrides = ctx?.deliveryPreferences?.userOverrides ?? {};
        const ebook = overrides.MONITORED_EBOOK_ROOTS;
        const audio = overrides.MONITORED_AUDIOBOOK_ROOTS;
        let ebookArr = Array.isArray(ebook) ? ebook.filter((v): v is string => typeof v === 'string' && Boolean(v.trim())) : [];
        let audioArr = Array.isArray(audio) ? audio.filter((v): v is string => typeof v === 'string' && Boolean(v.trim())) : [];

        if (ebookArr.length === 0 || audioArr.length === 0) {
          try {
            const fsRoots = await fsListDirectories();
            const systemRoots = (fsRoots.directories || []).map((d) => d.path).filter(Boolean);
            if (ebookArr.length === 0 && systemRoots.length > 0) ebookArr = [systemRoots[0]];
            if (audioArr.length === 0 && systemRoots.length > 1) audioArr = [systemRoots[1]];
            else if (audioArr.length === 0 && systemRoots.length > 0) audioArr = [systemRoots[0]];
          } catch {
            // best-effort
          }
        }

        if (!alive) return;
        setEbookRoots(ebookArr);
        setAudiobookRoots(audioArr);
      } catch {
        // best-effort
      }
    };
    void load();
    return () => { alive = false; };
  }, []);

  // Auto-populate paths when book + roots change
  useEffect(() => {
    if (!book) return;
    const authorName = extractPrimaryAuthorName(book.author || '') || normalizeAuthor(book.title || '') || 'Unknown';
    const ebook = ebookRoots.length > 0 ? joinPath(ebookRoots[0], authorName) : '';
    const audio = audiobookRoots.length > 0 ? joinPath(audiobookRoots[0], authorName) : '';
    setEbookAuthorDir(ebook);
    setAudiobookAuthorDir(audio);
    setError(null);
  }, [book, ebookRoots, audiobookRoots]);

  const refreshPathSuggestions = useCallback(async (kind: 'ebook' | 'audiobook', rawValue: string) => {
    const { parent, prefix } = splitPathForSuggest(rawValue);
    if (!parent) {
      setPathSuggestState((prev) => ({ ...prev, kind, open: false, loading: false, parent: null, entries: [], error: null }));
      return;
    }
    setPathSuggestState((prev) => ({ ...prev, kind, open: true, loading: true, parent, entries: [], error: null }));
    try {
      const res = await fsListDirectories(parent);
      const entries = (res.directories || [])
        .filter((d) => !prefix || d.name.toLowerCase().startsWith(prefix.toLowerCase()))
        .slice(0, 12);
      setPathSuggestState((prev) => ({ ...prev, kind, open: true, loading: false, parent, entries, error: null }));
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to list folders';
      setPathSuggestState((prev) => ({ ...prev, kind, open: true, loading: false, parent, entries: [], error: message }));
    }
  }, []);

  const persistLearnedRoots = useCallback(async (nextEbookRoot: string, nextAudiobookRoot: string) => {
    const ebookRoot = normalizeAbsolutePath(nextEbookRoot);
    const audioRoot = normalizeAbsolutePath(nextAudiobookRoot);
    if (!ebookRoot && !audioRoot) return;
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
      // best-effort
    }
  }, [ebookRoots, audiobookRoots]);

  const handleConfirm = useCallback(async () => {
    if (!book || saving) return;
    const provider = (book.provider || '').trim();
    const providerId = (book.provider_id || '').trim();
    if (!provider || !providerId) {
      setError('Book is missing provider metadata and cannot be monitored.');
      return;
    }
    if (!monitorEbook && !monitorAudiobook) {
      setError('Enable eBook, Audiobook, or both.');
      return;
    }
    const finalEbook = normalizeAbsolutePath(ebookAuthorDir);
    const finalAudio = normalizeAbsolutePath(audiobookAuthorDir);
    if (!finalEbook && !finalAudio) {
      setError('Please set an Ebook folder or Audiobook folder.');
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const created = await createMonitoredEntity({
        kind: 'book',
        name: (book.title || '').trim() || `${provider}:${providerId}`,
        provider,
        provider_id: providerId,
        settings: {
          photo_url: book.preview,
          book_title: book.title,
          book_author: book.author,
          book_source_url: book.source_url,
          ebook_author_dir: finalEbook || undefined,
          audiobook_author_dir: finalAudio || undefined,
          monitor_ebook: monitorEbook,
          monitor_audiobook: monitorAudiobook,
        },
      });
      const ebookRoot = finalEbook ? finalEbook.slice(0, finalEbook.lastIndexOf('/')) || '' : '';
      const audioRoot = finalAudio ? finalAudio.slice(0, finalAudio.lastIndexOf('/')) || '' : '';
      await persistLearnedRoots(ebookRoot, audioRoot);
      onMonitored(created);
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to monitor book');
    } finally {
      setSaving(false);
    }
  }, [book, saving, monitorEbook, monitorAudiobook, ebookAuthorDir, audiobookAuthorDir, persistLearnedRoots, onMonitored, onClose]);

  if (!book) return null;

  const authorName = extractPrimaryAuthorName(book.author || '');

  const renderPathInput = (
    kind: 'ebook' | 'audiobook',
    value: string,
    setValue: (v: string) => void,
    placeholder: string,
  ) => {
    const rootValue = stripTrailingAuthorName(value, authorName);
    const suffix = authorName ? `/${authorName}` : '';
    return (
      <>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setFolderBrowserState({ open: true, kind, initialPath: rootValue || null })}
            className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100"
          >
            Browse
          </button>
          <div className="text-xs text-gray-500 dark:text-gray-400 truncate">Type or browse to set the root author folder.</div>
        </div>
        <div className="relative">
          <div className="flex items-stretch rounded-xl border border-black/10 dark:border-white/10 overflow-hidden bg-white/80 dark:bg-white/10">
            <input
              value={rootValue}
              onChange={(e) => {
                const v = e.target.value;
                const nextFull = authorName ? joinPath(v, authorName) : v;
                setValue(nextFull);
                void refreshPathSuggestions(kind, v);
              }}
              onFocus={() => void refreshPathSuggestions(kind, rootValue)}
              onBlur={() => { window.setTimeout(() => setPathSuggestState((prev) => ({ ...prev, open: false })), 150); }}
              placeholder={placeholder}
              className="flex-1 min-w-0 px-3 py-2 text-sm bg-transparent outline-none"
            />
            {suffix ? (
              <div className="flex items-center px-2 text-sm text-gray-400 dark:text-gray-500 border-l border-black/10 dark:border-white/10 bg-black/5 dark:bg-white/5 select-none whitespace-nowrap">
                {suffix}
              </div>
            ) : null}
          </div>
          {pathSuggestState.open && pathSuggestState.kind === kind ? (
            <div className="absolute z-10 mt-1 w-full rounded-xl border border-[var(--border-muted)] bg-[var(--bg)] shadow-lg overflow-hidden">
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
                        const nextFull = authorName ? joinPath(entry.path, authorName) : entry.path;
                        setValue(nextFull);
                        setPathSuggestState((prev) => ({ ...prev, open: false }));
                      }}
                      className="w-full text-left px-3 py-2 text-sm hover:bg-black/5 dark:hover:bg-white/10"
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
        style={{ zIndex: 1200 }}
        onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
      >
        <div
          className="details-container w-full max-w-lg h-auto settings-modal-enter"
          role="dialog"
          aria-modal="true"
          aria-label="Monitor book"
        >
          <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-2xl overflow-hidden">
            <header className="flex items-start justify-between gap-3 border-b border-[var(--border-muted)] px-5 py-4">
              <div className="min-w-0">
                <div className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Monitor book</div>
                <div className="mt-1 text-base font-semibold truncate">{book.title || 'Unknown title'}</div>
                <div className="text-xs text-gray-500 dark:text-gray-400 truncate">{book.author || 'Unknown author'}</div>
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

            <div className="px-5 py-4 space-y-4">
              {error ? <div className="text-sm text-red-500">{error}</div> : null}

              <div className="flex items-center gap-4">
                <label className="inline-flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
                  <input type="checkbox" checked={monitorEbook} onChange={(e) => setMonitorEbook(e.target.checked)} className="accent-emerald-600" />
                  Monitor eBook
                </label>
                <label className="inline-flex items-center gap-2 text-sm font-medium text-gray-900 dark:text-gray-100">
                  <input type="checkbox" checked={monitorAudiobook} onChange={(e) => setMonitorAudiobook(e.target.checked)} className="accent-emerald-600" />
                  Monitor Audiobook
                </label>
              </div>

              <div className="space-y-2">
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Ebook folder</div>
                <div className="space-y-2">
                  {renderPathInput('ebook', ebookAuthorDir, setEbookAuthorDir, '/books/ebooks')}
                </div>
              </div>

              <div className="space-y-2">
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Audiobook folder</div>
                <div className="space-y-2">
                  {renderPathInput('audiobook', audiobookAuthorDir, setAudiobookAuthorDir, '/books/audiobooks')}
                </div>
              </div>
            </div>

            <footer className="flex items-center justify-end gap-2 border-t border-[var(--border-muted)] px-5 py-4 bg-[var(--bg)] sm:bg-[var(--bg-soft)]">
              <button
                type="button"
                onClick={onClose}
                disabled={saving}
                className="px-4 py-2 rounded-full bg-white/70 hover:bg-white text-gray-900 font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100 disabled:opacity-50"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={() => void handleConfirm()}
                disabled={saving}
                className="px-4 py-2 rounded-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white font-medium"
              >
                {saving ? 'Monitoring…' : 'Monitor'}
              </button>
            </footer>
          </div>
        </div>
      </div>

      <FolderBrowserModal
        open={folderBrowserState.open}
        title={folderBrowserState.kind === 'audiobook' ? 'Select audiobook folder' : 'Select ebook folder'}
        initialPath={folderBrowserState.initialPath}
        overlayZIndex={1300}
        onClose={() => setFolderBrowserState({ open: false, kind: null, initialPath: null })}
        onSelect={(path) => {
          if (folderBrowserState.kind === 'audiobook') {
            setAudiobookAuthorDir(authorName ? joinPath(path, authorName) : path);
          } else {
            setEbookAuthorDir(authorName ? joinPath(path, authorName) : path);
          }
          setFolderBrowserState({ open: false, kind: null, initialPath: null });
        }}
      />
    </>
  );
};
