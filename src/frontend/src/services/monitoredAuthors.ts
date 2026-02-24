import { deleteMonitoredEntity } from './api';

export interface DeleteMonitoredAuthorsResult {
  successfulIds: number[];
  failedIds: number[];
}

export const deleteMonitoredAuthorsByIds = async (entityIds: number[]): Promise<DeleteMonitoredAuthorsResult> => {
  const uniqueIds = Array.from(new Set(entityIds.filter((id) => Number.isFinite(id) && id > 0)));
  if (uniqueIds.length === 0) {
    return { successfulIds: [], failedIds: [] };
  }

  const results = await Promise.allSettled(uniqueIds.map((id) => deleteMonitoredEntity(id)));
  const successfulIds: number[] = [];
  const failedIds: number[] = [];

  results.forEach((result, index) => {
    if (result.status === 'fulfilled') {
      successfulIds.push(uniqueIds[index]);
    } else {
      failedIds.push(uniqueIds[index]);
    }
  });

  return { successfulIds, failedIds };
};
