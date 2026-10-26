'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { resolveDataSourceStatus, type DataSourceStatus } from './dataSource';

export const DATA_SOURCE_STATUS_QUERY_KEY = ['dataSourceStatus'] as const;

const STATUS_STALE_TIME_MS = 30_000;

/**
 * 数据源状态 Hook：与 resolveDataSourceStatus 的 30s 缓存一致。
 * B1 的 DataSourceStatusBar 用它显示当前连的是哪份数据、为何回退。
 */
export function useDataSourceStatus(): {
  status: DataSourceStatus | null;
  isResolving: boolean;
  refresh: () => void;
} {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: DATA_SOURCE_STATUS_QUERY_KEY,
    queryFn: () => resolveDataSourceStatus(),
    staleTime: STATUS_STALE_TIME_MS,
    gcTime: STATUS_STALE_TIME_MS * 2,
  });

  const refresh = () => {
    void (async () => {
      const fresh = await resolveDataSourceStatus(true);
      queryClient.setQueryData(DATA_SOURCE_STATUS_QUERY_KEY, fresh);
    })();
  };

  return {
    status: query.data ?? null,
    isResolving: query.isPending,
    refresh,
  };
}
