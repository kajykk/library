// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { QueueItem } from '@/lib/api/queue';
import type { ReviewCard } from '@/lib/api/reviews';

const api = vi.hoisted(() => ({
  listQueue: vi.fn(),
  getQueueNext: vi.fn(),
  updateQueueItem: vi.fn(),
  removeQueueItem: vi.fn(),
  reorderQueue: vi.fn(),
  getDueReviews: vi.fn(),
  getReviewStats: vi.fn(),
  rateReview: vi.fn(),
  snoozeReview: vi.fn(),
}));

const nav = vi.hoisted(() => ({
  params: new URLSearchParams(),
  push: vi.fn(),
  replace: vi.fn(),
}));

const ds = vi.hoisted(() => ({ mode: 'api' as 'api' | 'local' }));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: nav.push, replace: nav.replace }),
  useSearchParams: () => nav.params,
}));

vi.mock('next/link', () => ({
  default: ({ href, children }: { href: string; children: ReactNode }) => (
    <a href={href}>{children}</a>
  ),
}));

vi.mock('@/components/BookCover', () => ({
  default: () => <div data-testid="book-cover" />,
}));

vi.mock('@/lib/useDataSourceStatus', () => ({
  useDataSourceStatus: () => ({
    status: { mode: ds.mode },
    isResolving: false,
    refresh: vi.fn(),
  }),
}));

vi.mock('@/lib/api/queue', () => ({
  listQueue: api.listQueue,
  getQueueNext: api.getQueueNext,
  updateQueueItem: api.updateQueueItem,
  removeQueueItem: api.removeQueueItem,
  reorderQueue: api.reorderQueue,
}));

vi.mock('@/lib/api/reviews', () => ({
  getDueReviews: api.getDueReviews,
  getReviewStats: api.getReviewStats,
  rateReview: api.rateReview,
  snoozeReview: api.snoozeReview,
}));

import QueuePage from '../page';

const stats = {
  tz_offset_minutes: 480,
  due_now: 1,
  due_today: 0,
  new_today: 1,
  reviewed_today: 0,
  upcoming_7d: 0,
};

function queueItem(
  id: string,
  title: string,
  priority: number,
  sortOrder: number,
): QueueItem {
  return {
    id,
    document_id: `doc-${id}`,
    priority,
    sort_order: sortOrder,
    status: 'active',
    note: '',
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
    document: {
      id: `doc-${id}`,
      title,
      author: '',
      format: 'epub',
      cover_path: null,
      read_progress: 0.2,
      last_read_at: null,
    },
  };
}

const card: ReviewCard = {
  annotation_id: 'ann-1',
  document_id: 'doc-1',
  document_title: '复习之书',
  document_type: 'book',
  type: 'highlight',
  anchor: { chapter: 2 },
  quote: '关键句',
  content: '我的批注',
  created_at: '2026-01-01T00:00:00',
  due_at: '2026-01-02T00:00:00',
  interval_days: 0,
  repetitions: 0,
  ease: 2.5,
  preview: { again: '10 分钟', hard: '1 天', good: '3 天', easy: '4 天' },
};

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(
    <QueryClientProvider client={client}>
      <ToastProvider>
        <DialogProvider>
          <QueuePage />
        </DialogProvider>
      </ToastProvider>
    </QueryClientProvider>,
  );
}

import { DialogProvider, ToastProvider } from '@/components/ui';

beforeEach(() => {
  ds.mode = 'api';
  nav.params = new URLSearchParams();
  nav.push.mockReset();
  nav.replace.mockReset();
  api.listQueue.mockReset().mockResolvedValue({ total: 0, items: [] });
  api.getQueueNext.mockReset().mockResolvedValue(null);
  api.updateQueueItem.mockReset().mockResolvedValue({});
  api.removeQueueItem.mockReset().mockResolvedValue(undefined);
  api.reorderQueue.mockReset().mockResolvedValue({ items: [] });
  api.getDueReviews.mockReset().mockResolvedValue({ total: 0, cards: [] });
  api.getReviewStats.mockReset().mockResolvedValue(stats);
  api.rateReview.mockReset().mockResolvedValue({ annotation_id: 'ann-1' });
  api.snoozeReview.mockReset().mockResolvedValue({ annotation_id: 'ann-1' });
});

afterEach(cleanup);

describe('QueuePage · 阅读队列', () => {
  it('跨优先级不可移动，同优先级可重排', async () => {
    const low = queueItem('q-c', '低优先', 50, 0);
    const first = queueItem('q-a', '甲', 100, 0);
    const second = queueItem('q-b', '乙', 100, 1);
    api.listQueue.mockResolvedValue({ total: 3, items: [low, first, second] });

    renderPage();
    expect(await screen.findByText('在读队列')).toBeInTheDocument();
    expect(
      screen.getByText('不同优先级的书按优先级排序，上移/下移仅在相同优先级内生效。'),
    ).toBeInTheDocument();

    expect(screen.getByRole('button', { name: '上移《甲》' })).toBeDisabled();
    expect(screen.getByRole('button', { name: '下移《甲》' })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: '下移《甲》' }));
    await waitFor(() =>
      expect(api.reorderQueue).toHaveBeenCalledWith(['q-c', 'q-b', 'q-a']),
    );
  });

  it('本地模式不发队列请求，只显示连接后端提示', () => {
    ds.mode = 'local';
    renderPage();
    expect(screen.getByText('阅读队列需要连接后端')).toBeInTheDocument();
    expect(api.listQueue).not.toHaveBeenCalled();
    expect(api.getQueueNext).not.toHaveBeenCalled();
  });
});

describe('QueuePage · 间隔复习', () => {
  beforeEach(() => {
    nav.params = new URLSearchParams('tab=review');
    api.getReviewStats.mockResolvedValue(stats);
    api.getDueReviews.mockResolvedValue({ total: 1, cards: [card] });
  });

  it('先显示答案，再用快捷键评分', async () => {
    renderPage();
    expect(await screen.findByText('关键句')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: ' ' });
    expect(screen.getByText('良好 3')).toBeInTheDocument();
    expect(screen.getByText('3 天')).toBeInTheDocument();

    fireEvent.keyDown(window, { key: '3' });
    await waitFor(() =>
      expect(api.rateReview).toHaveBeenCalledWith('ann-1', 'good'),
    );
    expect(await screen.findByText('今天的复习做完了')).toBeInTheDocument();
  });

  it('推迟 1 天按钮调用 snooze', async () => {
    renderPage();
    expect(await screen.findByText('关键句')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '显示答案（空格 / Enter）' }));
    fireEvent.click(screen.getByRole('button', { name: '推迟 1 天（S）' }));

    await waitFor(() =>
      expect(api.snoozeReview).toHaveBeenCalledWith('ann-1', 1),
    );
  });
});
