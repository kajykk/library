// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DataSourceMode } from '../dataSource';

const nav = vi.hoisted(() => ({
  params: new URLSearchParams(),
  pathname: '/library',
  replace: vi.fn(),
}));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ replace: nav.replace }),
  usePathname: () => nav.pathname,
  useSearchParams: () => nav.params,
}));

import { useLibraryFilters } from '../useLibraryFilters';

function lastQuery(): URLSearchParams {
  const call = nav.replace.mock.calls.at(-1);
  expect(call).toBeDefined();
  const url = String(call?.[0]);
  const index = url.indexOf('?');
  return new URLSearchParams(index >= 0 ? url.slice(index + 1) : '');
}

beforeEach(() => {
  nav.params = new URLSearchParams();
  nav.replace.mockReset();
  window.localStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
});

describe('useLibraryFilters', () => {
  it('setFilter 写入 URL；view 同时落到 localStorage', () => {
    const { result } = renderHook(() => useLibraryFilters('api'));

    act(() => result.current.setFilter('tag', '近代'));
    expect(lastQuery().get('tag')).toBe('近代');
    expect(nav.replace).toHaveBeenLastCalledWith(expect.any(String), { scroll: false });

    act(() => result.current.setFilter('view', 'list'));
    expect(window.localStorage.getItem('kb_library_view')).toBe('list');
    expect(lastQuery().get('view')).toBe('list');
  });

  it('clearFilters 保留排序与视图偏好', () => {
    nav.params = new URLSearchParams(
      'q=x&shelf=reading&tag=t&sort=author_asc&view=list',
    );
    const { result } = renderHook(() => useLibraryFilters('api'));

    act(() => result.current.clearFilters());
    const query = lastQuery();
    expect(query.get('q')).toBeNull();
    expect(query.get('shelf')).toBeNull();
    expect(query.get('tag')).toBeNull();
    expect(query.get('sort')).toBe('author_asc');
    expect(query.get('view')).toBe('list');
  });

  it('本地模式丢弃 quality 并暴露 droppedFilters，切回 API 后恢复', () => {
    nav.params = new URLSearchParams('quality=issues&tag=近代');
    const { result, rerender } = renderHook(
      ({ mode }: { mode: DataSourceMode }) => useLibraryFilters(mode),
      { initialProps: { mode: 'local' as DataSourceMode } },
    );

    expect(result.current.filters.quality).toBeNull();
    expect(result.current.droppedFilters).toEqual(['quality']);
    expect(result.current.filters.tag).toBe('近代');
    expect(result.current.isFiltered).toBe(true);

    rerender({ mode: 'api' });
    expect(result.current.filters.quality).toBe('issues');
    expect(result.current.droppedFilters).toEqual([]);
  });

  it('搜索输入 300ms 防抖后才写 URL', () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useLibraryFilters('api'));

    act(() => result.current.setSearchInput('历史'));
    expect(result.current.searchInput).toBe('历史');
    act(() => vi.advanceTimersByTime(299));
    expect(nav.replace).not.toHaveBeenCalled();

    act(() => vi.advanceTimersByTime(1));
    expect(nav.replace).toHaveBeenCalledTimes(1);
    expect(lastQuery().get('q')).toBe('历史');
  });

  it('外部 URL 变化（浏览器前进/后退）同步搜索框', async () => {
    nav.params = new URLSearchParams('q=旧词');
    const { result, rerender } = renderHook(() => useLibraryFilters('api'));
    expect(result.current.searchInput).toBe('旧词');

    nav.params = new URLSearchParams('q=新词');
    rerender();
    await waitFor(() => expect(result.current.searchInput).toBe('新词'));
  });

  it('URL 无 view 时使用 localStorage 偏好，URL 显式 view 优先', async () => {
    window.localStorage.setItem('kb_library_view', 'list');
    const { result, rerender } = renderHook(() => useLibraryFilters('api'));
    await waitFor(() => expect(result.current.filters.view).toBe('list'));

    nav.params = new URLSearchParams('view=grid');
    rerender();
    expect(result.current.filters.view).toBe('grid');
  });

  it('shelf=recent 走固定排序且不产生 sort 参数', () => {
    nav.params = new URLSearchParams('shelf=recent');
    const { result } = renderHook(() => useLibraryFilters('api'));
    expect(result.current.filters.sort).toBe('created_desc');

    act(() => result.current.setFilter('shelf', 'recent'));
    expect(lastQuery().get('shelf')).toBe('recent');
    expect(lastQuery().get('sort')).toBeNull();
  });
});
