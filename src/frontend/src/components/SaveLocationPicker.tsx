import { useCallback, useEffect, useMemo, useState } from 'react';

import { DownloadDestination, listDownloadDestinations } from '../services/monitoredApi';
import { FolderBrowserModal } from './FolderBrowserModal';

interface SaveLocationPickerProps {
  /** Which destination set to offer. Follows the modal's ebook/audiobook tab. */
  contentType: 'ebook' | 'audiobook';
  /** Currently chosen path, or null to use the configured default. */
  value: string | null;
  /** Called with the chosen path, or null when the default is selected. */
  onChange: (path: string | null) => void;
  /** Stacking context for the browse modal, which opens above the release modal. */
  browseOverlayZIndex?: number;
}

const BROWSE_VALUE = '__browse__';
const DEFAULT_VALUE = '__default__';

/**
 * Save-location picker for standalone downloads.
 *
 * Monitored downloads resolve their own destination from the author's
 * configured folder, so this is only mounted on the plain search flow. Leaving
 * it on "Default" sends no override at all, which keeps the existing behaviour
 * byte-for-byte for anyone who ignores it.
 */
export const SaveLocationPicker = ({
  contentType,
  value,
  onChange,
  browseOverlayZIndex,
}: SaveLocationPickerProps) => {
  const [destinations, setDestinations] = useState<DownloadDestination[]>([]);
  const [loadFailed, setLoadFailed] = useState(false);
  const [browseOpen, setBrowseOpen] = useState(false);

  // Reload when the content type flips: ebook and audiobook have separate roots.
  useEffect(() => {
    let alive = true;
    setLoadFailed(false);
    const load = async () => {
      try {
        const result = await listDownloadDestinations(contentType);
        if (!alive) return;
        setDestinations(result);
      } catch {
        if (!alive) return;
        setDestinations([]);
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

  const quickRoots = useMemo(() => destinations.map((d) => d.path), [destinations]);

  // A path picked via Browse won't be in the dropdown list, so add it rather
  // than silently falling back to Default and downloading to the wrong place.
  const options = useMemo(() => {
    const known = destinations.map((d) => ({
      value: d.path,
      label: d.isDefault ? `Default (${d.path})` : d.path,
    }));
    if (value && !destinations.some((d) => d.path === value)) {
      known.push({ value, label: value });
    }
    return known;
  }, [destinations, value]);

  const handleSelectChange = useCallback(
    (next: string) => {
      if (next === BROWSE_VALUE) {
        setBrowseOpen(true);
        return;
      }
      if (next === DEFAULT_VALUE || next === defaultPath) {
        onChange(null);
        return;
      }
      onChange(next);
    },
    [defaultPath, onChange],
  );

  if (loadFailed) {
    return null;
  }

  return (
    <>
      <div className="flex items-center gap-2 text-sm">
        <span className="shrink-0 text-gray-500 dark:text-gray-400">Save to</span>
        <select
          value={value ?? defaultPath ?? DEFAULT_VALUE}
          onChange={(e) => handleSelectChange(e.target.value)}
          className="min-w-0 flex-1 truncate rounded-full border border-[var(--border-muted)] bg-white/70 px-3 py-1.5 text-gray-900 focus:outline-none dark:bg-white/10 dark:text-gray-100"
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
        onSelect={(path) => {
          onChange(path === defaultPath ? null : path);
          setBrowseOpen(false);
        }}
      />
    </>
  );
};
