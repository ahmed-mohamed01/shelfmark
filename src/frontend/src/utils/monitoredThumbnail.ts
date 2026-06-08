/**
 * Sized-thumbnail helpers for cover/portrait images served through the local
 * image-cache proxy. Mirrors the backend `/api/monitored/thumb` route +
 * `shelfmark/core/monitored_thumbnails.py`: a proxied `/api/covers/...` URL is
 * rewritten to the resizing route at a requested width; non-proxied URLs (raw
 * external CDN, or covers cache disabled) are returned untouched so the caller
 * can fall back to a plain `src` with no srcset.
 */

const THUMB_WIDTHS = [150, 300, 450] as const;

/** Tiles render at ~120-185px; this maps that to viewport widths for srcset. */
export const THUMB_SIZES = '(max-width: 640px) 33vw, (max-width: 1024px) 20vw, 12vw';

export function isProxiedCover(url?: string | null): url is string {
  return !!url && url.includes('/api/covers/');
}

export function thumbUrl(url: string, width: number): string {
  return `${url.replace('/api/covers/', '/api/monitored/thumb/')}${
    url.includes('?') ? '&' : '?'
  }w=${width}`;
}

export function thumbSrcSet(url: string): string {
  return THUMB_WIDTHS.map((w) => `${thumbUrl(url, w)} ${w}w`).join(', ');
}
