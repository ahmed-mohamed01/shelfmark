// oxlint-disable-next-line no-restricted-imports -- form resets on author identity; the dialog stays mounted across targets
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useDestinationLayout } from '../hooks/useDestinationLayout';
import { updateSelfUser } from '../services/api';
import { createMonitoredEntity, type MonitoredEntity } from '../services/monitoredApi';
import { joinPath, normalizeAbsolutePath, stripTrailingAuthorName } from '../utils/monitoredPaths';
import { DestinationPathBuilder } from './DestinationPathBuilder';
import { FolderBrowserModal } from './FolderBrowserModal';
import { ToggleSwitch } from './shared/ToggleSwitch';

// ---------------------------------------------------------------------------
// Helpers (kept local so the component stays self-contained)
// ---------------------------------------------------------------------------

const normalizeAuthor = (value: string): string => value.split(/\s+/).join(' ').trim();

type MonitorMode = 'none' | 'all' | 'missing' | 'upcoming';

const MONITOR_MODE_OPTIONS: { value: MonitorMode; label: string; hint: string }[] = [
  {
    value: 'none',
    label: 'No monitoring',
    hint: 'Author is tracked but nothing downloads automatically.',
  },
  { value: 'all', label: 'All books', hint: 'Grab every book and watch for new releases.' },
  {
    value: 'missing',
    label: 'Missing only',
    hint: 'Grab books not in your library, plus new releases.',
  },
  { value: 'upcoming', label: 'Future releases', hint: 'Only releases from now on.' },
];

const monitorModeHint = (mode: MonitorMode): string =>
  MONITOR_MODE_OPTIONS.find((o) => o.value === mode)?.hint ?? '';

const isMonitorMode = (value: string): value is MonitorMode =>
  MONITOR_MODE_OPTIONS.some((o) => o.value === value);

const toMonitorMode = (value: string): MonitorMode => (isMonitorMode(value) ? value : 'none');

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

/**
 * Monitor Author dialog with a structured destination: `[root ▾] / [🔒 Author]
 * / [{Series}]` per content type, previews derived from the same library layout
 * one-off downloads use, monitoring modes with hints, and the visibility toggle.
 *
 * The payload shape is unchanged — `ebook_author_dir` / `audiobook_author_dir`
 * are still `root/Author` — plus the new per-author `series_folder` switch.
 */
export const AuthorMonitorModal = ({
  author,
  onClose,
  onMonitored,
  authRequired = false,
  overlayZIndex = 1200,
}: AuthorMonitorModalProps) => {
  const authorName = author ? normalizeAuthor(author.name) : '';

  // Roots + library layout per content type; the server also flags roots that
  // already hold this author's folder.
  const ebookLayout = useDestinationLayout({
    contentType: 'ebook',
    authorName,
    enabled: Boolean(author),
  });
  const audiobookLayout = useDestinationLayout({
    contentType: 'audiobook',
    authorName,
    enabled: Boolean(author),
  });

  // null = the configured default for that content type.
  const [ebookRoot, setEbookRoot] = useState<string | null>(null);
  const [audiobookEnabled, setAudiobookEnabled] = useState(false);
  const [audiobookRoot, setAudiobookRoot] = useState<string | null>(null);
  const [seriesFolder, setSeriesFolder] = useState(true);
  const [monitorEbookMode, setMonitorEbookMode] = useState<MonitorMode>('none');
  const [monitorAudiobookMode, setMonitorAudiobookMode] = useState<MonitorMode>('none');
  const [visibility, setVisibility] = useState<'public' | 'private'>('public');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Identifies one open-dialog "session". Bumped whenever the dialog opens on a
  // new author target, so an in-flight create from an earlier session can never
  // close, error, or un-disable a later one.
  const sessionRef = useRef(0);

  const [folderBrowserState, setFolderBrowserState] = useState<{
    open: boolean;
    kind: 'ebook' | 'audiobook' | null;
  }>({ open: false, kind: null });

  // Reset to defaults whenever the dialog is opened. Keyed on the object, not
  // the name, so reopening the same author still starts from a clean form.
  useEffect(() => {
    if (!author) return;
    sessionRef.current += 1;
    setEbookRoot(null);
    setAudiobookEnabled(false);
    setAudiobookRoot(null);
    setSeriesFolder(true);
    setMonitorEbookMode('none');
    setMonitorAudiobookMode('none');
    setVisibility('public');
    setError(null);
    setSaving(false);
    setFolderBrowserState({ open: false, kind: null });
  }, [author]);

  const ebookRootEffective = ebookRoot ?? ebookLayout.defaultPath;
  const audiobookRootEffective = audiobookRoot ?? audiobookLayout.defaultPath;

  // Monitored authors always expose the series-folder switch; the folder is
  // created by the entity's own template (root/Author[/Series]/…). The preview
  // is schematic — it doesn't depend on the global File Organization template.
  const showSeriesChip = true;

  const previewFor = useCallback(
    (root: string | null): string => {
      if (!root || !authorName) return '';
      return `${normalizeAbsolutePath(root)}/${authorName}${seriesFolder ? '/{Series}' : ''}/`;
    },
    [authorName, seriesFolder],
  );
  const ebookPreview = useMemo(
    () => previewFor(ebookRootEffective),
    [previewFor, ebookRootEffective],
  );
  const audiobookPreview = useMemo(
    () => previewFor(audiobookRootEffective),
    [previewFor, audiobookRootEffective],
  );

  // Remember roots the user browsed to, so they are offered next time (they
  // feed resolve_allowed_roots via MONITORED_<TYPE>_ROOTS).
  const persistLearnedRoots = useCallback(
    async (nextEbookRoot: string, nextAudiobookRoot: string) => {
      const nextSettings: Record<string, unknown> = {};
      const ebookKnown = ebookLayout.destinations.map((d) => d.path);
      const audioKnown = audiobookLayout.destinations.map((d) => d.path);
      if (nextEbookRoot && !ebookKnown.includes(nextEbookRoot)) {
        nextSettings.MONITORED_EBOOK_ROOTS = Array.from(
          new Set([
            nextEbookRoot,
            ...ebookKnown.filter(
              (p) => !ebookLayout.destinations.find((d) => d.path === p)?.isDefault,
            ),
          ]),
        );
      }
      if (nextAudiobookRoot && !audioKnown.includes(nextAudiobookRoot)) {
        nextSettings.MONITORED_AUDIOBOOK_ROOTS = Array.from(
          new Set([
            nextAudiobookRoot,
            ...audioKnown.filter(
              (p) => !audiobookLayout.destinations.find((d) => d.path === p)?.isDefault,
            ),
          ]),
        );
      }
      if (Object.keys(nextSettings).length === 0) return;
      try {
        await updateSelfUser({ settings: nextSettings });
      } catch {
        // Best-effort persistence; ignore.
      }
    },
    [ebookLayout.destinations, audiobookLayout.destinations],
  );

  const handleConfirm = useCallback(async () => {
    if (!author || saving) return;

    const normalized = normalizeAuthor(author.name);
    if (!normalized) return;

    const finalEbookRoot = normalizeAbsolutePath(ebookRootEffective ?? '');
    const finalAudiobookRoot = audiobookEnabled
      ? normalizeAbsolutePath(audiobookRootEffective ?? '')
      : '';
    const finalEbookDir = finalEbookRoot ? joinPath(finalEbookRoot, normalized) : '';
    const finalAudiobookDir = finalAudiobookRoot ? joinPath(finalAudiobookRoot, normalized) : '';

    if (!finalEbookDir && !finalAudiobookDir) {
      setError('Pick an ebook library root, or enable an audiobook folder.');
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
          monitor_audiobook_mode: audiobookEnabled ? monitorAudiobookMode : 'none',
          series_folder: seriesFolder,
        },
        visibility,
      });

      void persistLearnedRoots(finalEbookRoot, finalAudiobookRoot);

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
    ebookRootEffective,
    audiobookEnabled,
    audiobookRootEffective,
    monitorEbookMode,
    monitorAudiobookMode,
    seriesFolder,
    visibility,
    persistLearnedRoots,
    onMonitored,
    onClose,
  ]);

  if (!author) return null;

  const rootsError =
    ebookLayout.failed && audiobookLayout.failed
      ? 'Could not load save locations — use Browse to pick a folder.'
      : null;

  const renderModeSelect = (
    label: string,
    value: MonitorMode,
    onChange: (next: MonitorMode) => void,
    disabled: boolean,
    disabledHint?: string,
  ) => (
    <label className="space-y-1">
      <div className="text-xs font-medium text-gray-700 dark:text-gray-300">{label}</div>
      <select
        value={disabled ? 'none' : value}
        onChange={(e) => onChange(toMonitorMode(e.target.value))}
        disabled={disabled}
        className="w-full rounded-xl border border-black/10 bg-white/80 px-3 py-2 text-sm disabled:cursor-not-allowed disabled:opacity-60 dark:border-white/10 dark:bg-white/10"
      >
        {disabled ? (
          <option value="none">{disabledHint ?? 'Unavailable'}</option>
        ) : (
          MONITOR_MODE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))
        )}
      </select>
      <div className="text-[11.5px] text-gray-500 dark:text-gray-400">
        {disabled ? 'Disabled until an audiobook folder is set.' : monitorModeHint(value)}
      </div>
    </label>
  );

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
          className="details-container settings-modal-enter h-auto w-full max-w-xl"
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

            <div className="space-y-5 px-5 py-4">
              {error || rootsError ? (
                <div className="flex flex-col gap-1">
                  {error ? <div className="text-sm text-red-500">{error}</div> : null}
                  {rootsError ? <div className="text-sm text-red-500">{rootsError}</div> : null}
                </div>
              ) : null}

              {/* Ebook folder */}
              <div className="space-y-2">
                <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                  Ebook folder
                </div>
                <DestinationPathBuilder
                  roots={ebookLayout.destinations}
                  rootValue={ebookRootEffective}
                  onRootChange={(root) => setEbookRoot(root)}
                  onBrowse={() => setFolderBrowserState({ open: true, kind: 'ebook' })}
                  authorName={authorName}
                  showSeriesChip={showSeriesChip}
                  seriesEnabled={seriesFolder}
                  onSeriesToggle={setSeriesFolder}
                  previewPath={ebookPreview}
                  loading={ebookLayout.loading}
                  disabled={saving}
                />
              </div>

              {/* Audiobook folder */}
              <div className="space-y-2">
                <div className="flex items-center justify-between gap-3">
                  <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                    Audiobook folder
                  </div>
                  <label className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400">
                    <ToggleSwitch
                      checked={audiobookEnabled}
                      onChange={setAudiobookEnabled}
                      color="emerald"
                      disabled={saving}
                      ariaLabel="Enable audiobook folder"
                    />
                    {audiobookEnabled ? 'Enabled' : 'Off'}
                  </label>
                </div>
                {audiobookEnabled ? (
                  <DestinationPathBuilder
                    roots={audiobookLayout.destinations}
                    rootValue={audiobookRootEffective}
                    onRootChange={(root) => setAudiobookRoot(root)}
                    onBrowse={() => setFolderBrowserState({ open: true, kind: 'audiobook' })}
                    authorName={authorName}
                    showSeriesChip={showSeriesChip}
                    seriesEnabled={seriesFolder}
                    onSeriesToggle={setSeriesFolder}
                    previewPath={audiobookPreview}
                    loading={audiobookLayout.loading}
                    disabled={saving}
                  />
                ) : (
                  <div className="text-xs text-gray-500 dark:text-gray-400">
                    Audiobooks get a per-book folder (multi-file); ebooks save directly into the
                    series folder.
                  </div>
                )}
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                {renderModeSelect('eBook monitoring', monitorEbookMode, setMonitorEbookMode, false)}
                {renderModeSelect(
                  'Audiobook monitoring',
                  monitorAudiobookMode,
                  setMonitorAudiobookMode,
                  !audiobookEnabled,
                  'Enable audiobook folder first',
                )}
              </div>

              {authRequired && (
                <label className="mt-1 flex items-center gap-2">
                  <ToggleSwitch
                    checked={visibility === 'public'}
                    onChange={(next) => setVisibility(next ? 'public' : 'private')}
                    color="emerald"
                    disabled={saving}
                    ariaLabel="Shared with all users"
                  />
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
        initialPath={
          folderBrowserState.kind === 'audiobook' ? audiobookRootEffective : ebookRootEffective
        }
        overlayZIndex={overlayZIndex + 100}
        quickRoots={(folderBrowserState.kind === 'audiobook'
          ? audiobookLayout.destinations
          : ebookLayout.destinations
        ).map((d) => d.path)}
        onClose={() => setFolderBrowserState({ open: false, kind: null })}
        onSelect={(path) => {
          // Browsing straight into the author folder must not nest a second one.
          const parent = stripTrailingAuthorName(normalizeAbsolutePath(path), authorName);
          if (folderBrowserState.kind === 'audiobook') {
            setAudiobookRoot(parent);
          } else {
            setEbookRoot(parent);
          }
          setFolderBrowserState({ open: false, kind: null });
        }}
      />
    </>
  );
};
