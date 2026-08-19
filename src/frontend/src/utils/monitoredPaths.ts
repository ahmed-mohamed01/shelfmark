/**
 * Shared path helpers for author-folder path selection.
 *
 * These were written inline in BookMonitorModal and MonitoredPage; this is the
 * canonical copy for new callers. The rule they encode: a picked folder is the
 * *parent* the author folder lives in, so re-picking a path that already ends
 * in the author name must not nest a second one.
 */

export const normalizeAbsolutePath = (value: string): string =>
  (value || '').trim().replace(/\/+$/g, '');

/** Append the author folder to a root. Returns '' for an empty root. */
export const joinPath = (root: string, authorName: string): string => {
  const r = normalizeAbsolutePath(root);
  if (!r) return '';
  const a = (authorName || '').trim();
  return a ? `${r}/${a}` : r;
};

/**
 * Drop a trailing `/<authorName>` so the parent root can be recovered.
 * Used when the user browses straight into the author folder.
 */
export const stripTrailingAuthorName = (fullPath: string, authorName: string): string => {
  const normalized = normalizeAbsolutePath(fullPath);
  const a = (authorName || '').trim();
  if (!normalized || !a) return normalized;
  const suffix = `/${a}`;
  if (normalized.endsWith(suffix)) {
    return normalized.slice(0, -suffix.length) || '/';
  }
  return normalized;
};
