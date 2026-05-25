import { useCallback, useEffect, useMemo, useState } from 'react';

import {
  attachBookFile,
  detachMatch,
  listMatchCandidates,
  listMatchCandidatesForBook,
  setManualMatch,
  type ChosenCandidate,
  type MatchCandidate,
  type MatchCandidatesResponse,
} from '../services/monitoredApi';
import { showConfirm } from './ConfirmDialog';

/**
 * Two modes:
 *   - `byFile`: existing file row, swap which file is attached or detach.
 *   - `byBook`: book has no attribution of the given file_type yet, pick
 *     a file to attach (no detach option).
 */
export type FixMatchTarget =
  | { mode: 'byFile'; fileId: number }
  | {
      mode: 'byBook';
      provider: string;
      providerBookId: string;
      fileType: 'ebook' | 'audiobook';
    };

interface FixMatchModalProps {
  entityId: number;
  target: FixMatchTarget;
  onClose: () => void;
  /** Called after a successful set / detach so the parent refetches. */
  onApplied: () => void;
}

const formatPosition = (pos: number | null | undefined): string => {
  if (pos == null) return '';
  return `#${pos}`;
};

const sourceLabel = (s: string | null): string => {
  if (!s) return 'unknown';
  if (s === 'audiobookshelf') return 'AudioBookShelf';
  if (s === 'grimmory') return 'Grimmory';
  if (s === 'filesystem') return 'Filesystem';
  return s;
};

/** Stable identifier for a candidate (DB id or source+path) used as the
 *  radio selection key. Virtual candidates have id=null so we fall back. */
const candidateKey = (c: MatchCandidate): string =>
  c.file.id != null ? `id:${c.file.id}` : `sp:${c.file.source}|${c.file.path}`;

const candidateToChosen = (
  c: MatchCandidate,
  fallbackFileType: 'ebook' | 'audiobook',
): ChosenCandidate => {
  if (c.file.id != null) return { fileId: c.file.id };
  const ft =
    c.file.file_type === 'audiobook' || c.file.file_type === 'ebook'
      ? c.file.file_type
      : fallbackFileType;
  return { source: c.file.source, path: c.file.path, fileType: ft };
};

export const FixMatchModal = ({ entityId, target, onClose, onApplied }: FixMatchModalProps) => {
  const [data, setData] = useState<MatchCandidatesResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);

  const isAttach = target.mode === 'byBook';
  const fallbackFileType: 'ebook' | 'audiobook' = isAttach ? target.fileType : 'ebook';

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    const loader =
      target.mode === 'byFile'
        ? listMatchCandidates(entityId, target.fileId)
        : listMatchCandidatesForBook(
            entityId,
            target.provider,
            target.providerBookId,
            target.fileType,
          );
    loader
      .then((resp) => {
        if (cancelled) return;
        setData(resp);
        const cur = resp.candidates.find((c) => c.is_current);
        if (cur) setSelectedKey(candidateKey(cur));
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : 'Failed to load candidates');
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [entityId, target]);

  const selectedCandidate = useMemo(
    () => data?.candidates.find((c) => candidateKey(c) === selectedKey) ?? null,
    [data, selectedKey],
  );

  const targetBook = data?.target_book;

  const onApply = useCallback(async () => {
    if (!selectedCandidate) return;
    setSubmitting(true);
    setError(null);
    try {
      const chosen = candidateToChosen(selectedCandidate, fallbackFileType);
      if (target.mode === 'byFile') {
        await setManualMatch(entityId, target.fileId, chosen);
      } else {
        await attachBookFile(entityId, target.provider, target.providerBookId, chosen);
      }
      onApplied();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to set match');
    } finally {
      setSubmitting(false);
    }
  }, [selectedCandidate, fallbackFileType, target, entityId, onApplied, onClose]);

  const onDetach = useCallback(async () => {
    if (target.mode !== 'byFile') return;
    if (
      !(await showConfirm({
        title: 'Detach attribution',
        message:
          'Detach this attribution? Future syncs will not re-attribute this file to this book — other books in the entity may still be considered for it.',
        confirmLabel: 'Detach',
        destructive: true,
      }))
    )
      return;
    setSubmitting(true);
    setError(null);
    try {
      await detachMatch(entityId, target.fileId);
      onApplied();
      onClose();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to detach');
    } finally {
      setSubmitting(false);
    }
  }, [target, entityId, onApplied, onClose]);

  const headerTitle = isAttach
    ? `Add ${target.fileType === 'audiobook' ? 'audiobook' : 'ebook'}`
    : 'Fix match';

  return (
    <div
      className="modal-overlay active sm:px-6 sm:py-6"
      style={{ zIndex: 2100 }}
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="details-container settings-modal-enter h-auto w-full max-w-2xl"
        role="dialog"
        aria-modal="true"
        aria-label={headerTitle}
      >
        <div className="overflow-hidden rounded-2xl border border-[var(--border-muted)] bg-[var(--bg)] text-[var(--text)] shadow-2xl sm:bg-[var(--bg-soft)]">
          {/* Header */}
          <div className="border-b border-[var(--border-muted)] px-5 py-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <h2 className="text-base font-semibold text-gray-900 dark:text-gray-100">
                  {headerTitle}
                </h2>
                {targetBook ? (
                  <div className="mt-1 text-xs text-gray-600 dark:text-gray-400">
                    Pick the file that represents{' '}
                    <span className="font-medium text-gray-800 dark:text-gray-200">
                      {targetBook.title}
                    </span>
                    {targetBook.series_name ? (
                      <span className="text-gray-500 dark:text-gray-400">
                        {' '}
                        ({targetBook.series_name} {formatPosition(targetBook.series_position)})
                      </span>
                    ) : null}
                  </div>
                ) : null}
              </div>
              <button
                type="button"
                onClick={onClose}
                className="-mt-1 -mr-1 text-gray-500 hover:text-gray-900 dark:hover:text-gray-200"
                aria-label="Close"
              >
                <svg className="h-5 w-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
              </button>
            </div>
          </div>

          {/* Body */}
          <div className="max-h-[60vh] overflow-y-auto px-5 py-4">
            {loading ? (
              <div className="py-8 text-center text-sm text-gray-500 dark:text-gray-400">
                Loading candidates…
              </div>
            ) : error ? (
              <div className="py-4 text-sm text-red-600 dark:text-red-400">{error}</div>
            ) : !data || data.candidates.length === 0 ? (
              <div className="py-4 text-sm text-gray-500 dark:text-gray-400">
                No candidate files found. The scanner may not have discovered other files of this
                type in this author's library yet — try running a filesystem rescan or syncing
                AudioBookShelf / Grimmory.
              </div>
            ) : (
              <ul className="space-y-1">
                {data.candidates.map((c: MatchCandidate) => {
                  const key = candidateKey(c);
                  const checked = selectedKey === key;
                  const confPct = Math.round((c.confidence ?? 0) * 100);
                  const path = c.file.path || '';
                  const lastSlash = Math.max(path.lastIndexOf('/'), path.lastIndexOf('\\'));
                  const fileName = lastSlash >= 0 ? path.slice(lastSlash + 1) : path;
                  const dirPath = lastSlash >= 0 ? path.slice(0, lastSlash + 1) : '';
                  const isVirtual = c.file.id == null;
                  return (
                    <li key={key}>
                      <label
                        className={`flex cursor-pointer items-start gap-3 rounded-lg p-2 ${
                          checked
                            ? 'bg-blue-500/10 dark:bg-blue-500/20'
                            : 'hover:bg-black/5 dark:hover:bg-white/5'
                        }`}
                      >
                        <input
                          type="radio"
                          name="fix-match-candidate"
                          className="mt-1"
                          checked={checked}
                          onChange={() => setSelectedKey(key)}
                        />
                        <div className="min-w-0 flex-1">
                          <div className="text-sm break-words text-gray-900 dark:text-gray-100">
                            {fileName || '(no path)'}
                            {c.is_current ? (
                              <span className="ml-2 rounded bg-blue-500/20 px-1.5 py-0.5 text-[10px] tracking-wide text-blue-700 uppercase dark:text-blue-300">
                                current
                              </span>
                            ) : null}
                            {isVirtual ? (
                              <span
                                className="ml-2 rounded bg-amber-500/20 px-1.5 py-0.5 text-[10px] tracking-wide text-amber-700 uppercase dark:text-amber-300"
                                title="Discovered in the source but not yet attributed to any book"
                              >
                                unmatched
                              </span>
                            ) : null}
                          </div>
                          {dirPath ? (
                            <div className="mt-0.5 text-xs break-all text-gray-500 dark:text-gray-400">
                              {dirPath}
                            </div>
                          ) : null}
                          <div className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
                            {sourceLabel(c.file.source)}
                          </div>
                        </div>
                        <span className="text-xs whitespace-nowrap text-gray-500 tabular-nums dark:text-gray-400">
                          {confPct}%
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>

          {/* Footer */}
          <div className="flex items-center justify-between gap-3 border-t border-[var(--border-muted)] px-5 py-3">
            {target.mode === 'byFile' ? (
              <button
                type="button"
                onClick={onDetach}
                disabled={submitting || loading}
                className="text-xs text-red-600 hover:text-red-700 disabled:opacity-50 dark:text-red-400 dark:hover:text-red-300"
              >
                Detach attribution
              </button>
            ) : (
              <span />
            )}
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={onClose}
                disabled={submitting}
                className="rounded-lg border border-[var(--border-muted)] px-3 py-1.5 text-sm text-gray-700 hover:bg-black/5 disabled:opacity-50 dark:text-gray-300 dark:hover:bg-white/5"
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={onApply}
                disabled={submitting || loading || !selectedCandidate}
                className="rounded-lg bg-blue-600 px-3 py-1.5 text-sm text-white hover:bg-blue-500 disabled:opacity-50"
              >
                {submitting ? 'Setting…' : isAttach ? 'Add file' : 'Set match'}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
