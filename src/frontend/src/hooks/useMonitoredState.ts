import { useState, useEffect, useCallback, useRef, useMemo } from 'react';
import { ActivityItem } from '../components/activity/activityTypes';
import { AppConfig, Book, ContentType, Release, StatusData } from '../types';

export const getReleaseMatchScore = (release: Release): number | null => {
  const raw = release.extra?.match_score;
  return typeof raw === 'number' ? raw : null;
};

interface BatchAutoStats {
  total: number;
  queued: number;
  skipped: number;
  failed: number;
  started: boolean;
  contentType: ContentType;
}

interface UseMonitoredStateParams {
  dismissedActivityKeys: string[];
  currentStatus: StatusData;
  config: AppConfig | null;
}

export function useMonitoredState({ dismissedActivityKeys, currentStatus, config }: UseMonitoredStateParams) {
  const [transientDownloadActivityItems, setTransientDownloadActivityItems] = useState<ActivityItem[]>([]);
  const [showDualGetButtons, setShowDualGetButtons] = useState<boolean>(false);
  const [releaseMonitoredEntityId, setReleaseMonitoredEntityId] = useState<number | null>(null);

  const batchAutoStatsRef = useRef<Record<string, BatchAutoStats>>({});

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
    if (!currentStatus.complete || dismissedDownloadTaskIds.size === 0) {
      return currentStatus;
    }

    const filteredComplete = Object.fromEntries(
      Object.entries(currentStatus.complete).filter(([taskId]) => !dismissedDownloadTaskIds.has(taskId))
    ) as Record<string, Book>;

    if (Object.keys(filteredComplete).length === Object.keys(currentStatus.complete).length) {
      return currentStatus;
    }

    return {
      ...currentStatus,
      complete: filteredComplete,
    };
  }, [currentStatus, dismissedDownloadTaskIds]);

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
    setShowDualGetButtons,
    releaseMonitoredEntityId,
    setReleaseMonitoredEntityId,
    batchAutoStatsRef,
    dismissedDownloadTaskIds,
    isDownloadTaskDismissed,
    statusForButtonState,
    transientOngoingCount,
  };
}
