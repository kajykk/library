// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { Book } from '../db';

vi.mock('../db', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../db')>();
  return {
    ...actual,
    initDB: vi.fn().mockResolvedValue(undefined),
    getBooks: vi.fn(),
  };
});

import { getBooks } from '../db';
import { loadLibraryPage, resetModeCache, sortLocalBooks } from '../dataSource';

function book(overrides: Partial<Book> = {}): Book {
  return {
    id: 'b1',
    title: '默认标题',
    author: '默认作者',
    publisher: '',
    category: '未分类',
    format: 'epub',
    filePath: 'b1',
    coverPath: null,
    description: '',
    addedAt: 1,
    lastReadAt: null,
    readProgress: 0,
    lastPosition: null,
    totalPages: 0,
    fileSize: 100,
    language: 'zh',
    isbn: '',
    tags: [],
    ...overrides,
  };
}

const getBooksMock = vi.mocked(getBooks);

beforeEach(() => {
  resetModeCache();
  window.localStorage.clear();
  getBooksMock.mockReset();
});

describe('sortLocalBooks', () => {
  it('created_desc 以 addedAt 倒序，同值用 id 稳定排序', () => {
    const sorted = sortLocalBooks(
      [
        book({ id: 'a', addedAt: 1 }),
        book({ id: 'c', addedAt: 2 }),
        book({ id: 'b', addedAt: 2 }),
      ],
      'created_desc',
    );
    expect(sorted.map((b) => b.id)).toEqual(['b', 'c', 'a']);
  });

  it('last_read_desc 把从未读过的书排最后', () => {
    const sorted = sortLocalBooks(
      [
        book({ id: 'never', lastReadAt: null }),
        book({ id: 'old', lastReadAt: 10 }),
        book({ id: 'new', lastReadAt: 20 }),
      ],
      'last_read_desc',
    );
    expect(sorted.map((b) => b.id)).toEqual(['new', 'old', 'never']);
  });

  it('title_asc 使用中文排序，同标题回退 id', () => {
    const sorted = sortLocalBooks(
      [
        book({ id: '2', title: '乙' }),
        book({ id: '1', title: '阿' }),
        book({ id: '0', title: '阿' }),
      ],
      'title_asc',
    );
    expect(sorted.map((b) => b.id)).toEqual(['0', '1', '2']);
  });
});

describe('loadLibraryPage 本地模式', () => {
  it('format 精确匹配', async () => {
    getBooksMock.mockResolvedValue([
      book({ id: 'e', format: 'epub' }),
      book({ id: 'p', format: 'pdf' }),
    ]);
    const page = await loadLibraryPage({ offset: 0, limit: 60, format: 'pdf' });
    expect(page.books.map((b) => b.id)).toEqual(['p']);
    expect(page.total).toBe(1);
  });

  it('language 按归一化值匹配 en-US / ENG / en', async () => {
    getBooksMock.mockResolvedValue([
      book({ id: 'a', language: 'en-US' }),
      book({ id: 'b', language: 'ENG' }),
      book({ id: 'c', language: 'en' }),
      book({ id: 'd', language: 'zh' }),
    ]);
    const page = await loadLibraryPage({ offset: 0, limit: 60, language: 'en' });
    expect(page.books.map((b) => b.id).sort()).toEqual(['a', 'b', 'c']);
  });

  it('tag 精确匹配标签数组', async () => {
    getBooksMock.mockResolvedValue([
      book({ id: 'a', tags: ['历史', '近代'] }),
      book({ id: 'b', tags: ['历史'] }),
    ]);
    const page = await loadLibraryPage({ offset: 0, limit: 60, tag: '近代' });
    expect(page.books.map((b) => b.id)).toEqual(['a']);
  });

  it('最近添加先过滤再截断，目标书不被前 20 本挤掉', async () => {
    const books = Array.from({ length: 30 }, (_, i) =>
      book({
        id: `b${i}`,
        title: i === 29 ? '目标书' : `普通书${i}`,
        addedAt: i,
      }),
    );
    getBooksMock.mockResolvedValue(books);
    const page = await loadLibraryPage({
      offset: 0,
      limit: 20,
      shelf: 'recent',
      search: '目标书',
    });
    expect(page.books.map((b) => b.id)).toEqual(['b29']);
    expect(page.total).toBe(1);
  });

  it('质量筛选在本地模式明确报错', async () => {
    getBooksMock.mockResolvedValue([]);
    await expect(
      loadLibraryPage({ offset: 0, limit: 60, quality: 'issues' }),
    ).rejects.toThrow('质量筛选需要连接后端服务');
  });
});
