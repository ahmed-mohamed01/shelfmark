// oxlint-disable-next-line no-restricted-imports -- named sync hook: mirrors the /api/download-destinations fetch into state
import { useEffect, useMemo, useState } from 'react';

import {
  listDownloadDestinations,
  type DownloadDestination,
  type DownloadDestinationLayout,
} from '../services/monitoredApi';
import type { ContentType } from '../types';

interface UseDestinationLayoutParams {
  contentType: ContentType;
  /** Author of the book; lets the server flag roots that already file this author. */
  authorName: string;
  /** Skip fetching (e.g. the modal is closed or the step is unavailable). */
  enabled?: boolean;
}

export interface DestinationLayoutState {
  destinations: DownloadDestination[];
  /** The configured default root for this content type, once loaded. */
  defaultPath: string | null;
  /** The library layout one-off downloads get; null until loaded or on failure. */
  layout: DownloadDestinationLayout | null;
  loading: boolean;
  failed: boolean;
}

const EMPTY: DownloadDestination[] = [];

/**
 * Roots + library layout for one content type, from `/api/download-destinations`.
 * Reloads when the content type or author changes — ebooks and audiobooks have
 * separate roots and templates, and the author-folder probe is per author.
 */
export const useDestinationLayout = ({
  contentType,
  authorName,
  enabled = true,
}: UseDestinationLayoutParams): DestinationLayoutState => {
  const [destinations, setDestinations] = useState<DownloadDestination[]>(EMPTY);
  const [layout, setLayout] = useState<DownloadDestinationLayout | null>(null);
  const [loading, setLoading] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (!enabled) return undefined;
    let alive = true;
    setLoading(true);
    setFailed(false);
    listDownloadDestinations(contentType, authorName)
      .then((result) => {
        if (!alive) return;
        setDestinations(result.destinations);
        setLayout(result.layout);
        setLoading(false);
      })
      .catch(() => {
        if (!alive) return;
        setDestinations(EMPTY);
        setLayout(null);
        setLoading(false);
        setFailed(true);
      });
    return () => {
      alive = false;
    };
  }, [contentType, authorName, enabled]);

  const defaultPath = useMemo(
    () => destinations.find((d) => d.isDefault)?.path ?? null,
    [destinations],
  );

  return { destinations, defaultPath, layout, loading, failed };
};
