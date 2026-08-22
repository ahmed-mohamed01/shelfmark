// oxlint-disable-next-line no-restricted-imports -- syncs the fetched default root / rendered preview into the selection state
import { useCallback, useEffect, useMemo, useState } from 'react';

import { useDestinationLayout } from '../hooks/useDestinationLayout';
import type { ReleaseSelectionConfig } from '../hooks/useReleaseSelection';
import type { Book, ContentType } from '../types';
import {
  buildDestinationPreview,
  stripAuthorPrefix,
  stripSeriesFolderSegment,
} from '../utils/destinationPreview';
import { stripTrailingAuthorName } from '../utils/monitoredPaths';
import { Dropdown } from './Dropdown';
import { FolderBrowserModal } from './FolderBrowserModal';

interface ReleaseSaveToBarProps {
  book: Book;
  selection: ReleaseSelectionConfig;
  /** Stacking context for the folder browser, which opens above the release modal. */
  browseOverlayZIndex?: number;
}

const CheckIcon = ({ className }: { className: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2.5}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <polyline points="20 6 9 17 4 12" />
  </svg>
);

const ChevronIcon = ({ className }: { className: string }) => (
  <svg
    className={className}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth={2.5}
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
  >
    <polyline points="6 9 12 15 18 9" />
  </svg>
);

/**
 * SAVE TO strip for the book modal: a root dropdown, an "Organize into folders"
 * checkbox, and a live PREVIEW.
 *
 * Everything files under <root>/<Author>. Ticked → organize (folders) via the
 * global organize template (author stripped); unticked → ebooks keep their
 * original filename, audiobooks still get their own folder (series stripped).
 * Follows the active ebook/audiobook step (separate roots + templates).
 */
export const ReleaseSaveToBar = ({
  book,
  selection,
  browseOverlayZIndex = 1300,
}: ReleaseSaveToBarProps) => {
  const authorName = book.author || '';
  const ebookLayout = useDestinationLayout({
    contentType: 'ebook',
    authorName,
    enabled: selection.stepAvailability.ebook,
  });
  const audiobookLayout = useDestinationLayout({
    contentType: 'audiobook',
    authorName,
    enabled: selection.stepAvailability.audiobook,
  });

  const step: ContentType = selection.activeStep;
  const layoutState = step === 'audiobook' ? audiobookLayout : ebookLayout;
  const pickedRoot = selection.roots[step];
  const effectiveRoot = pickedRoot ?? layoutState.defaultPath;

  const { onRootChange, onPreviewChange } = selection;
  // Adopt the default root once it loads so the queue action has a concrete root.
  useEffect(() => {
    if (pickedRoot == null && layoutState.defaultPath) {
      onRootChange(step, layoutState.defaultPath);
    }
  }, [pickedRoot, layoutState.defaultPath, step, onRootChange]);

  // Everything lands under <root>/<Author>; the template is applied inside it
  // (author stripped). The checkbox, per content type:
  //   ebook  ON  → organize template   ·  OFF → none (original filename)
  //   audiobook ON → organize template ·  OFF → organize, series folder stripped
  //                  (audiobooks are multi-file and always need their own folder)
  const organizeTmpl = stripAuthorPrefix(layoutState.layout?.organizeTemplate ?? '');
  let template = '';
  let renderMode: 'organize' | 'none' = 'organize';
  if (selection.organize) {
    template = organizeTmpl;
  } else if (step === 'audiobook') {
    template = stripSeriesFolderSegment(organizeTmpl);
  } else {
    renderMode = 'none';
  }

  const selectedRelease = selection.selected[step];
  // Manual-query books have no author; the download derives it from the release
  // (buildReleaseDownloadPayload), so the preview must too or it understates the
  // author folder.
  const releaseAuthor =
    typeof selectedRelease?.extra?.author === 'string' ? selectedRelease.extra.author : '';
  const previewBook = book.author ? book : { ...book, author: releaseAuthor };

  const preview = useMemo(() => {
    if (!effectiveRoot) return null;
    return buildDestinationPreview({
      root: effectiveRoot,
      template,
      book: previewBook,
      contentType: step,
      renderMode,
      releaseFormat: selectedRelease?.format ?? null,
      releaseTitle: selectedRelease?.title ?? null,
    });
  }, [effectiveRoot, template, renderMode, previewBook, step, selectedRelease]);

  useEffect(() => {
    onPreviewChange(step, preview?.full ?? null);
  }, [preview, step, onPreviewChange]);

  const [browseOpen, setBrowseOpen] = useState(false);
  const handleBrowseSelect = useCallback(
    (picked: string) => {
      onRootChange(step, stripTrailingAuthorName(picked, authorName));
      setBrowseOpen(false);
    },
    [onRootChange, step, authorName],
  );

  const roots = layoutState.destinations;
  const menuRoots =
    effectiveRoot && !roots.some((r) => r.path === effectiveRoot)
      ? [...roots, { path: effectiveRoot, label: effectiveRoot, isDefault: false }]
      : roots;
  // Hint reflects the effective behavior: the full path template (author folder +
  // template) when organizing, or a plain note when the file keeps its name.
  const hint = renderMode === 'none' ? 'keeps original filename' : `{Author}/${template}`;

  if (layoutState.failed) {
    return null;
  }

  return (
    <>
      <div className="shrink-0 border-t border-(--border-muted) bg-black/[0.03] px-5 py-3 dark:bg-black/20">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <span className="text-[11px] font-bold tracking-[0.06em] text-gray-500 dark:text-gray-400">
            SAVE TO
          </span>

          <Dropdown
            align="left"
            usePortal
            widthClassName="inline-flex max-w-full"
            panelClassName="z-[1350] min-w-[260px] max-w-[90vw] p-1.5"
            renderTrigger={({ isOpen, toggle }) => (
              <button
                type="button"
                onClick={toggle}
                aria-haspopup="listbox"
                aria-expanded={isOpen}
                aria-label="Save location"
                title={effectiveRoot ?? undefined}
                className="inline-flex max-w-[16rem] items-center gap-1.5 rounded-[9px] border border-(--border-muted) bg-black/5 px-2.5 py-1.5 font-mono text-xs font-semibold text-(--text) transition-colors hover:bg-black/10 sm:max-w-[24rem] dark:bg-white/10 dark:hover:bg-white/15"
              >
                <span className="truncate">
                  {effectiveRoot ?? (layoutState.loading ? 'Loading…' : 'Default')}
                </span>
                <ChevronIcon
                  className={`h-2.5 w-2.5 shrink-0 text-gray-500 transition-transform duration-150 dark:text-gray-400 ${isOpen ? 'rotate-180' : ''}`}
                />
              </button>
            )}
          >
            {({ close }) => (
              <div role="listbox" aria-label="Save location" className="flex flex-col gap-0.5">
                {menuRoots.length === 0 ? (
                  <div className="px-3 py-2 text-xs text-gray-500 dark:text-gray-400">
                    {layoutState.loading ? 'Loading…' : 'No save locations configured'}
                  </div>
                ) : null}
                {menuRoots.map((root) => {
                  const isSelected = root.path === effectiveRoot;
                  const suffix = [
                    root.authorFolderExists ? '✓' : '',
                    root.isDefault ? '— default' : '',
                  ]
                    .filter(Boolean)
                    .join(' ');
                  return (
                    <button
                      key={root.path}
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      onClick={() => {
                        onRootChange(step, root.isDefault ? layoutState.defaultPath : root.path);
                        close();
                      }}
                      className={`hover-surface flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left font-mono text-[13px] ${
                        isSelected
                          ? 'font-semibold text-emerald-600 dark:text-emerald-400'
                          : 'text-(--text)'
                      }`}
                      title={
                        root.authorFolderExists ? 'Author folder already exists here' : undefined
                      }
                    >
                      <span className="min-w-0 truncate">
                        {root.path}
                        {suffix ? (
                          <span className="ml-2 text-gray-500 dark:text-gray-400">{suffix}</span>
                        ) : null}
                      </span>
                      {isSelected ? <CheckIcon className="h-3.5 w-3.5 shrink-0" /> : null}
                    </button>
                  );
                })}
                <div className="my-1 border-t border-(--border-muted)" />
                <button
                  type="button"
                  onClick={() => {
                    close();
                    setBrowseOpen(true);
                  }}
                  className="hover-surface flex w-full items-center rounded-lg px-3 py-2 text-left text-[13px] text-(--text)"
                >
                  Browse…
                </button>
              </div>
            )}
          </Dropdown>

          <button
            type="button"
            role="checkbox"
            aria-checked={selection.organize}
            onClick={() => selection.onOrganizeChange(!selection.organize)}
            className="inline-flex cursor-pointer items-center gap-2 text-xs font-semibold text-(--text)"
          >
            <span
              className={`flex h-4 w-4 items-center justify-center rounded border transition-colors ${
                selection.organize
                  ? 'border-emerald-600 bg-emerald-600 text-white'
                  : 'border-(--border-muted) bg-transparent'
              }`}
            >
              {selection.organize ? <CheckIcon className="h-3 w-3" /> : null}
            </span>
            Organize into folders
          </button>

          {hint ? (
            <span className="ml-auto hidden text-[11px] text-gray-500 sm:inline dark:text-gray-400">
              template <span className="font-mono text-gray-700 dark:text-gray-300">{hint}</span>
            </span>
          ) : null}
        </div>

        {preview?.full ? (
          <div className="mt-2 flex min-w-0 items-center gap-2 rounded-[9px] border border-(--border-muted) bg-black/[0.04] px-3 py-2 dark:bg-black/30">
            <span className="shrink-0 text-[10.5px] font-bold tracking-[0.08em] text-gray-500 dark:text-gray-400">
              PREVIEW
            </span>
            <span className="min-w-0 font-mono text-xs break-all text-emerald-700 dark:text-emerald-400">
              {preview.full}
            </span>
          </div>
        ) : null}
      </div>

      <FolderBrowserModal
        open={browseOpen}
        title={step === 'audiobook' ? 'Select audiobook folder' : 'Select ebook folder'}
        initialPath={effectiveRoot}
        overlayZIndex={browseOverlayZIndex}
        quickRoots={roots.map((d) => d.path)}
        onClose={() => setBrowseOpen(false)}
        onSelect={handleBrowseSelect}
      />
    </>
  );
};
