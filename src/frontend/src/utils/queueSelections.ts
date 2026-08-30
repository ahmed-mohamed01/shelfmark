import type { QueueSelectionsRequest } from '../hooks/useReleaseSelection';
import type {
  MonitoredReleaseDownloadOptions,
  StandaloneDownloadOptions,
} from '../services/monitoredApi';
import type { Book, ContentType, CreateRequestPayload, Release } from '../types';
import {
  buildMetadataBookRequestData,
  buildReleaseDataFromMetadataRelease,
} from './requestPayload';

/**
 * App-local callbacks the queue step needs. Kept as injected deps so the
 * orchestration itself lives in this branch-only module rather than inline in
 * the upstream `App.tsx` (Rule #1) — App passes a thin adapter.
 */
export interface QueueSelectionsDeps {
  /** Undefined for standalone downloads; a number for monitored-entity downloads. */
  monitoredEntityId?: number;
  onBehalfOfUserId?: number;
  executeReleaseDownload: (
    book: Book,
    release: Release,
    contentType: ContentType,
    onBehalfOfUserId?: number,
    options?: MonitoredReleaseDownloadOptions,
  ) => Promise<void>;
  openRequestConfirmation: (
    payload: CreateRequestPayload,
    additional?: CreateRequestPayload[],
    onBehalfOfUserId?: number,
  ) => void;
  showToast: (message: string, type: 'success' | 'error' | 'info') => void;
}

/**
 * Queue whatever the selection modal picked. Each item routes by its resolved
 * mode: `download` → the download queue (standalone ones carry the SAVE TO root
 * + organize flag); `request_release` → aggregated into one request
 * confirmation. Downloads report a toast with the resolved folders.
 */
export const runQueueSelections = async (
  book: Book,
  request: QueueSelectionsRequest,
  deps: QueueSelectionsDeps,
): Promise<void> => {
  const { monitoredEntityId, onBehalfOfUserId } = deps;
  const requestPayloads: CreateRequestPayload[] = [];
  const queued: string[] = [];

  for (const item of request.items) {
    if (item.mode === 'download') {
      // Standalone downloads (no monitored entity) carry the SAVE TO root + the
      // organize flag. The root may still be null if the destinations fetch
      // hasn't resolved; the server then composes the author folder on the
      // default destination, so the author folder is created either way.
      const standalone: StandaloneDownloadOptions | null =
        monitoredEntityId === undefined
          ? { saveLocation: item.root, organize: request.organize }
          : null;
      // Multi-book packs: the split approved in the review panel, or the header
      // toggle forcing a heuristic split for a release that couldn't be inspected.
      const options: MonitoredReleaseDownloadOptions = {
        monitoredEntityId,
        standalone,
        ...(request.multiBook ? { multiBook: true } : {}),
        ...(item.bookPlan ? { bookPlan: item.bookPlan } : {}),
      };
      // eslint-disable-next-line no-await-in-loop -- queue downloads one at a time to preserve order
      await deps.executeReleaseDownload(
        book,
        item.release,
        item.contentType,
        onBehalfOfUserId,
        options,
      );
      const folder = item.preview ? item.preview.slice(0, item.preview.lastIndexOf('/') + 1) : '';
      const what = item.bookPlan
        ? `${item.contentType} (${item.bookPlan.length} books)`
        : item.contentType;
      queued.push(folder ? `${what} → ${folder}` : what);
    } else if (item.mode === 'request_release') {
      const payload: CreateRequestPayload = {
        book_data: buildMetadataBookRequestData(book, item.contentType),
        release_data: buildReleaseDataFromMetadataRelease(book, item.release, item.contentType),
        context: {
          source: item.release.source,
          content_type: item.contentType,
          request_level: 'release' as const,
        },
      };
      requestPayloads.push(
        typeof onBehalfOfUserId === 'number'
          ? { ...payload, on_behalf_of_user_id: onBehalfOfUserId }
          : payload,
      );
    }
  }

  if (queued.length > 0) {
    deps.showToast(`Queued ${queued.join('  ·  ')}`, 'success');
  }
  if (requestPayloads.length > 0) {
    deps.openRequestConfirmation(requestPayloads[0], requestPayloads.slice(1), onBehalfOfUserId);
  }
};
