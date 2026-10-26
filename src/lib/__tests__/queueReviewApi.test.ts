import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  addToQueue,
  autoAddToQueue,
  getQueueNext,
  listQueue,
  removeQueueItem,
  reorderQueue,
  updateQueueItem,
} from '../api/queue';
import {
  getDueReviews,
  getReviewStats,
  rateReview,
  snoozeReview,
} from '../api/reviews';

const BASE = 'http://localhost:8000';

function jsonResponse(data: unknown, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    json: () => Promise.resolve(data),
    text: () => Promise.resolve(JSON.stringify(data)),
  };
}

const fetchMock = vi.fn();

function lastCall(): [string, RequestInit] {
  const call = fetchMock.mock.calls.at(-1);
  expect(call).toBeDefined();
  return call as [string, RequestInit];
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('queue API', () => {
  it('listQueue 拼接 status/limit/offset，省略未传参数', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ total: 0, items: [] }));
    await listQueue();
    expect(lastCall()[0]).toBe(`${BASE}/api/reading-queue`);
    expect(lastCall()[1].method).toBeUndefined();

    await listQueue({ status: 'all', limit: 200, offset: 20 });
    expect(lastCall()[0]).toBe(
      `${BASE}/api/reading-queue?status=all&limit=200&offset=20`,
    );
  });

  it('addToQueue POST 默认 priority=100、note 为空', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ created: true }, 201));
    const result = await addToQueue('doc-1');
    expect(result.created).toBe(true);
    const [url, init] = lastCall();
    expect(url).toBe(`${BASE}/api/reading-queue`);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({
      document_id: 'doc-1',
      priority: 100,
      note: '',
    });
  });

  it('addToQueue 透传 priority/note', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ created: true }, 201));
    await addToQueue('doc-2', { priority: 5, note: '周末读' });
    expect(JSON.parse(String(lastCall()[1].body))).toEqual({
      document_id: 'doc-2',
      priority: 5,
      note: '周末读',
    });
  });

  it('autoAddToQueue 走幂等端点并返回 created=false/skipped', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ created: false, skipped: 'exists' }));
    const result = await autoAddToQueue('doc-3');
    expect(result).toEqual({ created: false, skipped: 'exists' });
    expect(lastCall()[0]).toBe(`${BASE}/api/reading-queue/auto-add`);
    expect(lastCall()[1].method).toBe('POST');
  });

  it('updateQueueItem PATCH 指定 id 并只发送传入字段', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 'q1' }));
    await updateQueueItem('q1', { status: 'paused' });
    const [url, init] = lastCall();
    expect(url).toBe(`${BASE}/api/reading-queue/q1`);
    expect(init.method).toBe('PATCH');
    expect(JSON.parse(String(init.body))).toEqual({ status: 'paused' });
  });

  it('removeQueueItem DELETE，204 解析为 undefined', async () => {
    fetchMock.mockResolvedValue(jsonResponse(undefined, 204));
    await expect(removeQueueItem('q2')).resolves.toBeUndefined();
    const [url, init] = lastCall();
    expect(url).toBe(`${BASE}/api/reading-queue/q2`);
    expect(init.method).toBe('DELETE');
  });

  it('reorderQueue 提交 ordered_ids', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ items: [] }));
    await reorderQueue(['a', 'b']);
    const [url, init] = lastCall();
    expect(url).toBe(`${BASE}/api/reading-queue/reorder`);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ ordered_ids: ['a', 'b'] });
  });

  it('getQueueNext 无下一本（204）返回 null，有则返回条目', async () => {
    fetchMock.mockResolvedValue(jsonResponse(undefined, 204));
    await expect(getQueueNext()).resolves.toBeNull();

    fetchMock.mockResolvedValue(jsonResponse({ id: 'q1', document_id: 'd1' }));
    await expect(getQueueNext()).resolves.toMatchObject({ id: 'q1' });
  });
});

describe('reviews API', () => {
  it('getDueReviews 映射 new_limit/document_id 并省略未传参数', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ total: 0, cards: [] }));
    await getDueReviews();
    expect(lastCall()[0]).toBe(`${BASE}/api/reviews/due`);

    await getDueReviews({ limit: 5, newLimit: 3, documentId: 'd1' });
    expect(lastCall()[0]).toBe(
      `${BASE}/api/reviews/due?limit=5&new_limit=3&document_id=d1`,
    );
  });

  it('rateReview POST 评分档位', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ annotation_id: 'a1' }));
    await rateReview('a1', 'good');
    const [url, init] = lastCall();
    expect(url).toBe(`${BASE}/api/reviews/a1/rate`);
    expect(init.method).toBe('POST');
    expect(JSON.parse(String(init.body))).toEqual({ rating: 'good' });
  });

  it('snoozeReview 默认推迟 1 天，可指定 3/7', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ annotation_id: 'a1' }));
    await snoozeReview('a1');
    expect(lastCall()[0]).toBe(`${BASE}/api/reviews/a1/snooze`);
    expect(JSON.parse(String(lastCall()[1].body))).toEqual({ days: 1 });

    await snoozeReview('a1', 7);
    expect(JSON.parse(String(lastCall()[1].body))).toEqual({ days: 7 });
  });

  it('getReviewStats 携带 tz_offset_minutes', async () => {
    fetchMock.mockResolvedValue(jsonResponse({ due_now: 0 }));
    await getReviewStats(480);
    expect(lastCall()[0]).toBe(`${BASE}/api/reviews/stats?tz_offset_minutes=480`);
  });

  it('错误响应抛出 ApiError 并保留状态码与 detail', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 400,
      statusText: 'Bad Request',
      json: () => Promise.resolve({ detail: '书签不参与复习' }),
      text: () => Promise.resolve(JSON.stringify({ detail: '书签不参与复习' })),
    });
    await expect(rateReview('a1', 'again')).rejects.toMatchObject({
      status: 400,
      message: '书签不参与复习',
    });
  });
});
