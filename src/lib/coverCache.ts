/** 封面 blob URL 缓存：LRU 200 + 引用计数 + inflight 去重，refs===0 才允许 revoke。 */

import { useEffect, useSyncExternalStore } from 'react';

export type CoverLoader = (bookId: string) => Promise<string | null>;

interface Entry {
  url: string | null;
  loaded: boolean;
  refs: number;
  inflight: Promise<void> | null;
  lastUsed: number;
}

export const COVER_CACHE_LIMIT = 200;

const entries = new Map<string, Entry>();
const listeners = new Set<() => void>();
let clock = 0;

function emit(): void {
  listeners.forEach((listener) => listener());
}

function revoke(url: string | null): void {
  if (url && url.startsWith('blob:')) URL.revokeObjectURL(url);
}

function touch(entry: Entry): void {
  entry.lastUsed = ++clock;
}

function prune(): void {
  if (entries.size <= COVER_CACHE_LIMIT) return;
  const idle = [...entries.entries()]
    .filter(([, entry]) => entry.refs === 0 && entry.inflight === null)
    .sort((a, b) => a[1].lastUsed - b[1].lastUsed);
  for (const [bookId, entry] of idle) {
    if (entries.size <= COVER_CACHE_LIMIT) break;
    entries.delete(bookId);
    revoke(entry.url);
  }
}

function ensure(bookId: string, loader: CoverLoader): Entry {
  let entry = entries.get(bookId);
  if (!entry) {
    entry = { url: null, loaded: false, refs: 0, inflight: null, lastUsed: 0 };
    entries.set(bookId, entry);
  }
  touch(entry);
  if (entry.loaded || entry.inflight) return entry;

  const current = entry;
  current.inflight = loader(bookId)
    .then((url) => {
      current.inflight = null;
      // 已被 LRU 清出（迟到返回）：blob 由这里负责释放
      if (entries.get(bookId) !== current) {
        revoke(url);
        return;
      }
      current.url = url;
      current.loaded = true;
      touch(current);
      emit();
      prune();
    })
    .catch((err) => {
      current.inflight = null;
      if (entries.get(bookId) === current) {
        entries.delete(bookId);
        revoke(current.url);
        emit();
      }
      console.warn('加载封面失败:', bookId, err);
    });
  return current;
}

export function retainCover(bookId: string, loader: CoverLoader): void {
  const entry = ensure(bookId, loader);
  entry.refs += 1;
  touch(entry);
  prune();
}

export function releaseCover(bookId: string): void {
  const entry = entries.get(bookId);
  if (!entry) return;
  entry.refs = Math.max(0, entry.refs - 1);
  touch(entry);
  prune();
}

export function getCachedCoverUrl(bookId: string): string | null {
  return entries.get(bookId)?.url ?? null;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useCoverUrl(
  bookId: string,
  enabled: boolean,
  loader: CoverLoader,
): string | null {
  const url = useSyncExternalStore(
    subscribe,
    () => getCachedCoverUrl(bookId),
    () => null,
  );
  useEffect(() => {
    if (!enabled) return;
    retainCover(bookId, loader);
    return () => releaseCover(bookId);
  }, [bookId, enabled, loader]);
  return enabled ? url : null;
}

/** 清空未被引用的缓存（测试或切换数据源时使用）。 */
export function clearCoverCache(): void {
  for (const [bookId, entry] of entries) {
    if (entry.refs > 0) continue;
    entries.delete(bookId);
    if (!entry.inflight) revoke(entry.url);
  }
  emit();
}
