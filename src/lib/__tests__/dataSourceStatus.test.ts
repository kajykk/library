// @vitest-environment jsdom
import { describe, expect, it, beforeEach, vi } from 'vitest';
import {
  resolveDataSourceStatus,
  resolveMode,
  resetModeCache,
  type DataSourceStatus,
} from '../dataSource';

// ---- 测试工具 ----

function mockLocalStorage(token: string | null, baseUrl = 'http://localhost:8000') {
  const store = new Map<string, string>();
  if (token) store.set('kb_api_token', token);
  store.set('kb_api_base', baseUrl);
  Object.defineProperty(window, 'localStorage', {
    value: {
      getItem: (k: string) => store.get(k) ?? null,
      setItem: (k: string, v: string) => store.set(k, v),
      removeItem: (k: string) => store.delete(k),
      clear: () => store.clear(),
    },
    writable: true,
    configurable: true,
  });
}

function mockFetchOk(health: Record<string, unknown> = { status: 'ok', checks: {} }) {
  return vi.fn().mockImplementation((url: string) => {
    if (url.includes('/api/health')) {
      return Promise.resolve({
        ok: true,
        status: 200,
        json: () => Promise.resolve(health),
      });
    }
    return Promise.reject(new Error(`unexpected fetch: ${url}`));
  });
}

function mockFetchFail() {
  return vi.fn().mockImplementation(() =>
    Promise.reject(new TypeError('Failed to fetch')),
  );
}

beforeEach(() => {
  resetModeCache();
  vi.restoreAllMocks();
});

// ---- 测试 ----

describe('resolveDataSourceStatus', () => {
  it('无 token → local / no_token', async () => {
    mockLocalStorage(null);
    vi.stubGlobal('fetch', mockFetchOk());

    const status = await resolveDataSourceStatus();
    expect(status.mode).toBe('local');
    expect(status.reason).toBe('no_token');
    expect(status.tokenConfigured).toBe(false);
    expect(status.health).toBeNull();
    expect(fetch).not.toHaveBeenCalled(); // 没 token 不发健康检查
  });

  it('有 token + health ok → api / api_ok', async () => {
    mockLocalStorage('test-token');
    vi.stubGlobal('fetch', mockFetchOk({ status: 'ok', checks: { db: 'ok' } }));

    const status = await resolveDataSourceStatus();
    expect(status.mode).toBe('api');
    expect(status.reason).toBe('api_ok');
    expect(status.tokenConfigured).toBe(true);
    expect(status.health).not.toBeNull();
    expect(status.health!.status).toBe('ok');
    expect(status.baseUrl).toBe('http://localhost:8000');
  });

  it('有 token + health fail → degraded / health_failed', async () => {
    mockLocalStorage('test-token');
    vi.stubGlobal('fetch', mockFetchFail());

    const status = await resolveDataSourceStatus();
    expect(status.mode).toBe('degraded');
    expect(status.reason).toBe('health_failed');
    expect(status.tokenConfigured).toBe(true);
    expect(status.health).toBeNull();
  });

  it('resolveMode() 在 degraded 时仍返回 local（数据逻辑不变）', async () => {
    mockLocalStorage('bad-token');
    vi.stubGlobal('fetch', mockFetchFail());

    const mode = await resolveMode();
    expect(mode).toBe('local');
  });

  it('resolveMode() 在 api 时返回 api', async () => {
    mockLocalStorage('good-token');
    vi.stubGlobal('fetch', mockFetchOk());

    const mode = await resolveMode();
    expect(mode).toBe('api');
  });

  it('两次并发调用只发一次 fetch（单飞去重）', async () => {
    mockLocalStorage('test-token');
    const fetchMock = mockFetchOk();
    vi.stubGlobal('fetch', fetchMock);

    const [a, b] = await Promise.all([
      resolveDataSourceStatus(),
      resolveDataSourceStatus(),
    ]);
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(a).toBe(b); // 同一对象引用
  });

  it('force=true 强制重新探测', async () => {
    mockLocalStorage('test-token');
    const fetchMock = mockFetchOk();
    vi.stubGlobal('fetch', fetchMock);

    await resolveDataSourceStatus();
    await resolveDataSourceStatus(true); // force
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('30s 缓存生效：两次顺序调用只发一次 fetch', async () => {
    mockLocalStorage('test-token');
    const fetchMock = mockFetchOk();
    vi.stubGlobal('fetch', fetchMock);

    await resolveDataSourceStatus();
    await resolveDataSourceStatus(); // 缓存命中
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('resetModeCache() 清空缓存，下次重新探测', async () => {
    mockLocalStorage('test-token');
    const fetchMock = mockFetchOk();
    vi.stubGlobal('fetch', fetchMock);

    await resolveDataSourceStatus();
    resetModeCache();
    await resolveDataSourceStatus();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
