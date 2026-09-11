// @vitest-environment jsdom
/**
 * BookReader 特征测试（characterization tests）：
 * 在拆分 hooks/Panes 之前锁定当前行为——翻页、进度保存参数、快捷键
 * 修饰键豁免、退出前 flush。重构时这些用例必须原样通过。
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
  convertAnnotationToNote: vi.fn(),
}));

const mockedGetFile = vi.mocked(getFileArrayBuffer);
const mockedSaveProgress = vi.mocked(saveReadingProgress);

function makeTxtBook(totalLines: number): Book {
  return {
    id: 'b1',
    title: '测试书',
    author: '作者',
    publisher: '',
    category: '',
    format: 'txt',
    filePath: 'f',
    coverPath: null,
    description: '',
    addedAt: 0,
    lastReadAt: null,
    readProgress: 0,
    lastPosition: null,
    totalPages: 0,
    fileSize: 0,
    language: 'zh',
    isbn: '',
    tags: [],
  };
}

async function renderTxtReader(totalLines: number) {
  const lines = Array.from({ length: totalLines }, (_, i) => `line-${i}`);
  const bytes = new TextEncoder().encode(lines.join('\n'));
  mockedGetFile.mockResolvedValue(bytes.buffer as ArrayBuffer);
  render(<BookReader book={makeTxtBook(totalLines)} onClose={vi.fn()} />);
  // 等待首屏渲染完成（加载态消失）
  await waitFor(() => expect(screen.getByText(/line-0/)).toBeInTheDocument());
}

describe('BookReader（TXT 特征基线）', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });
  afterEach(() => {
    cleanup();
  });

  it('按 40 行分页渲染首页', async () => {
    await renderTxtReader(100);
    expect(screen.getByText(/line-39/)).toBeInTheDocument();
    expect(screen.queryByText(/line-40\n/)).not.toBeInTheDocument();
  });

  it('ArrowRight 翻到第二页并以目标页码保存进度', async () => {
    await renderTxtReader(100);
    mockedSaveProgress.mockClear();

    fireEvent.keyDown(window, { key: 'ArrowRight' });

    await waitFor(() => expect(screen.getByText(/line-79/)).toBeInTheDocument());
    await waitFor(() => {
      expect(mockedSaveProgress).toHaveBeenCalledTimes(1);
    });
    const call = mockedSaveProgress.mock.calls[0];
    if (!call) throw new Error('saveReadingProgress 应至少被调用一次');
    const [bookId, progress, position] = call;
    expect(bookId).toBe('b1');
    expect(position).toBe(1); // 第二页索引为 1
    expect(progress).toBeGreaterThan(0);
    expect(progress).toBeLessThanOrEqual(100);
  });

  it('带 Ctrl 的按键不触发翻页（浏览器快捷键豁免）', async () => {
    await renderTxtReader(100);
    fireEvent.keyDown(window, { key: 'ArrowRight', ctrlKey: true });
    fireEvent.keyDown(window, { key: 'f', ctrlKey: true });
    // 内容仍停留在第一页
    expect(screen.getByText(/line-0/)).toBeInTheDocument();
    expect(screen.queryByText(/line-40/)).not.toBeInTheDocument();
    expect(mockedSaveProgress).not.toHaveBeenCalled();
  });

  it('输入框聚焦时方向键不劫持翻页', async () => {
    await renderTxtReader(100);
    const input = screen.getByRole('spinbutton');
    // 事件从输入框派发并冒泡：target 为 INPUT，isTyping 守卫生效
    fireEvent.keyDown(input, { key: 'ArrowRight' });
    fireEvent.keyDown(input, { key: 'ArrowLeft' });
    expect(screen.getByText(/line-0/)).toBeInTheDocument();
    expect(mockedSaveProgress).not.toHaveBeenCalled();
  });

  it('点击返回按钮触发 onClose（在途保存 flush 后）', async () => {
    const onClose = vi.fn();
    const lines = Array.from({ length: 100 }, (_, i) => `line-${i}`);
    mockedGetFile.mockResolvedValue(new TextEncoder().encode(lines.join('\n')).buffer as ArrayBuffer);
    render(<BookReader book={makeTxtBook(100)} onClose={onClose} />);
    await waitFor(() => expect(screen.getByText(/line-0/)).toBeInTheDocument());

    fireEvent.click(screen.getByTitle('返回书架'));

    await waitFor(() => expect(onClose).toHaveBeenCalledTimes(1));
  });
});
