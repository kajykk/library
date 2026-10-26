'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import type { DataSourceMode } from './dataSource';
import {
  DEFAULT_FILTERS,
  buildActiveChips,
  clearLibraryFilters,
  isLibraryFiltered,
  parseLibraryFilters,
  serializeLibraryFilters,
  type LibraryFilters,
  type LibraryView,
} from './libraryFilters';

const SEARCH_DEBOUNCE_MS = 300;
const VIEW_STORAGE_KEY = 'kb_library_view';

export interface UseLibraryFiltersResult {
  filters: LibraryFilters;
  searchInput: string;
  setSearchInput: (value: string) => void;
  setFilter: <K extends keyof LibraryFilters>(key: K, value: LibraryFilters[K]) => void;
  patchFilters: (next: Partial<LibraryFilters>) => void;
  clearFilters: () => void;
  isFiltered: boolean;
  activeCount: number;
  /** 当前数据源不支持的筛选（本地模式下的 quality），UI 需提示用户 */
  droppedFilters: string[];
}

/**
 * 书架筛选状态 URL 化。
 * 导航一律 replace：点筛选不污染历史栈，后退仍由浏览器负责。
 */
export function useLibraryFilters(mode: DataSourceMode): UseLibraryFiltersResult {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const parsed = useMemo(() => parseLibraryFilters(searchParams), [searchParams]);

  const [storedView, setStoredView] = useState<LibraryView | null>(null);
  useEffect(() => {
    const stored = window.localStorage.getItem(VIEW_STORAGE_KEY);
    if (stored === 'grid' || stored === 'list') setStoredView(stored);
  }, []);

  // 本地模式不支持质量筛选：从生效条件中丢弃，但保留 URL 供 UI 提示/清除
  const filters = useMemo<LibraryFilters>(() => {
    const withMode = mode === 'local' && parsed.quality ? { ...parsed, quality: null } : parsed;
    // URL 的 view 优先；首次进入用 localStorage 里的偏好
    if (!searchParams.get('view') && storedView) {
      return { ...withMode, view: storedView };
    }
    return withMode;
  }, [parsed, mode, storedView, searchParams]);

  const droppedFilters = useMemo(
    () => (mode === 'local' && parsed.quality ? ['quality'] : []),
    [mode, parsed.quality],
  );

  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  const apply = useCallback(
    (next: LibraryFilters) => {
      const qs = serializeLibraryFilters(next).toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname],
  );

  const patchFilters = useCallback(
    (next: Partial<LibraryFilters>) => {
      apply({ ...filtersRef.current, ...next });
    },
    [apply],
  );

  const setFilter = useCallback(
    <K extends keyof LibraryFilters>(key: K, value: LibraryFilters[K]) => {
      if (key === 'view' && (value === 'grid' || value === 'list')) {
        window.localStorage.setItem(VIEW_STORAGE_KEY, value);
      }
      apply({ ...filtersRef.current, [key]: value });
    },
    [apply],
  );

  const clearFilters = useCallback(() => {
    apply(clearLibraryFilters(filtersRef.current));
  }, [apply]);

  // 输入框保持即时响应，300ms 后再写 URL
  const [searchInput, setSearchInput] = useState(parsed.q);
  const lastAppliedQ = useRef(parsed.q);

  useEffect(() => {
    if (parsed.q !== lastAppliedQ.current) {
      lastAppliedQ.current = parsed.q;
      setSearchInput(parsed.q);
    }
  }, [parsed.q]);

  useEffect(() => {
    const trimmed = searchInput.trim();
    if (trimmed === filtersRef.current.q) return;
    const timer = setTimeout(() => {
      lastAppliedQ.current = trimmed;
      patchFilters({ q: trimmed });
    }, SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [searchInput, patchFilters, parsed.q]);

  return {
    filters,
    searchInput,
    setSearchInput,
    setFilter,
    patchFilters,
    clearFilters,
    isFiltered: isLibraryFiltered(filters),
    activeCount: buildActiveChips(filters).length,
    droppedFilters,
  };
}

/** 供测试断言默认值，避免测试自己复制一份常量。 */
export const LIBRARY_FILTER_DEFAULTS = DEFAULT_FILTERS;
