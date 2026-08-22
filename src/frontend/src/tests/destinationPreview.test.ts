import { describe, expect, it } from 'vitest';

import {
  buildDestinationPreview,
  derivePrimaryTitle,
  formatSeriesPosition,
  splitTemplateSegments,
  stripAuthorPrefix,
  stripSeriesFolderSegment,
} from '../utils/destinationPreview';

// The one-off download always files under <root>/<Author>; the template is
// applied INSIDE the author folder (so it's passed {Author}-stripped).
const ORGANIZE = '{Series}/{Series} {SeriesPosition} - {Title} ({Year})';

const withSeries = {
  author: 'Megan E. O’Keefe',
  title: 'Velocity Weapon',
  year: '2019',
  series_name: 'The Protectorate',
  series_position: 1,
};
const noSeries = { author: 'Megan E. O’Keefe', title: 'Velocity Weapon', year: '2019' };

describe('destinationPreview', () => {
  it('files under root/Author and renders the template inside', () => {
    const p = buildDestinationPreview({
      root: '/books/ebooks/fiction/',
      template: ORGANIZE,
      book: withSeries,
      contentType: 'ebook',
      renderMode: 'organize',
      releaseFormat: 'EPUB',
    });
    expect(p.directory).toBe('/books/ebooks/fiction/Megan E. O’Keefe/The Protectorate');
    expect(p.filename).toBe('The Protectorate 1 - Velocity Weapon (2019).epub');
    expect(p.full).toBe(
      '/books/ebooks/fiction/Megan E. O’Keefe/The Protectorate/The Protectorate 1 - Velocity Weapon (2019).epub',
    );
  });

  it('collapses the empty series cleanly (no dangling separator)', () => {
    const p = buildDestinationPreview({
      root: '/books/ebooks/fiction',
      template: ORGANIZE,
      book: noSeries,
      contentType: 'ebook',
      renderMode: 'organize',
    });
    // Author folder, no series folder, and no leftover " - " prefix.
    expect(p.directory).toBe('/books/ebooks/fiction/Megan E. O’Keefe');
    expect(p.filename).toBe('Velocity Weapon (2019).epub');
  });

  it('renders none mode as the original filename in the author folder', () => {
    const p = buildDestinationPreview({
      root: '/books/ebooks/fiction',
      template: '',
      book: noSeries,
      contentType: 'ebook',
      renderMode: 'none',
    });
    expect(p.directory).toBe('/books/ebooks/fiction/Megan E. O’Keefe');
    expect(p.filename).toBe('⟨original filename⟩.epub');
  });

  it('does not double the author folder when the root already ends in it', () => {
    const p = buildDestinationPreview({
      root: '/books/ebooks/fiction/Megan E. O’Keefe',
      template: ORGANIZE,
      book: noSeries,
      contentType: 'ebook',
      renderMode: 'organize',
    });
    expect(p.directory).toBe('/books/ebooks/fiction/Megan E. O’Keefe');
  });

  it('defaults the extension per content type', () => {
    const ab = buildDestinationPreview({
      root: '/b',
      template: '{Title} ({Year})',
      book: noSeries,
      contentType: 'audiobook',
      renderMode: 'organize',
    });
    expect(ab.filename.endsWith('.m4b')).toBe(true);
    expect(ab.directory).toBe('/b/Megan E. O’Keefe');
  });

  it('stripAuthorPrefix removes only a leading {Author}/ segment', () => {
    expect(stripAuthorPrefix('{Author}/{Series}/{Title}')).toBe('{Series}/{Title}');
    expect(stripAuthorPrefix('{author}/{Title}')).toBe('{Title}');
    expect(stripAuthorPrefix('{Series}/{Title}')).toBe('{Series}/{Title}');
    expect(stripAuthorPrefix('{Title} - {Author}')).toBe('{Title} - {Author}');
    // Parity with backend strip_author_prefix: internal brace whitespace is NOT stripped.
    expect(stripAuthorPrefix('{ Author }/{Title}')).toBe('{ Author }/{Title}');
  });

  it('stripSeriesFolderSegment drops the {Series} folder but never the filename', () => {
    expect(stripSeriesFolderSegment(ORGANIZE)).toBe('{Series} {SeriesPosition} - {Title} ({Year})');
    expect(stripSeriesFolderSegment('{Series}/{Title}/{OriginalName}')).toBe(
      '{Title}/{OriginalName}',
    );
    expect(stripSeriesFolderSegment('{Series}')).toBe('{Series}');
  });

  it('splits on slashes outside braces and normalises {Series/}', () => {
    expect(splitTemplateSegments('{Series/}{Title}{ - Part }{PartNumber}')).toEqual([
      '{Series}',
      '{Title}{ - Part }{PartNumber}',
    ]);
  });

  it('mirrors the backend series-position and primary-title formatting', () => {
    expect(formatSeriesPosition(1)).toBe('1');
    expect(formatSeriesPosition(1.5)).toBe('1.5');
    expect(formatSeriesPosition(undefined)).toBe('');
    expect(derivePrimaryTitle('Mistborn: The Final Empire', 'The Final Empire')).toBe('Mistborn');
  });
});
