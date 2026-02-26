import { useCallback } from 'react';
import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import { AppConfig, Book, ContentType, OpenReleasesOptions, Release, ReleasePrimaryAction } from '../types';
import { getReleases } from '../services/api';
import { recordMonitoredAutoSearchAttempt } from '../services/monitoredApi';
import { ActivityItem } from '../components/activity/activityTypes';
import { policyTrace } from '../utils/policyTrace';
import { getUnreleasedUntilDateForAutoSearch } from '../utils/monitoredAutoSearchUtils';
import { BatchAutoStats, getReleaseMatchScore } from './useMonitoredState';

export type AutoSearchOutcome = 'queued' | 'skip' | 'fallback';

interface UseMonitoredAutoSearchParams {
  config: AppConfig | null;
  username: string | undefined;
  showToast: (message: string, type: 'info' | 'success' | 'error', sticky?: boolean) => string | null;
  removeToast: (id: string) => void;
  setTransientDownloadActivityItems: Dispatch<SetStateAction<ActivityItem[]>>;
  batchAutoStatsRef: MutableRefObject<Record<string, BatchAutoStats>>;
  handleReleaseDownload: (book: Book, release: Release, contentType: ContentType, monitoredEntityId?: number | null) => Promise<void>;
}

export function useMonitoredAutoSearch({
  config,
  username,
  showToast,
  removeToast,
  setTransientDownloadActivityItems,
  batchAutoStatsRef,
  handleReleaseDownload,
}: UseMonitoredAutoSearchParams) {
  const executeAutoSearch = useCallback(async (
    book: Book,
    normalizedContentType: ContentType,
    monitoredEntityId: number | null | undefined,
    actionOverride: ReleasePrimaryAction | undefined,
    options: OpenReleasesOptions | undefined,
  ): Promise<AutoSearchOutcome> => {
    const isForcedAutoAction = actionOverride === 'auto_search_download';
    const batchAuto = options?.batchAutoDownload;
    const isBatchAutoSearch = Boolean(isForcedAutoAction && batchAuto);
    const suppressPerBookAutoSearchToasts = Boolean(options?.suppressPerBookAutoSearchToasts || isBatchAutoSearch);
    const batchMasterActivityId = batchAuto
      ? `auto-search-batch:${batchAuto.batchId}:${normalizedContentType}`
      : null;
    const batchStatsKey = batchMasterActivityId || '';

    const updateBatchMasterActivity = (params: {
      statusDetail: string;
      progress: number;
      visualStatus?: ActivityItem['visualStatus'];
      statusLabel?: string;
      progressAnimated?: boolean;
    }) => {
      if (!isBatchAutoSearch || !batchMasterActivityId || !batchAuto) {
        return;
      }
      const masterActivityId = batchMasterActivityId;
      setTransientDownloadActivityItems((prev) => {
        const next = [...prev];
        const existingIndex = next.findIndex((item) => item.id === masterActivityId);
        const baseItem: ActivityItem = existingIndex >= 0
          ? next[existingIndex]
          : {
            id: masterActivityId,
            kind: 'download',
            visualStatus: 'resolving',
            title: batchAuto.contentType === 'audiobook' ? 'Batch audiobook auto-download' : 'Batch ebook auto-download',
            author: 'Monitored books',
            preview: book.preview,
            metaLine: [
              batchAuto.contentType === 'audiobook' ? 'AUDIOBOOK' : 'EBOOK',
              username || undefined,
            ].filter(Boolean).join(' · '),
            statusLabel: 'Resolving',
            statusDetail: '',
            progress: 10,
            progressAnimated: true,
            timestamp: Date.now() / 1000,
            username: username || undefined,
          };
        const updated: ActivityItem = {
          ...baseItem,
          preview: book.preview || baseItem.preview,
          visualStatus: params.visualStatus || baseItem.visualStatus,
          statusLabel: params.statusLabel || baseItem.statusLabel,
          statusDetail: params.statusDetail,
          progress: params.progress,
          progressAnimated: params.progressAnimated ?? true,
          timestamp: Date.now() / 1000,
        };
        if (existingIndex >= 0) {
          next[existingIndex] = updated;
        } else {
          next.unshift(updated);
        }
        return next;
      });
    };

    const autoDownloadMinMatchScore = typeof config?.auto_download_min_match_score === 'number'
      ? config.auto_download_min_match_score
      : 75;

    if (isBatchAutoSearch && batchAuto && batchStatsKey) {
      if (!batchAutoStatsRef.current[batchStatsKey]) {
        batchAutoStatsRef.current[batchStatsKey] = {
          total: batchAuto.total,
          queued: 0,
          skipped: 0,
          failed: 0,
          started: false,
          contentType: batchAuto.contentType,
        };
      }
      if (!batchAutoStatsRef.current[batchStatsKey].started) {
        batchAutoStatsRef.current[batchStatsKey].started = true;
        showToast('Batch processing downloads started…', 'info');
      }
      updateBatchMasterActivity({
        statusDetail: `Processing book ${batchAuto.index}/${batchAuto.total} (pre-process)…`,
        progress: Math.max(5, Math.min(95, Math.round(((batchAuto.index - 1) / Math.max(1, batchAuto.total)) * 100))),
        visualStatus: 'resolving',
        statusLabel: 'Resolving',
        progressAnimated: true,
      });
    }

    if (!book.provider || !book.provider_id) {
      if (!suppressPerBookAutoSearchToasts || !isForcedAutoAction) {
        showToast(
          isForcedAutoAction
            ? 'Auto search requires provider-linked metadata. Skipping this selected book.'
            : 'Auto search requires provider-linked book metadata. Opening interactive search instead.',
          'info'
        );
      }
      if (isBatchAutoSearch && batchStatsKey) {
        batchAutoStatsRef.current[batchStatsKey].skipped += 1;
      }
      return isForcedAutoAction ? 'skip' : 'fallback';
    }

    const unreleasedUntil = getUnreleasedUntilDateForAutoSearch(book);
    if (unreleasedUntil) {
      const unreleasedMessage = `Book is unreleased until ${unreleasedUntil}`;
      policyTrace('universal.get:auto_search:skip_unreleased', {
        bookId: book.id,
        contentType: normalizedContentType,
        unreleasedUntil,
      });
      void recordMonitoredAutoSearchAttempt({
        monitoredEntityId,
        provider: book.provider,
        providerBookId: book.provider_id,
        contentType: normalizedContentType,
        status: 'not_released',
        errorMessage: unreleasedMessage,
      });
      if (!suppressPerBookAutoSearchToasts || !isForcedAutoAction) {
        showToast(`${unreleasedMessage}. Skipping auto-search.`, 'info');
      }
      if (isBatchAutoSearch && batchAuto && batchStatsKey) {
        batchAutoStatsRef.current[batchStatsKey].skipped += 1;
        updateBatchMasterActivity({
          statusDetail: `Skipped ${batchAuto.index}/${batchAuto.total} (unreleased)`,
          progress: Math.max(10, Math.min(95, Math.round((batchAuto.index / Math.max(1, batchAuto.total)) * 100))),
          visualStatus: 'resolving',
          statusLabel: 'Resolving',
          progressAnimated: true,
        });
      }
      return 'skip';
    }

    let processingActivityId: string | null = null;
    let processingToastId: string | null = null;
    if (!isBatchAutoSearch) {
      const nonBatchProcessingId = `auto-search:${book.id}:${Date.now()}`;
      processingActivityId = nonBatchProcessingId;
      setTransientDownloadActivityItems((prev) => [
        {
          id: nonBatchProcessingId,
          kind: 'download',
          visualStatus: 'resolving',
          title: book.title || 'Unknown title',
          author: book.author || 'Unknown author',
          preview: book.preview,
          metaLine: [
            normalizedContentType === 'audiobook' ? 'AUDIOBOOK' : 'EBOOK',
            book.format?.toUpperCase(),
            username || undefined,
          ].filter(Boolean).join(' · '),
          statusLabel: 'Resolving',
          statusDetail: `Processing releases for ${book.title || 'selected book'}...`,
          progress: 15,
          progressAnimated: true,
          timestamp: Date.now() / 1000,
          username: username || undefined,
        },
        ...prev,
      ]);
      processingToastId = showToast(`Processing releases for ${book.title || 'selected book'}...`, 'info', true);
    }

    try {
      policyTrace('universal.get:auto_search:start', {
        bookId: book.id,
        contentType: normalizedContentType,
        minMatchScore: autoDownloadMinMatchScore,
      });
      const bookLanguages = config?.book_languages || [];
      const defaultLanguageCodes = config?.default_language?.length
        ? config.default_language
        : [bookLanguages[0]?.code || 'en'];

      const response = await getReleases(
        book.provider,
        book.provider_id,
        undefined,
        undefined,
        undefined,
        false,
        defaultLanguageCodes,
        normalizedContentType,
      );

      const bestRelease = [...(response.releases || [])].sort((a, b) => {
        return (getReleaseMatchScore(b) || 0) - (getReleaseMatchScore(a) || 0);
      })[0];

      const bestMatchScore = bestRelease ? getReleaseMatchScore(bestRelease) : null;

      if (bestRelease && bestMatchScore !== null && bestMatchScore >= autoDownloadMinMatchScore) {
        policyTrace('universal.get:auto_search:queue_best', {
          bookId: book.id,
          releaseId: bestRelease.source_id,
          source: bestRelease.source,
          contentType: normalizedContentType,
          matchScore: bestMatchScore,
        });
        if (!suppressPerBookAutoSearchToasts) {
          showToast(`Starting download (match ${bestMatchScore})`, 'info');
        }
        await handleReleaseDownload(book, bestRelease, normalizedContentType, monitoredEntityId ?? null);
        if (!suppressPerBookAutoSearchToasts) {
          showToast(`Queued top match (score ${bestMatchScore})`, 'success');
        }
        if (isBatchAutoSearch && batchAuto && batchStatsKey) {
          batchAutoStatsRef.current[batchStatsKey].queued += 1;
          updateBatchMasterActivity({
            statusDetail: `Queued ${batchAuto.index}/${batchAuto.total} for download`,
            progress: Math.max(10, Math.min(95, Math.round((batchAuto.index / Math.max(1, batchAuto.total)) * 100))),
            visualStatus: 'locating',
            statusLabel: 'Locating',
            progressAnimated: true,
          });
        }
        return 'queued';
      }

      policyTrace('universal.get:auto_search:fallback_interactive', {
        bookId: book.id,
        contentType: normalizedContentType,
        reason: bestRelease ? 'below_threshold' : 'no_results',
        bestMatchScore,
        minMatchScore: autoDownloadMinMatchScore,
      });
      void recordMonitoredAutoSearchAttempt({
        monitoredEntityId,
        provider: book.provider,
        providerBookId: book.provider_id,
        contentType: normalizedContentType,
        status: bestRelease ? 'below_cutoff' : 'no_match',
        source: bestRelease?.source,
        sourceId: bestRelease?.source_id,
        releaseTitle: bestRelease?.title,
        matchScore: bestMatchScore,
      });
      if (!suppressPerBookAutoSearchToasts || !isForcedAutoAction) {
        showToast(
          isForcedAutoAction
            ? 'No release met auto-download cutoff for selected book.'
            : 'No release met auto-download cutoff. Opening interactive search.',
          'info'
        );
      }
      if (isBatchAutoSearch && batchStatsKey) {
        batchAutoStatsRef.current[batchStatsKey].skipped += 1;
      }
      return isForcedAutoAction ? 'skip' : 'fallback';
    } catch (error) {
      console.error('Auto search + download failed, falling back to interactive search:', error);
      policyTrace('universal.get:auto_search:error', {
        bookId: book.id,
        contentType: normalizedContentType,
        message: error instanceof Error ? error.message : String(error),
      });
      void recordMonitoredAutoSearchAttempt({
        monitoredEntityId,
        provider: book.provider,
        providerBookId: book.provider_id,
        contentType: normalizedContentType,
        status: 'error',
        errorMessage: error instanceof Error ? error.message : String(error),
      });
      if (!suppressPerBookAutoSearchToasts || !isForcedAutoAction) {
        showToast(
          isForcedAutoAction
            ? 'Auto search failed for selected book.'
            : 'Auto search failed. Opening interactive search.',
          'error'
        );
      }
      if (isBatchAutoSearch && batchStatsKey) {
        batchAutoStatsRef.current[batchStatsKey].failed += 1;
      }
      return isForcedAutoAction ? 'skip' : 'fallback';
    } finally {
      if (processingActivityId) {
        setTransientDownloadActivityItems((prev) => prev.filter((item) => item.id !== processingActivityId));
      }
      if (processingToastId) {
        removeToast(processingToastId);
      }
      if (isBatchAutoSearch && batchAuto && batchStatsKey && batchAuto.index >= batchAuto.total) {
        const batchStats = batchAutoStatsRef.current[batchStatsKey];
        if (batchStats) {
          const total = Math.max(1, batchStats.total || 0);
          const processed = batchStats.queued + batchStats.skipped + batchStats.failed;
          const completedLabel = batchStats.failed > 0 ? 'Error' : 'Complete';
          const completedVisualStatus: ActivityItem['visualStatus'] = batchStats.failed > 0 ? 'error' : 'complete';
          updateBatchMasterActivity({
            statusDetail: `${batchStats.queued}/${total} queued · ${batchStats.skipped} skipped · ${batchStats.failed} failed`,
            progress: Math.max(95, Math.min(100, Math.round((processed / total) * 100))),
            visualStatus: completedVisualStatus,
            statusLabel: completedLabel,
            progressAnimated: false,
          });
          showToast(
            `Batch pre-processing finished: ${batchStats.queued} queued, ${batchStats.skipped} skipped, ${batchStats.failed} failed.`,
            batchStats.failed > 0 ? 'error' : 'success'
          );
          delete batchAutoStatsRef.current[batchStatsKey];
        }
      }
    }
  }, [
    config,
    username,
    showToast,
    removeToast,
    setTransientDownloadActivityItems,
    batchAutoStatsRef,
    handleReleaseDownload,
  ]);

  return { executeAutoSearch };
}
