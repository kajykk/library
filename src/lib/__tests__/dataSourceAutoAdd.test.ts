// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../db', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../db')>();
  return {
    ...actual,
    initDB: vi.fn().mockResolvedValue(undefined),
    getBookById: vi.fn().mockResolvedValue(null),
    updateBook: vi.fn().mockResolvedValue(undefined),
    addPendingWrite: vi.fn().mockResolvedValue(undefined),
  };
});

vi.mock('../api/client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../api/client')>();
  return {
    ...actual,
    fetchSystemHealth: vi.fn(),
    patchDocument: vi.fn(),
  };
});

vi.mock('../api/queue', () => ({
  autoAddToQueue: vi.fn(),
}));

import { fetchSystemHealth, patchDocument } from '../api/client';
import { autoAddToQueue } from '../api/queue';
import { resetModeCache, saveReadingProgress } from '../dataSource';

const healthMock = vi.mocked(fetchSystemHealth);
const patchMock = vi.mocked(patchDocument);
const autoAddMock = vi.mocked(autoAddToQueue);

beforeEach(() => {
  resetModeCache();
  window.localStorage.clear();
  window.sessionStorage.clear();
  healthMock.mockReset().mockResolvedValue({ status: 'ok', checks: {} });
  patchMock.mockReset().mockResolvedValue({} as never);
  autoAddMock.mockReset().mockResolvedValue({ created: true });
  window.localStorage.setItem('kb_api_token', 'test-token');
});

describe('saveReadingProgress 自动入队', () => {
  it('进度为 0 时只保存进度，不触发入队', async () => {
    await saveReadingProgress('progress-zero', 0, null);
    expect(patchMock).toHaveBeenCalledTimes(1);
    expect(autoAddMock).not.toHaveBeenCalled();
  });

  it('进度 > 0 时在保存成功后触发一次 auto-add，并写入会话标记', async () => {
    await saveReadingProgress('progress-new', 12, 3);
    expect(patchMock).toHaveBeenCalledWith('progress-new', expect.objectContaining({
      read_progress: 0.12,
      position: { page: 3 },
    }));
    expect(autoAddMock).toHaveBeenCalledTimes(1);
    expect(autoAddMock).toHaveBeenCalledWith('progress-new');
    expect(window.sessionStorage.getItem('kb_queue_auto_added:progress-new')).toBe('1');
  });

  it('同一本书同一会话只尝试一次（内存去重）', async () => {
    await saveReadingProgress('progress-repeat', 10, 1);
    await saveReadingProgress('progress-repeat', 20, 2);
    expect(patchMock).toHaveBeenCalledTimes(2);
    expect(autoAddMock).toHaveBeenCalledTimes(1);
  });

  it('会话标记已存在时不再请求后端', async () => {
    window.sessionStorage.setItem('kb_queue_auto_added:progress-marked', '1');
    await saveReadingProgress('progress-marked', 30, null);
    expect(autoAddMock).not.toHaveBeenCalled();
  });

  it('auto-add 失败静默：进度保存仍视为成功', async () => {
    autoAddMock.mockRejectedValueOnce(new Error('queue down'));
    await expect(saveReadingProgress('progress-fail', 5, null)).resolves.toBeUndefined();
    await Promise.resolve();
    expect(autoAddMock).toHaveBeenCalledTimes(1);
  });

  it('进度保存失败时进入离线队列，不触发 auto-add', async () => {
    patchMock.mockRejectedValueOnce(new Error('network down'));
    await saveReadingProgress('progress-offline', 8, null);
    expect(autoAddMock).not.toHaveBeenCalled();
  });
});
