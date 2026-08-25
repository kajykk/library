// @vitest-environment jsdom
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ApiDocument } from '@/lib/api/client';
import { patchDocument, setDocumentTags } from '@/lib/api/client';
import { useDebouncedSave, type SaveState } from '../useDebouncedSave';

vi.mock('@/lib/api/client', () => ({
  patchDocument: vi.fn(),
  setDocumentTags: vi.fn(),
}));

const mockedPatch = vi.mocked(patchDocument);
const mockedTags = vi.mocked(setDocumentTags);

type HookOpts = Parameters<typeof useDebouncedSave>[0];

interface MutableOpts {
  docId: string;
  titleRef: { current: string };
  contentRef: { current: string };
  tagsRef: { current: string[] };
  savedTagsRef: { current: string[] };
}

function makeOpts(): MutableOpts & {
  states: SaveState[];
} & Pick<HookOpts, 'onStateChange'> {
  const states: SaveState[] = [];
  const refs = {
    titleRef: { current: '标题A' },
    contentRef: { current: '正文A' },
    tagsRef: { current: ['t1'] },
    savedTagsRef: { current: ['t1'] },
  };
  return {
    ...refs,
    docId: 'doc-1',
    onStateChange: (s) => states.push(s),
    states,
  };
}

describe('useDebouncedSave', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    mockedPatch.mockReset().mockResolvedValue({ id: 'doc-1' } as ApiDocument);
    mockedTags.mockReset().mockResolvedValue({ id: 'doc-1' } as ApiDocument);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('800ms 防抖窗口内多次 schedule 只触发一次保存', async () => {
    const o = makeOpts();
    const { result } = renderHook(() => useDebouncedSave(o));

    act(() => result.current.schedule());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(300);
    });
    act(() => result.current.schedule());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(799);
    });
    expect(mockedPatch).not.toHaveBeenCalled();
    expect(o.states).toEqual(['dirty', 'dirty']);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(mockedPatch).toHaveBeenCalledTimes(1);
    expect(mockedPatch).toHaveBeenCalledWith('doc-1', { title: '标题A', content: '正文A' });
    // 标签相对快照未变化：不提交标签接口
    expect(mockedTags).not.toHaveBeenCalled();
    expect(o.states.slice(-3)).toEqual(['dirty', 'saving', 'saved']);
  });

  it('flush 失焦强制立即落盘且不重复保存', async () => {
    const o = makeOpts();
    const { result } = renderHook(() => useDebouncedSave(o));

    act(() => result.current.schedule());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    await act(async () => {
      await result.current.flush();
    });
    expect(mockedPatch).toHaveBeenCalledTimes(1);
    expect(mockedPatch).toHaveBeenCalledWith('doc-1', { title: '标题A', content: '正文A' });
    expect(o.states.at(-1)).toBe('saved');

    // 计时器已被 flush 清理，后续推进不再触发
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    // 无 pending 时 flush 是空操作
    await act(async () => {
      await result.current.flush();
    });
    expect(mockedPatch).toHaveBeenCalledTimes(1);
  });

  it('卸载时清掉计时器并补发 pending 保存（读取最新终态）', async () => {
    const o = makeOpts();
    const { result, unmount } = renderHook(() => useDebouncedSave(o));

    o.titleRef.current = '最终标题';
    o.contentRef.current = '最终内容';
    act(() => result.current.schedule());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100);
    });
    expect(mockedPatch).not.toHaveBeenCalled();

    unmount();
    expect(mockedPatch).toHaveBeenCalledWith('doc-1', { title: '最终标题', content: '最终内容' });
    // 卸载清理已 clearTimeout，之后时间推进不会二次保存
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(mockedPatch).toHaveBeenCalledTimes(1);
  });

  it('标签相对已存快照有差异才提交 setDocumentTags 并更新快照', async () => {
    const o = makeOpts();
    o.tagsRef.current = ['t1', 't2'];
    const { result } = renderHook(() => useDebouncedSave(o));

    act(() => result.current.schedule());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(mockedTags).toHaveBeenCalledTimes(1);
    expect(mockedTags).toHaveBeenCalledWith('doc-1', ['t1', 't2']);
    expect(o.savedTagsRef.current).toEqual(['t1', 't2']);

    // 第二次保存且标签未变：不再提交标签接口
    act(() => result.current.schedule());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(mockedPatch).toHaveBeenCalledTimes(2);
    expect(mockedTags).toHaveBeenCalledTimes(1);
  });

  it('保存失败时状态回退 dirty 而非 saved', async () => {
    mockedPatch.mockRejectedValueOnce(new Error('network down'));
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {});
    const o = makeOpts();
    const { result } = renderHook(() => useDebouncedSave(o));

    act(() => result.current.schedule());
    await act(async () => {
      await vi.advanceTimersByTimeAsync(800);
    });
    expect(o.states).toEqual(['dirty', 'saving', 'dirty']);
    errSpy.mockRestore();
  });
});
