import { Book } from '../types';

export const extractAutoSearchReleaseDateCandidate = (book: Book): string | null => {
  const explicit = String(book.release_date || '').trim();
  if (explicit) return explicit;

  const fields = Array.isArray(book.display_fields) ? book.display_fields : [];
  for (const field of fields) {
    const label = String(field?.label || '').trim().toLowerCase();
    const value = String(field?.value || '').trim();
    if (!label || !value) continue;
    const isReleaseLabel =
      label.includes('released') ||
      label.includes('release date') ||
      label.includes('publish date') ||
      label.includes('publication date') ||
      label === 'release' ||
      label === 'published' ||
      label === 'publication';
    if (isReleaseLabel) {
      return value;
    }
  }
  return null;
};

export const getUnreleasedUntilDateForAutoSearch = (book: Book): string | null => {
  const rawCandidate = extractAutoSearchReleaseDateCandidate(book);
  if (!rawCandidate) return null;
  const candidate = rawCandidate.trim();
  const today = new Date();
  const currentYear = today.getUTCFullYear();

  if (/^\d{4}$/.test(candidate)) {
    const year = Number.parseInt(candidate, 10);
    if (Number.isFinite(year) && year > currentYear) {
      return `${year}-01-01`;
    }
    return null;
  }

  const token = candidate.split('T', 1)[0].split(' ', 1)[0].trim();
  if (/^\d{4}-\d{2}-\d{2}$/.test(token)) {
    const parsed = new Date(`${token}T00:00:00Z`);
    if (!Number.isNaN(parsed.getTime()) && parsed.getTime() > Date.now()) {
      return token;
    }
    return null;
  }

  if (/^\d{4}-\d{2}$/.test(token)) {
    const parsed = new Date(`${token}-01T00:00:00Z`);
    if (!Number.isNaN(parsed.getTime()) && parsed.getTime() > Date.now()) {
      return `${token}-01`;
    }
    return null;
  }

  const embeddedYear = candidate.match(/\b(19|20)\d{2}\b/);
  if (embeddedYear) {
    const year = Number.parseInt(embeddedYear[0], 10);
    if (Number.isFinite(year) && year > currentYear) {
      return `${year}-01-01`;
    }
  }

  return null;
};
