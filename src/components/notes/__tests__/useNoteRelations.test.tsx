// @vitest-environment jsdom
import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ApiDocument, Backlink } from '@/lib/api/client';
import { getBacklinks, getUnlinkedMentions, listDocuments } from '@/lib/api/client';
import { useNoteRelations } from '../useNoteRelations';

vi.mock('@/lib/api/client', () => ({
  listDocuments: vi.fn(),
  getBacklinks: vi.fn(),
  getUnlinkedMentions: vi.fn(),
}));

const mockedDocs = vi.mocked(listDocuments);
const mockedBacklinks = vi.mocked(getBacklinks);
const mockedMentions = vi.mocked(getUnlinkedMentions);

function makeDoc(partial: Partial<ApiDocument>): ApiDocument {
  return {
    id: '',
    type: 'note',
    title: '',
    author: '',
    publisher: '',
    language: '',
    isbn: '',
    description: '',
    source_url: null,
    file_path: null,
    cover_path: null,
    file_size: 0,
    format: null,
    content: '',
    meta: {},
    collection_id: null,
    read_progress: 0,
    position: null,
    last_read_at: null,
    tags: [],
    created_at: '',
    updated_at: '',
    deleted_at: null,
    ...partial,
  };
}

function makeBacklink(id: string): Backlink {
  return { id, title: `标题-${id}`, type: 'note', snippet: `片段-${id}` };
}

describe('useNoteRelations', () => {
  beforeEach(() => {
    mockedDocs.mockReset().mockResolvedValue([]);
    mockedBacklinks.mockReset().mockResolvedValue([]);
    mockedMentions.mockReset().mockResolvedValue([]);
  });

  it('allTitles 从文档列表派生并过滤当前笔记自身', async () => {
    mockedDocs.mockResolvedValue([
      makeDoc({ id: 'a', title: '当前' }),
      makeDoc({ id: 'b', title: 'B 文档' }),
      makeDoc({ id: 'c', title: 'C 文档' }),
    ]);
    const { result } = renderHook(() => useNoteRelations('a', '当前'));

    await waitFor(() => expect(result.current.allTitles).toHaveLength(2));
    expect(result.current.allTitles).toEqual([
      { id: 'b', title: 'B 文档' },
      { id: 'c', title: 'C 文档' },
    ]);
    // 只拉一次列表，且不带正文
    expect(mockedDocs).toHaveBeenCalledTimes(1);
    expect(mockedDocs).toHaveBeenCalledWith({ include_content: 'false' });
  });

  it('suggestedLinks 按标题包含关系打分、降序排序、截断前 5 并排除自身', async () => {
    mockedDocs.mockResolvedValue([
      makeDoc({ id: 'self', title: 'React 入门', type: 'note' }),
      makeDoc({ id: 'd1', title: 'React 入门指南', type: 'note' }), // 包含完整标题 → 0.9
      makeDoc({ id: 'd2', title: '入门', type: 'book' }), // 标题被当前包含 → 0.9
      makeDoc({ id: 'd3', title: 'Python 教程', type: 'book' }), // 无关 → 0.4
      makeDoc({ id: 'd4', title: 'Go 语言', type: 'book' }),
      makeDoc({ id: 'd5', title: 'Java 核心', type: 'book' }),
      makeDoc({ id: 'd6', title: 'Rust 手册', type: 'book' }),
    ]);
    const { result } = renderHook(() => useNoteRelations('self', 'React 入门'));

    await waitFor(() => expect(result.current.suggestedLinks.length).toBe(5));
    expect(result.current.suggestedLinks.map((l) => l.id)).toEqual(['d1', 'd2', 'd3', 'd4', 'd5']);
    expect(result.current.suggestedLinks[0].score).toBe(0.9);
    expect(result.current.suggestedLinks[1].score).toBe(0.9);
    expect(result.current.suggestedLinks.slice(2).every((l) => l.score === 0.4)).toBe(true);
    expect(result.current.suggestedLinks.every((l) => l.id !== 'self')).toBe(true);
    expect(result.current.suggestedLinks[1].type).toBe('book');
  });

  it('useMemo 缓存：无关重渲染不重算派生值（引用稳定），noteTitle 变化只重算 suggestedLinks', async () => {
    mockedDocs.mockResolvedValue([makeDoc({ id: 'b', title: 'React 入门指南' })]);
    const { result, rerender } = renderHook(
      ({ noteId, noteTitle }: { noteId: string; noteTitle: string }) =>
        useNoteRelations(noteId, noteTitle),
      { initialProps: { noteId: 'a', noteTitle: 'React 入门' } },
    );
    await waitFor(() => expect(result.current.allTitles).toHaveLength(1));

    const titles1 = result.current.allTitles;
    const links1 = result.current.suggestedLinks;
    const docsCalls = mockedDocs.mock.calls.length;

    rerender({ noteId: 'a', noteTitle: 'React 入门' });
    expect(result.current.allTitles).toBe(titles1);
    expect(result.current.suggestedLinks).toBe(links1);
    // effect 依赖 noteId：未重新拉取文档列表
    expect(mockedDocs.mock.calls.length).toBe(docsCalls);

    rerender({ noteId: 'a', noteTitle: '新标题' });
    expect(result.current.allTitles).toBe(titles1);
    expect(result.current.suggestedLinks).not.toBe(links1);
  });

  it('removeUnlinked 将对应文档从未链接提及列表移除', async () => {
    mockedMentions.mockResolvedValue([makeBacklink('u1'), makeBacklink('u2')]);
    const { result } = renderHook(() => useNoteRelations('a', 'T'));

    await waitFor(() => expect(result.current.unlinked).toHaveLength(2));
    act(() => result.current.removeUnlinked('u1'));
    expect(result.current.unlinked.map((u) => u.id)).toEqual(['u2']);
  });

  it('refreshBacklinks 重新拉取反向链接并更新状态', async () => {
    mockedBacklinks.mockResolvedValueOnce([makeBacklink('old')]);
    const { result } = renderHook(() => useNoteRelations('a', 'T'));
    await waitFor(() => expect(result.current.backlinks).toEqual([makeBacklink('old')]));

    mockedBacklinks.mockResolvedValueOnce([makeBacklink('n1'), makeBacklink('n2')]);
    await act(async () => {
      result.current.refreshBacklinks();
    });
    expect(mockedBacklinks).toHaveBeenCalledTimes(2);
    expect(mockedBacklinks).toHaveBeenLastCalledWith('a');
    expect(result.current.backlinks).toEqual([makeBacklink('n1'), makeBacklink('n2')]);
  });
});
