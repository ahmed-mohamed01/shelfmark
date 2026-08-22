/**
 * Resolved-path preview for the destination path builder.
 *
 * The server files a one-off library download under `<root>/<Author>` and
 * applies the organize template from `/api/download-destinations` → `layout`
 * *inside* that folder — the same shape a monitored author's folder + template
 * produces. This module renders that template client-side with the book's
 * real metadata, mirroring shelfmark/core/naming.py, so the user sees the exact
 * path before queueing. Everything files under `<root>/<Author>`; the template
 * (passed {Author}-stripped) renders inside it, and "organize off" for ebooks
 * keeps the original filename (`none` mode).
 */

import type { Book, ContentType } from '../types';
import { normalizeAbsolutePath } from './monitoredPaths';
import { renderNamingTemplate } from './namingTemplatePreview';

export type PreviewBook = Pick<
  Book,
  'author' | 'title' | 'year' | 'series_name' | 'series_position' | 'subtitle' | 'language'
>;

export interface DestinationPreviewInput {
  /** Chosen root; the author folder is appended to it. */
  root: string;
  /** Organize template applied inside the author folder, e.g. `{Series}/{Title}`. */
  template: string;
  book: PreviewBook;
  contentType: ContentType;
  /** 'organize' → render the (author-stripped) template; 'none' → keep the original filename. */
  renderMode: 'organize' | 'none';
  /** Format of the selected release (drives the extension); falls back per content type. */
  releaseFormat?: string | null;
  /** Stand-in for `{OriginalName}` (source filename without extension). */
  releaseTitle?: string | null;
}

export interface DestinationPreview {
  /** Absolute directory the file lands in (no trailing slash). */
  directory: string;
  /** Rendered filename incl. extension. */
  filename: string;
  /** `directory/filename`. */
  full: string;
}

/** Mirrors naming.sanitize_filename — what post-processing does to `{Author}` and every token. */
export const sanitizeFilename = (value: string): string =>
  (value || '')
    .replace(/[\\/:*?"<>|]/g, '_')
    .replace(/^[\s.]+|[\s.]+$/g, '')
    .replace(/_+/g, '_')
    .slice(0, 245);

/** Mirrors naming.format_series_position: integers print without decimals. */
export const formatSeriesPosition = (position: number | null | undefined): string => {
  if (position === null || position === undefined || Number.isNaN(position)) return '';
  return String(position);
};

/** Mirrors naming.derive_primary_title: strip an explicit subtitle suffix. */
export const derivePrimaryTitle = (title: string, subtitle?: string | null): string => {
  const t = (title || '').split(/\s+/).join(' ').trim();
  const s = (subtitle || '').split(/\s+/).join(' ').trim();
  if (!t || !s) return t;
  const escaped = s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = t.match(new RegExp(`^(.+?)(?:\\s*:\\s*|\\s+-\\s+)${escaped}$`, 'i'));
  return match?.[1]?.trim() || t;
};

export const buildPreviewMetadata = ({
  book,
  releaseTitle,
}: Pick<DestinationPreviewInput, 'book' | 'releaseTitle'>): Record<string, string> => ({
  Author: book.author || '',
  Title: book.title || '',
  PrimaryTitle: derivePrimaryTitle(book.title || '', book.subtitle),
  Subtitle: book.subtitle || '',
  Year: book.year || '',
  Language: book.language || '',
  // Series metadata always renders; the "series folder off" toggle drops the
  // {Series} *folder* segment from the template, not the values themselves.
  Series: book.series_name || '',
  SeriesPosition: formatSeriesPosition(book.series_position),
  OriginalName: (releaseTitle || '').trim() || 'Part 01',
  PartNumber: '01',
  User: '',
});

/**
 * Split a template on `/` characters that sit outside brace blocks, so a
 * conditional literal like `{ - Part }` can never be mistaken for a folder
 * boundary. The `{Series/}` spelling is normalised to `{Series}/` first — the
 * server renders both the same way.
 */
export const splitTemplateSegments = (template: string): string[] => {
  const normalised = (template || '').replace(/\{\s*series\s*\/\s*\}/gi, '{Series}/');
  const segments: string[] = [];
  let depth = 0;
  let current = '';
  for (const ch of normalised) {
    if (ch === '{') depth += 1;
    else if (ch === '}') depth = Math.max(0, depth - 1);
    if (ch === '/' && depth === 0) {
      segments.push(current);
      current = '';
      continue;
    }
    current += ch;
  }
  segments.push(current);
  return segments.filter((segment) => segment.trim().length > 0);
};

/** A directory segment that is exactly the series token — the "series folder". */
export const isSeriesFolderSegment = (segment: string): boolean =>
  /^\{\s*series\s*\}$/i.test(segment.trim());

/**
 * Drop a leading `{Author}/` — the author folder is the destination, not the
 * template. Matches the backend `strip_author_prefix` exactly (case-insensitive,
 * no internal brace whitespace) so the preview can't diverge from the real path.
 */
export const stripAuthorPrefix = (template: string): string => {
  const t = (template || '').trim().replace(/^\/+/, '');
  return /^\{author\}\//i.test(t) ? t.replace(/^\{author\}\/+/i, '') : t;
};

/**
 * Drop directory segments that are exactly `{Series}` (the series subfolder),
 * keeping the final filename segment. Mirrors the backend
 * `strip_series_folder_segment`: "series folder off" removes the folder while
 * series metadata still renders in filenames.
 */
export const stripSeriesFolderSegment = (template: string): string => {
  const segments = splitTemplateSegments(template);
  if (segments.length <= 1) return template;
  const dirs = segments.slice(0, -1).filter((segment) => !isSeriesFolderSegment(segment));
  return [...dirs, segments[segments.length - 1]].join('/');
};

export const buildDestinationPreview = (input: DestinationPreviewInput): DestinationPreview => {
  // The author folder is always the destination (skip if the chosen root already
  // ends in it); the template — passed already {Author}-stripped — renders inside.
  const root = normalizeAbsolutePath(input.root);
  const authorFolder = sanitizeFilename(input.book.author || '');
  const base =
    root && authorFolder && root.split('/').pop() !== authorFolder
      ? `${root}/${authorFolder}`
      : root;

  const extension =
    (input.releaseFormat || '').trim().replace(/^\./, '').toLowerCase() ||
    (input.contentType === 'audiobook' ? 'm4b' : 'epub');

  if (input.renderMode === 'none') {
    // Organize off (ebook): the source file keeps its name in the author folder.
    const filename = `⟨original filename⟩.${extension}`;
    return { directory: base, filename, full: `${base}/${filename}` };
  }

  const metadata = buildPreviewMetadata(input);
  const rendered = renderNamingTemplate(input.template, metadata, {
    allowPathSeparators: true,
  }).value;
  const relative = rendered || (input.book.title || 'Untitled').replace(/[\\/:*?"<>|]/g, '_');
  const slash = relative.lastIndexOf('/');
  const relativeDirectory = slash >= 0 ? relative.slice(0, slash) : '';
  const nameBase = slash >= 0 ? relative.slice(slash + 1) : relative;
  const filename = `${nameBase}.${extension}`;
  const directory = [base, relativeDirectory].filter(Boolean).join('/');
  return {
    directory,
    filename,
    full: directory ? `${directory}/${filename}` : filename,
  };
};
