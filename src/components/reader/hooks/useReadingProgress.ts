'use client';

/**
 * 阅读进度保存：登记在途保存请求，供关闭阅读器前 flush。
 * 从 BookReader 抽离；行为契约见 __tests__/BookReader.test.tsx。
 */

import { useCallback, useRef } from 'react';
import { saveReadingProgress } from '@/lib/dataSource';

export function useReadingProgress(bookId: string) {
  const lastSaveRef = useRef<Promise<unknown>>(Promise.resolve());

  /** 发起一次进度保存并登记为在途。失败静默（离线队列已接管），不阻塞翻页。 */
  const save = useCallback(
    (progressPercent: number, position: number): Promise<void> => {
      const pending = saveReadingProgress(bookId, Math.min(100, progressPercent), position).catch(
        () => undefined,
      );
      lastSaveRef.current = pending;
      return pending;
    },
    [bookId],
  );

  /** 等待在途保存落地；保存失败不阻塞退出。 */
  const flush = useCallback(async () => {
    try {
      await lastSaveRef.current;
    } catch {
      // 保存失败不阻塞退出（离线队列已接管）
    }
  }, []);

  return { save, flush };
}
