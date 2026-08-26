// @vitest-environment jsdom
/**
 * BookReader PDF/EPUB 特征测试：
 * PDF —— mock pdfjs（jsdom 无 canvas 2d 上下文，渲染自然早退），
 *        锁定元信息回填、翻页调用与进度保存、卸载资源释放；
 * EPUB —— mock foliate 解析器，锁定章节内容切换与连续滚动模式。
 */
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import BookReader from '../BookReader';
import type { Book } from '@/lib/db';
import { getFileArrayBuffer, saveReadingProgress } from '@/lib/dataSource';

vi.mock('@/lib/dataSource', () => ({
  getFileArrayBuffer: vi.fn(),
  saveReadingProgress: vi.fn().mockResolvedValue(undefined),
  saveReadingSession: vi.fn().mockResolvedValue(undefined),
  listAnnotations: vi.fn().mockResolvedValue([]),
  addBookmarkAnnotation: vi.fn(),
  addNoteAnnotation: vi.fn(),
  createHighlight: vi.fn(),
  deleteAnnotation: vi.fn(),
}));

// pdfjs 动态导入 mock：spies 在 hoisted 阶段创建（工厂惰性执行也可引用）
const pdfjs = vi.hoisted(() => ({
  getPage: vi.fn(),
  destroyDoc: vi.fn(async () => undefined),
  destroyTask: vi.fn(async () => undefined),
  numPages: 3,
}));

vi.mock('pdfjs-dist/legacy/build/pdf.mjs', () => {
  const pdf = {
    numPages: pdfjs.numPages,
    getPage: pdfjs.getPage,
    destroy: pdfjs.destroyDoc,
  };
  const loadingTask = { promise: Promise.resolve(pdf), destroy: pdfjs.destroyTask };
  return {
    GlobalWorkerOptions: { workerSrc: '' },
    getDocument: () => loadingTask,
  };
});

vi.mock('@/lib/parsers/foliate', () => ({
  parseEpubChapters: vi.fn(async () => [
    { href: 'c1.xhtml', title: '第一章', content: '<p>第一章内容</p>' },
    { href: 'c2.xhtml', title: '第二章', content: '<p>第二章内容</p>' },
  ]),
  parseMobiChapters: vi.fn(async () => []),
}));

const mockedGetFile = vi.mocked(getFileArrayBuffer);
const mockedSaveProgress = vi.mocked(saveReadingProgress);

function makeBook(format: string, lastPosition: number | null): Book {
  return {
    id: 'b1',
    title: '测试书',
    author: '作者',
    publisher: '',
    category: '',
    format,
    filePath: 'f',
    coverPath: null,
    description: '',
    addedAt: 0,
    lastReadAt: null,
    readProgress: 0,
    lastPosition,
    totalPages: 0,
    fileSize: 0,
    language: 'zh',
    isbn: '',
    tags: [],
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  // jsdom 无 canvas 2d：renderPdfPage 在 getContext 处早退（不设置内容）
  pdfjs.getPage.mockImplementation(async (n: number) => ({
    getViewport: ({ scale }: { scale: number }) => ({ width: 600 * scale, height: 800 * scale }),
    render: () => ({ promise: Promise.resolve() }),
  }));
});

afterEach(() => {
  cleanup();
});

describe('BookReader（PDF 特征基线）', () => {
  it('加载后回填真实页数与保存位置', async () => {
    mockedGetFile.mockResolvedValue(new ArrayBuffer(8));
    render(<BookReader book={makeBook('pdf', 2)} onClose={vi.fn()} />);

    // totalPages 由 pdf.numPages 回填；savedPage=2
    await waitFor(() => expect(screen.getByText('/ 3')).toBeInTheDocument());
    expect(screen.getByRole('spinbutton')).toHaveValue(2);
  });

  it('ArrowRight 翻页：渲染目标页并以新页码保存进度', async () => {
    mockedGetFile.mockResolvedValue(new ArrayBuffer(8));
    render(<BookReader book={makeBook('pdf', 2)} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('/ 3')).toBeInTheDocument());
    mockedSaveProgress.mockClear();

    fireEvent.keyDown(window, { key: 'ArrowRight' });

    await waitFor(() => expect(pdfjs.getPage).toHaveBeenCalledWith(3));
    await waitFor(() => expect(mockedSaveProgress).toHaveBeenCalledTimes(1));
    const call = mockedSaveProgress.mock.calls[0];
    if (!call) throw new Error('saveReadingProgress 应至少被调用一次');
    const [bookId, progress, position] = call;
    expect(bookId).toBe('b1');
    expect(position).toBe(3);
    expect(progress).toBe(100);
  });

  it('卸载时销毁 pdf 文档与 loadingTask（防 worker 内存泄漏）', async () => {
    mockedGetFile.mockResolvedValue(new ArrayBuffer(8));
    const { unmount } = render(<BookReader book={makeBook('pdf', 1)} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText('/ 3')).toBeInTheDocument());

    unmount();

    expect(pdfjs.destroyDoc).toHaveBeenCalledTimes(1);
    expect(pdfjs.destroyTask).toHaveBeenCalledTimes(1);
  });
});

describe('BookReader（EPUB 特征基线）', () => {
  it('渲染当前章节内容，ArrowRight 切换到下一章节', async () => {
    mockedGetFile.mockResolvedValue(new ArrayBuffer(8));
    render(<BookReader book={makeBook('epub', null)} onClose={vi.fn()} />);

    await waitFor(() => expect(screen.getByText(/第一章内容/)).toBeInTheDocument());
    mockedSaveProgress.mockClear();

    fireEvent.keyDown(window, { key: 'ArrowRight' });

    await waitFor(() => expect(screen.getByText(/第二章内容/)).toBeInTheDocument());
    await waitFor(() => expect(mockedSaveProgress).toHaveBeenCalledTimes(1));
    const call = mockedSaveProgress.mock.calls[0];
    if (!call) throw new Error('saveReadingProgress 应至少被调用一次');
    const [, progress, position] = call;
    expect(position).toBe(1);
    expect(progress).toBe(100); // 第 2 章 / 共 2 章
  });

  it('连续滚动模式同时渲染全部章节（净化已记忆化）', async () => {
    mockedGetFile.mockResolvedValue(new ArrayBuffer(8));
    render(<BookReader book={makeBook('epub', null)} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/第一章内容/)).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('切换为连续滚动模式'));

    await waitFor(() => {
      expect(screen.getByText('第一章')).toBeInTheDocument();
      expect(screen.getByText('第二章')).toBeInTheDocument();
    });
  });

  it('书内搜索面板可打开并命中关键词（章节文本缓存路径）', async () => {
    mockedGetFile.mockResolvedValue(new ArrayBuffer(8));
    render(<BookReader book={makeBook('epub', null)} onClose={vi.fn()} />);
    await waitFor(() => expect(screen.getByText(/第一章内容/)).toBeInTheDocument());

    // S 快捷键打开书内搜索
    fireEvent.keyDown(window, { key: 's' });
    const input = await screen.findByPlaceholderText('在全书内容中搜索...');
    fireEvent.change(input, { target: { value: '第二章' } });

    await waitFor(() =>
      expect(screen.getAllByText(/第二章/).length).toBeGreaterThan(0),
    );
  });
});
