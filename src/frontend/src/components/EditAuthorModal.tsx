import { useCallback, useEffect, useState } from 'react';
import { getMonitoredEntity, patchMonitoredEntity, MonitoredEntity } from '../services/api';
import { deleteMonitoredAuthorsByIds } from '../services/monitoredAuthors';
import { FolderBrowserModal } from './FolderBrowserModal';

type MonitorMode = 'all' | 'missing' | 'upcoming';

interface ParsedEntitySettings {
  ebookAuthorDir: string;
  audiobookAuthorDir: string;
  monitorEbookMode: MonitorMode;
  monitorAudiobookMode: MonitorMode;
}

function parseEntitySettings(settings: Record<string, unknown>): ParsedEntitySettings {
  const validateMode = (v: unknown): MonitorMode =>
    v === 'all' || v === 'missing' ? v : 'upcoming';
  return {
    ebookAuthorDir: typeof settings.ebook_author_dir === 'string' ? settings.ebook_author_dir : '',
    audiobookAuthorDir: typeof settings.audiobook_author_dir === 'string' ? settings.audiobook_author_dir : '',
    monitorEbookMode: validateMode(settings.monitor_ebook_mode),
    monitorAudiobookMode: validateMode(settings.monitor_audiobook_mode),
  };
}

interface EditAuthorModalProps {
  open: boolean;
  entityId: number | null;
  authorName: string;
  onClose: () => void;
  onDeleted?: () => void;
  onSaved?: () => void;
}

export const EditAuthorModal = ({
  open,
  entityId,
  authorName,
  onClose,
  onDeleted,
  onSaved,
}: EditAuthorModalProps) => {
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ebookAuthorDir, setEbookAuthorDir] = useState('');
  const [audiobookAuthorDir, setAudiobookAuthorDir] = useState('');
  const [monitorEbookMode, setMonitorEbookMode] = useState<MonitorMode>('upcoming');
  const [monitorAudiobookMode, setMonitorAudiobookMode] = useState<MonitorMode>('upcoming');
  const [entity, setEntity] = useState<MonitoredEntity | null>(null);
  const [folderBrowserState, setFolderBrowserState] = useState<{
    open: boolean;
    kind: 'ebook' | 'audiobook' | null;
    initialPath: string | null;
  }>({
    open: false,
    kind: null,
    initialPath: null,
  });

  useEffect(() => {
    if (!open || !entityId) {
      setLoading(false);
      setError(null);
      setEbookAuthorDir('');
      setAudiobookAuthorDir('');
      setMonitorEbookMode('upcoming');
      setMonitorAudiobookMode('upcoming');
      setEntity(null);
      return;
    }

    let alive = true;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const loadedEntity = await getMonitoredEntity(entityId);
        if (!alive) return;
        setEntity(loadedEntity);
        const parsed = parseEntitySettings(loadedEntity.settings || {});
        setEbookAuthorDir(parsed.ebookAuthorDir);
        setAudiobookAuthorDir(parsed.audiobookAuthorDir);
        setMonitorEbookMode(parsed.monitorEbookMode);
        setMonitorAudiobookMode(parsed.monitorAudiobookMode);
      } catch (e) {
        if (!alive) return;
        const message = e instanceof Error ? e.message : 'Failed to load author settings';
        setError(message);
      } finally {
        if (alive) setLoading(false);
      }
    };

    void load();
    return () => {
      alive = false;
    };
  }, [open, entityId]);

  const handleSave = useCallback(async () => {
    if (!entityId || saving || deleting) return;

    setSaving(true);
    setError(null);
    try {
      const updated = await patchMonitoredEntity(entityId, {
        settings: {
          ebook_author_dir: ebookAuthorDir || undefined,
          audiobook_author_dir: audiobookAuthorDir || undefined,
          monitor_ebook_mode: monitorEbookMode,
          monitor_audiobook_mode: monitorAudiobookMode,
        },
      });
      setEntity(updated);
      const parsed = parseEntitySettings(updated.settings || {});
      setEbookAuthorDir(parsed.ebookAuthorDir);
      setAudiobookAuthorDir(parsed.audiobookAuthorDir);
      setMonitorEbookMode(parsed.monitorEbookMode);
      setMonitorAudiobookMode(parsed.monitorAudiobookMode);
      onSaved?.();
      onClose();
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to save paths';
      setError(message);
    } finally {
      setSaving(false);
    }
  }, [entityId, saving, deleting, ebookAuthorDir, audiobookAuthorDir, monitorEbookMode, monitorAudiobookMode, onSaved, onClose]);

  const handleDelete = useCallback(async () => {
    if (!entityId || deleting || saving) return;

    const confirmed = window.confirm(
      `Delete monitored author "${authorName || 'Unknown author'}"?\n\n` +
      'This removes monitored author data from Shelfmark database only (books, file matches, and settings for this monitored author).\n' +
      'Files on disk will NOT be deleted.'
    );
    if (!confirmed) return;

    setDeleting(true);
    setError(null);
    try {
      const { failedIds } = await deleteMonitoredAuthorsByIds([entityId]);
      if (failedIds.length > 0) {
        throw new Error('Failed to delete monitored author');
      }
      onDeleted?.();
      onClose();
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Failed to delete author';
      setError(message);
    } finally {
      setDeleting(false);
    }
  }, [entityId, deleting, saving, authorName, onDeleted, onClose]);

  const handleCancel = useCallback(() => {
    if (saving || deleting) return;
    const parsed = parseEntitySettings(entity?.settings || {});
    setEbookAuthorDir(parsed.ebookAuthorDir);
    setAudiobookAuthorDir(parsed.audiobookAuthorDir);
    setMonitorEbookMode(parsed.monitorEbookMode);
    setMonitorAudiobookMode(parsed.monitorAudiobookMode);
    setError(null);
    onClose();
  }, [saving, deleting, entity, onClose]);

  if (!open) return null;

  return (
    <>
      <div
        className="modal-overlay active sm:px-6 sm:py-6"
        style={{ zIndex: 2000, pointerEvents: folderBrowserState.open ? 'none' : 'auto' }}
        onClick={(e) => {
          if (e.target === e.currentTarget && !saving && !deleting) {
            handleCancel();
          }
        }}
      >
        <div className="details-container w-full max-w-2xl h-auto settings-modal-enter" role="dialog" aria-modal="true" aria-label="Edit monitored author">
          <div className="rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] sm:bg-[var(--bg-soft)] text-[var(--text)] shadow-2xl overflow-hidden">
            <header className="flex items-start justify-between gap-3 border-b border-[var(--border-muted)] px-5 py-4">
              <div className="min-w-0">
                <div className="text-xs uppercase tracking-wide text-gray-500 dark:text-gray-400">Edit</div>
                <div className="mt-1 text-base font-semibold truncate">{authorName || 'Unknown author'}</div>
              </div>
              <button
                type="button"
                onClick={handleCancel}
                disabled={saving || deleting}
                className="rounded-full p-2 text-gray-500 transition-colors hover-action hover:text-gray-900 dark:hover:text-gray-100 disabled:opacity-50"
                aria-label="Close"
              >
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </header>

            <div className="px-5 py-4 space-y-4">
              {loading ? <div className="text-sm text-gray-500 dark:text-gray-400">Loading…</div> : null}
              {error ? <div className="text-sm text-red-500">{error}</div> : null}

              <div className="space-y-2">
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">eBooks Path</div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setFolderBrowserState({ open: true, kind: 'ebook', initialPath: ebookAuthorDir || null })}
                    disabled={loading || saving || deleting}
                    className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100 disabled:opacity-50"
                  >
                    Browse
                  </button>
                  <input
                    value={ebookAuthorDir}
                    onChange={(e) => setEbookAuthorDir(e.target.value)}
                    placeholder="/books/ebooks/Author Name"
                    className="flex-1 px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm"
                    disabled={loading || saving || deleting}
                  />
                </div>
              </div>

              <div className="space-y-2">
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">Audiobooks Path</div>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => setFolderBrowserState({ open: true, kind: 'audiobook', initialPath: audiobookAuthorDir || null })}
                    disabled={loading || saving || deleting}
                    className="px-3 py-1.5 rounded-full bg-white/70 hover:bg-white text-gray-900 text-xs font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100 disabled:opacity-50"
                  >
                    Browse
                  </button>
                  <input
                    value={audiobookAuthorDir}
                    onChange={(e) => setAudiobookAuthorDir(e.target.value)}
                    placeholder="/books/audiobooks/Author Name"
                    className="flex-1 px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm"
                    disabled={loading || saving || deleting}
                  />
                </div>
              </div>

              <div className="border-t border-[var(--border-muted)] pt-4 mt-4">
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100 mb-3">Monitoring Settings</div>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                  <div className="space-y-1.5">
                    <label className="text-xs text-gray-600 dark:text-gray-400">Monitor eBooks</label>
                    <select
                      value={monitorEbookMode}
                      onChange={(e) => setMonitorEbookMode(e.target.value as MonitorMode)}
                      disabled={loading || saving || deleting}
                      className="w-full px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm disabled:opacity-50"
                    >
                      <option value="upcoming">Upcoming releases only</option>
                      <option value="missing">All missing</option>
                      <option value="all">All books</option>
                    </select>
                  </div>
                  <div className="space-y-1.5">
                    <label className="text-xs text-gray-600 dark:text-gray-400">Monitor Audiobooks</label>
                    <select
                      value={monitorAudiobookMode}
                      onChange={(e) => setMonitorAudiobookMode(e.target.value as MonitorMode)}
                      disabled={loading || saving || deleting}
                      className="w-full px-3 py-2 rounded-xl bg-white/80 dark:bg-white/10 border border-black/10 dark:border-white/10 text-sm disabled:opacity-50"
                    >
                      <option value="upcoming">Upcoming releases only</option>
                      <option value="missing">All missing</option>
                      <option value="all">All books</option>
                    </select>
                  </div>
                </div>
                <p className="mt-2 text-xs text-gray-500 dark:text-gray-400">
                  Controls which books are auto-monitored when syncing. "Upcoming" monitors unreleased books missing files. "Missing" monitors all books without files. "All" monitors every book.
                </p>
              </div>
            </div>

            <footer className="flex items-center justify-between gap-2 border-t border-[var(--border-muted)] px-5 py-4 bg-[var(--bg)] sm:bg-[var(--bg-soft)]">
              <button
                type="button"
                onClick={() => void handleDelete()}
                disabled={loading || saving || deleting}
                className="px-4 py-2 rounded-full bg-red-600 hover:bg-red-700 disabled:opacity-60 text-white font-medium"
                title="Deletes monitored author records from database only. Files on disk are not deleted."
              >
                {deleting ? 'Deleting…' : 'Delete Author'}
              </button>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={handleCancel}
                  disabled={saving || deleting}
                  className="px-4 py-2 rounded-full bg-white/70 hover:bg-white text-gray-900 font-medium dark:bg-white/10 dark:hover:bg-white/20 dark:text-gray-100 disabled:opacity-50"
                >
                  Cancel
                </button>
                <button
                  type="button"
                  disabled={loading || saving || deleting}
                  onClick={() => void handleSave()}
                  className="px-4 py-2 rounded-full bg-emerald-600 hover:bg-emerald-700 disabled:opacity-60 text-white font-medium"
                >
                  {saving ? 'Saving…' : 'Save'}
                </button>
              </div>
            </footer>
          </div>
        </div>
      </div>

      <FolderBrowserModal
        open={folderBrowserState.open}
        title={folderBrowserState.kind === 'audiobook' ? 'Select audiobook folder' : 'Select ebook folder'}
        initialPath={folderBrowserState.initialPath}
        onClose={() => setFolderBrowserState({ open: false, kind: null, initialPath: null })}
        onSelect={(path) => {
          if (folderBrowserState.kind === 'audiobook') {
            setAudiobookAuthorDir(path);
          } else {
            setEbookAuthorDir(path);
          }
          setFolderBrowserState({ open: false, kind: null, initialPath: null });
        }}
      />
    </>
  );
};
