import { describe, expect, it, vi } from 'vitest';

import type { QueueSelectionsRequest } from '../hooks/useReleaseSelection';
import type { Book, Release } from '../types';
import { runQueueSelections, type QueueSelectionsDeps } from '../utils/queueSelections';

const book = { id: 'b1', title: 'T', author: 'A', provider: 'manual', provider_id: 'b1' } as Book;
const rel = (id: string): Release => ({ source: 's', source_id: id, title: `R${id}` }) as Release;

const mkDeps = (over: Partial<QueueSelectionsDeps> = {}): QueueSelectionsDeps => ({
  executeReleaseDownload: vi.fn().mockResolvedValue(undefined),
  openRequestConfirmation: vi.fn(),
  showToast: vi.fn(),
  ...over,
});

const req = (
  items: QueueSelectionsRequest['items'],
  organize = true,
  multiBook = false,
): QueueSelectionsRequest => ({
  items,
  organize,
  multiBook,
});

const optionsOfCall = (deps: QueueSelectionsDeps, call = 0) =>
  vi.mocked(deps.executeReleaseDownload).mock.calls[call][4];

describe('runQueueSelections', () => {
  it('queues a standalone download with SAVE TO options + toast', async () => {
    const deps = mkDeps();
    await runQueueSelections(
      book,
      req([
        {
          contentType: 'ebook',
          release: rel('a'),
          mode: 'download',
          root: '/books',
          preview: '/books/A/x.epub',
          bookPlan: null,
        },
      ]),
      deps,
    );
    expect(deps.executeReleaseDownload).toHaveBeenCalledTimes(1);
    // Options carry the standalone SAVE TO choice (monitoredEntityId undefined → standalone)
    // and no pack fields for a plain single-book pick.
    expect(optionsOfCall(deps)).toEqual({
      monitoredEntityId: undefined,
      standalone: { saveLocation: '/books', organize: true },
    });
    expect(deps.showToast).toHaveBeenCalledWith('Queued ebook → /books/A/', 'success');
    expect(deps.openRequestConfirmation).not.toHaveBeenCalled();
  });

  it('sends no standalone options for monitored-entity downloads', async () => {
    const deps = mkDeps({ monitoredEntityId: 5 });
    await runQueueSelections(
      book,
      req([
        {
          contentType: 'ebook',
          release: rel('a'),
          mode: 'download',
          root: '/books',
          preview: null,
          bookPlan: null,
        },
      ]),
      deps,
    );
    expect(optionsOfCall(deps)).toEqual({ monitoredEntityId: 5, standalone: null });
  });

  it('attaches an approved multi-book plan to the download options + toast', async () => {
    const deps = mkDeps();
    const bookPlan = [
      { title: 'Leviathan Wakes', series_position: 1, year: 2011, files: ['1.m4b'] },
      { title: 'Caliban’s War', series_position: 2, year: 2012, files: ['2.m4b'] },
    ];
    await runQueueSelections(
      book,
      req([
        {
          contentType: 'audiobook',
          release: rel('a'),
          mode: 'download',
          root: '/audio',
          preview: '/audio/A/Series/x.m4b',
          bookPlan,
        },
      ]),
      deps,
    );
    expect(optionsOfCall(deps)).toEqual({
      monitoredEntityId: undefined,
      standalone: { saveLocation: '/audio', organize: true },
      bookPlan,
    });
    expect(deps.showToast).toHaveBeenCalledWith(
      'Queued audiobook (2 books) → /audio/A/Series/',
      'success',
    );
  });

  it('forwards the header multi-book toggle as a heuristic split', async () => {
    const deps = mkDeps({ monitoredEntityId: 5 });
    await runQueueSelections(
      book,
      req(
        [
          {
            contentType: 'ebook',
            release: rel('a'),
            mode: 'download',
            root: null,
            preview: null,
            bookPlan: null,
          },
        ],
        true,
        true,
      ),
      deps,
    );
    expect(optionsOfCall(deps)).toEqual({
      monitoredEntityId: 5,
      standalone: null,
      multiBook: true,
    });
  });

  it('aggregates request_release items into one confirmation, no toast', async () => {
    const deps = mkDeps();
    await runQueueSelections(
      book,
      req([
        {
          contentType: 'ebook',
          release: rel('a'),
          mode: 'request_release',
          root: null,
          preview: null,
          bookPlan: null,
        },
        {
          contentType: 'audiobook',
          release: rel('b'),
          mode: 'request_release',
          root: null,
          preview: null,
          bookPlan: null,
        },
      ]),
      deps,
    );
    expect(deps.executeReleaseDownload).not.toHaveBeenCalled();
    expect(deps.openRequestConfirmation).toHaveBeenCalledTimes(1);
    const [primary, extra] = vi.mocked(deps.openRequestConfirmation).mock.calls[0];
    expect(primary.context.content_type).toBe('ebook');
    expect(extra).toHaveLength(1);
  });

  it('handles a mixed download + request selection', async () => {
    const deps = mkDeps();
    await runQueueSelections(
      book,
      req([
        {
          contentType: 'ebook',
          release: rel('a'),
          mode: 'download',
          root: '/books',
          preview: '/books/A/x.epub',
          bookPlan: null,
        },
        {
          contentType: 'audiobook',
          release: rel('b'),
          mode: 'request_release',
          root: null,
          preview: null,
          bookPlan: null,
        },
      ]),
      deps,
    );
    expect(deps.executeReleaseDownload).toHaveBeenCalledTimes(1);
    expect(deps.openRequestConfirmation).toHaveBeenCalledTimes(1);
    expect(deps.showToast).toHaveBeenCalledTimes(1);
  });
});
