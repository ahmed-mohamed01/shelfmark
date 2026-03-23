import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { ActivityItem } from '../components/activity/activityTypes';
import { AppConfig, Book, ContentType, Release, StatusData } from '../types';

export const getReleaseMatchScore = (release: Release): number | null => {
  const raw = release.extra?.match_score;
  return typeof raw === 'number' ? raw : null;
};

export interface BatchAutoStats {
  total: number;
  queued: number;
  skipped: number;
  skippedExistingFile: number;
  failed: number;
  started: boolean;
  cancelled: boolean;
  contentType: ContentType;
}

interface UseMonitoredStateParams {
  dismissedActivityKeys: string[];
  currentStatus: StatusData;
  activityStatus: StatusData;
  config: AppConfig | null;
}

export function useMonitoredState({ dismissedActivityKeys, currentStatus, activityStatus, config }: UseMonitoredStateParams) {
  const [transientDownloadActivityItems, setTransientDownloadActivityItems] = useState<ActivityItem[]>([]);
  const [showDualGetButtons, setShowDualGetButtons] = useState<boolean>(false);
  const [releaseMonitoredEntityId, setReleaseMonitoredEntityId] = useState<number | null>(null);

  const batchAutoStatsRef = useRef<Record<string, BatchAutoStats>>({});
  const cancelledBatchIdsRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    if (!config) {
      return;
    }
    setShowDualGetButtons(Boolean(config.show_dual_get_buttons));
  }, [config]);

  const dismissedDownloadTaskIds = useMemo(() => {
    const result = new Set<string>();
    for (const key of dismissedActivityKeys) {
      if (typeof key !== 'string' || !key.startsWith('download:')) {
        continue;
      }
      const taskId = key.substring('download:'.length).trim();
      if (taskId) {
        result.add(taskId);
      }
    }
    return result;
  }, [dismissedActivityKeys]);

  const isDownloadTaskDismissed = useCallback((taskId: string) => {
    return dismissedDownloadTaskIds.has(taskId);
  }, [dismissedDownloadTaskIds]);

  const statusForButtonState = useMemo(() => {
    // Merge persisted terminal states (activityStatus.complete) with live states (currentStatus)
    // so "already downloaded" button state works even after the task moves to history.
    const mergedComplete = { ...activityStatus.complete, ...currentStatus.complete };
    const filteredComplete = Object.fromEntries(
      Object.entries(mergedComplete).filter(([taskId]) => !dismissedDownloadTaskIds.has(taskId))
    ) as Record<string, Book>;
    return { ...activityStatus, ...currentStatus, complete: filteredComplete };
  }, [currentStatus, activityStatus, dismissedDownloadTaskIds]);

  const transientOngoingCount = useMemo(() => {
    return transientDownloadActivityItems.filter((item) => (
      item.kind === 'download'
      && (
        item.visualStatus === 'queued'
        || item.visualStatus === 'resolving'
        || item.visualStatus === 'locating'
        || item.visualStatus === 'downloading'
      )
    )).length;
  }, [transientDownloadActivityItems]);

  return {
    transientDownloadActivityItems,
    setTransientDownloadActivityItems,
    showDualGetButtons,
    releaseMonitoredEntityId,
    setReleaseMonitoredEntityId,
    batchAutoStatsRef,
    cancelledBatchIdsRef,
    dismissedDownloadTaskIds,
    isDownloadTaskDismissed,
    statusForButtonState,
    transientOngoingCount,
  };
}
