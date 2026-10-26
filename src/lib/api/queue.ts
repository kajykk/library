/** 阅读队列 API（契约第 6 节）。单独成文件，避免 client.ts 继续膨胀。 */

import { request } from './client';

export type QueueStatus = 'active' | 'paused' | 'done';
export type QueueListStatus = QueueStatus | 'all';

export interface QueueDocument {
  id: string;
  title: string;
  author: string;
  format: string | null;
  cover_path: string | null;
  read_progress: number;
  last_read_at: string | null;
}

export interface QueueItem {
  id: string;
  document_id: string;
  priority: number;
  sort_order: number;
  status: QueueStatus;
  note: string;
  created_at: string | null;
  updated_at: string | null;
  document: QueueDocument;
}

export interface QueueList {
  total: number;
  items: QueueItem[];
}

/** `created=false` 表示已存在或不适用（auto-add）；`skipped` 说明原因。 */
export interface QueueCreateResult {
  created: boolean;
  item?: QueueItem;
  skipped?: string;
}

export interface QueueListParams {
  status?: QueueListStatus;
  limit?: number;
  offset?: number;
}

export function listQueue(params: QueueListParams = {}): Promise<QueueList> {
  const qs: Record<string, string> = {};
  if (params.status) qs.status = params.status;
  if (params.limit != null) qs.limit = String(params.limit);
  if (params.offset != null) qs.offset = String(params.offset);
  const query = new URLSearchParams(qs).toString();
  return request<QueueList>(`/api/reading-queue${query ? `?${query}` : ''}`);
}

export function addToQueue(
  documentId: string,
  options: { priority?: number; note?: string } = {},
): Promise<QueueCreateResult> {
  return request<QueueCreateResult>('/api/reading-queue', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      document_id: documentId,
      priority: options.priority ?? 100,
      note: options.note ?? '',
    }),
  });
}

/** 阅读进度上报时调用：后端幂等，重复/不适用返回 200 + created=false。 */
export function autoAddToQueue(documentId: string): Promise<QueueCreateResult> {
  return request<QueueCreateResult>('/api/reading-queue/auto-add', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ document_id: documentId, priority: 100, note: '' }),
  });
}

export function updateQueueItem(
  itemId: string,
  patch: { priority?: number; status?: QueueStatus; note?: string },
): Promise<QueueItem> {
  return request<QueueItem>(`/api/reading-queue/${itemId}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
}

export function removeQueueItem(itemId: string): Promise<void> {
  return request<void>(`/api/reading-queue/${itemId}`, { method: 'DELETE' });
}

/** 提交当前完整的 active 顺序；后端要求所有 id 均为 active。 */
export function reorderQueue(orderedIds: string[]): Promise<{ items: QueueItem[] }> {
  return request<{ items: QueueItem[] }>('/api/reading-queue/reorder', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ordered_ids: orderedIds }),
  });
}

export async function getQueueNext(): Promise<QueueItem | null> {
  return (await request<QueueItem | undefined>('/api/reading-queue/next')) ?? null;
}
