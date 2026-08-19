import { useCallback, useEffect, useMemo, useState } from 'react';

import { DownloadDestination, listDownloadDestinations } from '../services/monitoredApi';
import { joinPath, stripTrailingAuthorName } from '../utils/monitoredPaths';
import { FolderBrowserModal } from './FolderBrowserModal';

interface SaveLocationPickerProps {
  /** Which destination set to offer. Follows the modal's ebook/audiobook tab. */
  contentType: 'ebook' | 'audiobook';
  /** Author of the book being downloaded, used to resolve the author folder. */
  authorName: string;
  /** Chosen parent folder, or null to use the configured default. */
  value: string | null;
  /** Called with the chosen parent folder, or null when the default is selected. */
  onChange: (path: string | null) => void;
  /** Stacking context for the browse modal, which opens above the release modal. */
  browseOverlayZIndex?: number;
}

const BROWSE_VALUE = '__browse__';
const DEFAULT_VALUE = '__default__';

/**
 * Save-location picker for standalone downloads.
 *
 * The value handed upward is always the *parent* folder. The author folder is
 * appended by the naming template during post-processing, exactly as it is for
 * downloads that never touch this picker — so this only ever swaps the base
 * directory. What is *displayed* is the resolved path (parent + author), so the
 * user sees where the file actually lands rather than a root it never sits in.
 *
 * Consequently, browsing straight into an existing author folder strips that
 * segment back off before storing, or the template would nest a second one.
 * When the template has no author segment, paths are shown and stored literally.
 */
export const SaveLocationPicker = ({
  contentType,
  authorName,
  value,
  onChange,
  browseOverlayZIndex,
}: SaveLocationPickerProps) => {
  const [destinations, setDestinations] = useState<DownloadDestination[]>([]);
  const [createsAuthorFolder, setCreatesAuthorFolder] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);

  // Reload when the content type flips: ebook and audiobook have separate roots
  // and separate templates, so the author-folder answer can differ too.
  useEffect(() => {
    let alive = true;
    setLoadFailed(false);
    const load = async () => {
      try {
        const result = await listDownloadDestinations(contentType);
        if (!alive) return;
        setDestinations(result.destinations);
        setCreatesAuthorFolder(result.createsAuthorFolder);
      } catch {
        if (!alive) return;
        setDestinations([]);
        setCreatesAuthorFolder(false);
        setLoadFailed(true);
      }
    };
    void load();
    return () => {
      alive = false;
    };
  }, [contentType]);

  const defaultPath = useMemo(
    () => destinations.find((d) => d.isDefault)?.path ?? null,
    [destinations],
  );

  /** Parent folder -> the path the file will actually land in. */
  const resolve = useCallback(
    (parent: string) => (createsAuthorFolder ? joinPath(parent, authorName) : parent),
    [createsAuthorFolder, authorName],
  );

  const quickRoots = useMemo(() => destinations.map((d) => d.path), [destinations]);

  // Options are keyed by parent but labelled with the resolved path. A parent
  // chosen via Browse won't be in the configured list, so carry it explicitly
  // rather than letting the select fall back to Default and download elsewhere.
  const options = useMemo(() => {
    const known = destinations.map((d) => ({
      value: d.path,
      label: d.isDefault ? `${resolve(d.path)} — default` : resolve(d.path),
    }));
    if (value && !destinations.some((d) => d.path === value)) {
      known.push({ value, label: resolve(value) });
    }
    return known;
  }, [destinations, value, resolve]);

  const handleSelectChange = useCallback(
    (next: string) => {
      if (next === BROWSE_VALUE) {
        setBrowseOpen(true);
        return;
      }
      onChange(next === DEFAULT_VALUE || next === defaultPath ? null : next);
    },
    [defaultPath, onChange],
  );

  const handleBrowseSelect = useCallback(
    (picked: string) => {
      // Picking the author folder itself yields the same parent as picking its
      // container, so the template never nests a duplicate.
      const parent = createsAuthorFolder ? stripTrailingAuthorName(picked, authorName) : picked;
      onChange(parent === defaultPath ? null : parent);
      setBrowseOpen(false);
    },
    [createsAuthorFolder, authorName, defaultPath, onChange],
  );

  if (loadFailed) {
    return null;
  }

  return (
    <>
      <div className="flex items-center gap-3 text-sm">
        <span className="shrink-0 text-gray-500 dark:text-gray-400">Save to</span>
        <select
          value={value ?? defaultPath ?? DEFAULT_VALUE}
          onChange={(e) => handleSelectChange(e.target.value)}
          className="min-w-0 flex-1 truncate rounded-lg border border-[var(--border-muted)] bg-white/70 px-3 py-2 text-gray-900 focus:outline-none dark:bg-white/10 dark:text-gray-100"
        >
          {options.length === 0 && <option value={DEFAULT_VALUE}>Default</option>}
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
          <option value={BROWSE_VALUE}>Browse…</option>
        </select>
      </div>

      <FolderBrowserModal
        open={browseOpen}
        title={contentType === 'audiobook' ? 'Select audiobook folder' : 'Select ebook folder'}
        initialPath={value ?? defaultPath}
        overlayZIndex={browseOverlayZIndex}
        quickRoots={quickRoots}
        onClose={() => setBrowseOpen(false)}
        onSelect={handleBrowseSelect}
      />
    </>
  );
};
