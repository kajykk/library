'use client';

import { useQuery, useQueryClient } from '@tanstack/react-query';
import { resolveMode, type DataSourceMode } from './dataSource';

export const DATA_SOURCE_MODE_QUERY_KEY = ['dataSourceMode'] as const;

/**
 * 数据源模式共享 Hook：用 React Query 缓存并去重 resolveMode()，
 * 替代各页面手写的 useState+useEffect 模式解析状态机。
 *
 * - mode === null：解析尚未完成，调用方应渲染加载骨架而非"空态"，
 *   避免闪现「还没有笔记」之类的错误占位。
 * - mode：'api' | 'local'。
 *
 * 设置页修改后端配置后需调用 invalidateDataSourceMode() 使缓存失效。
 */
export function useDataSourceMode(): { mode: DataSourceMode | null; isResolving: boolean } {
  const query = useQuery({
    queryKey: DATA_SOURCE_MODE_QUERY_KEY,
    queryFn: () => resolveMode(),
    staleTime: Infinity,
    gcTime: Infinity,
  });
  return {
    mode: query.data ?? null,
    isResolving: query.isPending,
  };
}

/** 重新探测数据源模式（设置页保存配置后调用） */
export async function invalidateDataSourceMode(): Promise<void> {
  // 先清掉 dataSource 内部的模块级缓存，再失效 React Query 缓存
  await import('./dataSource').then((m) => m.resetModeCache());
}

export function useDataSourceModeInvalidator(): () => void {
  const queryClient = useQueryClient();
  return () => {
    void invalidateDataSourceMode();
    void queryClient.invalidateQueries({ queryKey: DATA_SOURCE_MODE_QUERY_KEY });
  };
}
