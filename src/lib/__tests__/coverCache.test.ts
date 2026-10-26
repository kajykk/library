import { beforeEach, afterEach, describe, expect, it, vi } from 'vitest';

const revoke = vi.fn();
const originalRevoke = URL.revokeObjectURL;

let cache: typeof import('../coverCache');

beforeEach(async () => {
  vi.resetModules();
  cache = await import('../coverCache');
  URL.revokeObjectURL = revoke;
});

afterEach(() => {
  URL.revokeObjectURL = originalRevoke;
  revoke.mockReset();
});

describe('coverCache', () => {
  it('inflight 去重：同一本书并发 retain 只请求一次', async () => {
    let resolve!: (url: string | null) => void;
    const loader = vi.fn(
      () => new Promise<string | null>((r) => { resolve = r; }),
    );
    cache.retainCover('b1', loader);
    cache.retainCover('b1', loader);
    expect(loader).toHaveBeenCalledTimes(1);
    expect(cache.getCachedCoverUrl('b1')).toBeNull();

    resolve('blob:b1');
    await vi.waitFor(() => expect(cache.getCachedCoverUrl('b1')).toBe('blob:b1'));
    expect(revoke).not.toHaveBeenCalled();

    cache.releaseCover('b1');
    cache.releaseCover('b1');
  });

  it('超出 LRU 上限时驱逐最久未用且 refs=0 的条目', async () => {
    const loader = (id: string) => Promise.resolve(`blob:${id}`);
    for (let i = 0; i <= cache.COVER_CACHE_LIMIT; i += 1) {
      cache.retainCover(`b${i}`, loader);
      cache.releaseCover(`b${i}`);
    }
    await vi.waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:b0'));
    expect(cache.getCachedCoverUrl('b0')).toBeNull();
    expect(cache.getCachedCoverUrl('b1')).toBe('blob:b1');
  });

  it('refs>0 的条目不会被驱逐', async () => {
    const loader = (id: string) => Promise.resolve(`blob:${id}`);
    cache.retainCover('pinned', loader);
    for (let i = 0; i <= cache.COVER_CACHE_LIMIT; i += 1) {
      cache.retainCover(`x${i}`, loader);
      cache.releaseCover(`x${i}`);
    }
    await vi.waitFor(() => expect(cache.getCachedCoverUrl('pinned')).toBe('blob:pinned'));
    expect(revoke).not.toHaveBeenCalledWith('blob:pinned');
    expect(cache.getCachedCoverUrl('x0')).toBeNull();
    cache.releaseCover('pinned');
  });

  it('清空后迟到的 blob 会被立即释放', async () => {
    let resolve!: (url: string | null) => void;
    const loader = () => new Promise<string | null>((r) => { resolve = r; });
    cache.retainCover('late', loader);
    cache.releaseCover('late');
    cache.clearCoverCache();

    resolve('blob:late');
    await vi.waitFor(() => expect(revoke).toHaveBeenCalledWith('blob:late'));
    expect(cache.getCachedCoverUrl('late')).toBeNull();
  });

  it('无封面（null）结果会缓存，避免重复 404', async () => {
    const loader = vi.fn(() => Promise.resolve(null));
    cache.retainCover('none', loader);
    await vi.waitFor(() => expect(loader).toHaveBeenCalledTimes(1));
    await Promise.resolve();

    cache.retainCover('none', loader);
    expect(loader).toHaveBeenCalledTimes(1);
    expect(cache.getCachedCoverUrl('none')).toBeNull();

    cache.releaseCover('none');
    cache.releaseCover('none');
  });

  it('data: URL 不需要 revoke', async () => {
    const dataUrl = 'data:image/png;base64,AAAA';
    const loader = () => Promise.resolve(dataUrl);
    cache.retainCover('d1', loader);
    await vi.waitFor(() => expect(cache.getCachedCoverUrl('d1')).toBe(dataUrl));

    cache.releaseCover('d1');
    cache.clearCoverCache();
    expect(revoke).not.toHaveBeenCalled();
  });
});
