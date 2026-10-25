import { afterEach, describe, expect, it, vi } from 'vitest';
import { listAllDocuments, LIST_HARD_LIMIT } from '../client';

/** 只关心分页拼参：把 fetch 换成一个按 offset/limit 切片的内存"后端" */
function stubBackend(total: number) {
  const calls: Array<{ offset: number; limit: number }> = [];
  const rows = Array.from({ length: total }, (_, i) => ({ id: `doc-${i}`, title: `书 ${i}` }));

  vi.stubGlobal(
    'fetch',
    vi.fn(async (url: string) => {
      const parsed = new URL(url, 'http://localhost');
      const limit = Number(parsed.searchParams.get('limit'));
      const offset = Number(parsed.searchParams.get('offset') || '0');
      calls.push({ limit, offset });
      return new Response(JSON.stringify(rows.slice(offset, offset + limit)), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      });
    }),
  );

  return { calls, rows };
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('listAllDocuments', () => {
  it('按硬上限翻页，直到取回全部结果', async () => {
    const total = LIST_HARD_LIMIT + 5;
    const { calls, rows } = stubBackend(total);

    const docs = await listAllDocuments({ include_content: 'false' });

    expect(docs).toHaveLength(total);
    expect(docs[total - 1]?.id).toBe(rows[total - 1]?.id);
    // 第一页拿满 → 必然继续翻第二页，第二页不足一页即停
    expect(calls).toEqual([
      { limit: LIST_HARD_LIMIT, offset: 0 },
      { limit: LIST_HARD_LIMIT, offset: LIST_HARD_LIMIT },
    ]);
  });

  it('结果不足一页时只请求一次', async () => {
    const { calls } = stubBackend(3);

    const docs = await listAllDocuments();

    expect(docs).toHaveLength(3);
    expect(calls).toHaveLength(1);
  });

  it('空库返回空数组', async () => {
    const { calls } = stubBackend(0);

    expect(await listAllDocuments()).toEqual([]);
    expect(calls).toHaveLength(1);
  });
});
