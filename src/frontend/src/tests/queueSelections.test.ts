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

const req = (items: QueueSelectionsRequest['items'], organize = true): QueueSelectionsRequest => ({
  items,
  organize,
});

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
        },
      ]),
      deps,
    );
    expect(deps.executeReleaseDownload).toHaveBeenCalledTimes(1);
    // 8th arg is the standalone options (monitoredEntityId undefined → standalone)
    const standalone = vi.mocked(deps.executeReleaseDownload).mock.calls[0][7];
    expect(standalone).toEqual({ saveLocation: '/books', organize: true });
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
        },
      ]),
      deps,
    );
    expect(vi.mocked(deps.executeReleaseDownload).mock.calls[0][7]).toBeNull();
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
        },
        {
          contentType: 'audiobook',
          release: rel('b'),
          mode: 'request_release',
          root: null,
          preview: null,
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
        },
        {
          contentType: 'audiobook',
          release: rel('b'),
          mode: 'request_release',
          root: null,
          preview: null,
        },
      ]),
      deps,
    );
    expect(deps.executeReleaseDownload).toHaveBeenCalledTimes(1);
    expect(deps.openRequestConfirmation).toHaveBeenCalledTimes(1);
    expect(deps.showToast).toHaveBeenCalledTimes(1);
  });
});
