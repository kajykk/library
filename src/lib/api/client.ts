/**
 * 后端 API client
 * 地址与 Token 优先取 localStorage（设置页可改），回退构建时环境变量
 */

export function getApiBaseUrl(): string {
  if (typeof window !== 'undefined') {
    const stored = window.localStorage.getItem('kb_api_base');
    if (stored) return stored.replace(/\/$/, '');
  }
  return (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000').replace(/\/$/, '');
}

export function getApiToken(): string {
  if (typeof window !== 'undefined') {
    return window.localStorage.getItem('kb_api_token') || process.env.NEXT_PUBLIC_API_TOKEN || '';
  }
  return process.env.NEXT_PUBLIC_API_TOKEN || '';
}

export function saveApiConfig(baseUrl: string, token: string): void {
  window.localStorage.setItem('kb_api_base', baseUrl.replace(/\/$/, ''));
  window.localStorage.setItem('kb_api_token', token);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${getApiBaseUrl()}${path}`, {
    ...init,
    headers: {
      'X-API-Token': getApiToken(),
      ...(init?.headers || {}),
    },
  });
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    let message = text.slice(0, 200) || resp.statusText;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed === 'object') {
        const detail = parsed.detail;
        message = typeof detail === 'string' ? detail : detail?.message || message;
      }
    } catch {
      // 非 JSON 错误体，用原始文本
    }
    throw new Error(message);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export async function checkHealth(): Promise<boolean> {
  try {
    const resp = await fetch(`${getApiBaseUrl()}/api/health`);
    return resp.ok;
  } catch {
    return false;
  }
}

// ---------- 类型 ----------

export interface ApiTag {
  id: string;
  name: string;
}

export interface ApiDocument {
  id: string;
  type: string;
  title: string;
  author: string;
  publisher: string;
  language: string;
  isbn: string;
  description: string;
  source_url: string | null;
  file_path: string | null;
  cover_path: string | null;
  file_size: number;
  format: string | null;
  content: string;
  meta: Record<string, unknown>;
  collection_id: string | null;
  read_progress: number;
  position: Record<string, unknown> | null;
  last_read_at: string | null;
  tags: ApiTag[];
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

export interface ApiCollection {
  id: string;
  name: string;
  parent_id: string | null;
  sort_order: number;
  created_at: string;
}

export interface DocumentPatch {
  title?: string;
  author?: string;
  publisher?: string;
  language?: string;
  isbn?: string;
  description?: string;
  source_url?: string;
  content?: string;
  collection_id?: string | null;
  read_progress?: number;
  position?: Record<string, unknown> | null;
  last_read_at?: string;
  meta?: Record<string, unknown>;
}

// ---------- documents / collections ----------

export function listDocuments(params?: Record<string, string>): Promise<ApiDocument[]> {
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return request<ApiDocument[]>(`/api/documents${qs}`);
}

export function createDocument(payload: {
  type: string;
  title: string;
  content?: string;
  source_url?: string;
  format?: string;
  tags?: string[];
}): Promise<ApiDocument> {
  return request<ApiDocument>('/api/documents', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function getDocument(id: string): Promise<ApiDocument> {
  return request<ApiDocument>(`/api/documents/${id}`);
}

export function patchDocument(id: string, patch: DocumentPatch): Promise<ApiDocument> {
  return request<ApiDocument>(`/api/documents/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
}

export function deleteDocument(id: string, hard = false): Promise<void> {
  return request<void>(`/api/documents/${id}${hard ? '?hard=true' : ''}`, { method: 'DELETE' });
}

export function setDocumentTags(id: string, tags: string[]): Promise<ApiDocument> {
  return request<ApiDocument>(`/api/documents/${id}/tags`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ tags }),
  });
}

export async function uploadDocument(
  file: File | Blob,
  filename: string,
  collectionId?: string | null,
): Promise<ApiDocument> {
  const form = new FormData();
  form.append('file', file, filename);
  if (collectionId) form.append('collection_id', collectionId);
  return request<ApiDocument>('/api/documents/upload', { method: 'POST', body: form });
}

// ---------- 后台全文重索引 ----------

export interface ReindexStatus {
  running: boolean;
  total: number;
  done: number;
  updated: number;
  skipped: number;
  empty: number;
  sha_backfilled: number;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export function startReindex(): Promise<{ status: string } | Record<string, number>> {
  return request('/api/documents/reindex-content', { method: 'POST' });
}

export function getReindexStatus(): Promise<ReindexStatus> {
  return request<ReindexStatus>('/api/documents/reindex-status');
}

export function listCollections(): Promise<ApiCollection[]> {
  return request<ApiCollection[]>('/api/collections');
}export function createCollection(name: string, sortOrder = 0): Promise<ApiCollection> {
  return request<ApiCollection>('/api/collections', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name, sort_order: sortOrder }),
  });
}

export function deleteCollection(id: string): Promise<void> {
  return request<void>(`/api/collections/${id}`, { method: 'DELETE' });
}

// ---------- reading records ----------

export function createReadingRecord(payload: {
  document_id: string;
  start_time: string;
  end_time: string;
  units_read: number;
}): Promise<unknown> {
  return request<unknown>('/api/reading-records', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

// ---------- 二进制（文件/封面）----------

export async function fetchDocumentFileArrayBuffer(id: string): Promise<ArrayBuffer | null> {
  const resp = await fetch(`${getApiBaseUrl()}/api/documents/${id}/file`, {
    headers: { 'X-API-Token': getApiToken() },
  });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
  return resp.arrayBuffer();
}

export async function fetchDocumentCoverBlobUrl(id: string): Promise<string | null> {
  const resp = await fetch(`${getApiBaseUrl()}/api/documents/${id}/cover`, {
    headers: { 'X-API-Token': getApiToken() },
  });
  if (resp.status === 404) return null;
  if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
  return URL.createObjectURL(await resp.blob());
}

// ---------- annotations ----------

export interface ApiAnnotation {
  id: string;
  document_id: string;
  type: 'bookmark' | 'highlight' | 'note';
  anchor: Record<string, unknown>;
  quote: string;
  content: string;
  color: string | null;
  created_at: string;
  updated_at: string;
}

export function listAnnotationsApi(documentId: string): Promise<ApiAnnotation[]> {
  const qs = new URLSearchParams({ document_id: documentId }).toString();
  return request<ApiAnnotation[]>(`/api/annotations?${qs}`);
}

export function createAnnotationApi(payload: {
  document_id: string;
  type: string;
  anchor: Record<string, unknown>;
  quote?: string;
  content?: string;
  color?: string;
}): Promise<ApiAnnotation> {
  return request<ApiAnnotation>('/api/annotations', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export function deleteAnnotationApi(id: string): Promise<void> {
  return request<void>(`/api/annotations/${id}`, { method: 'DELETE' });
}

// ---------- search / stats ----------

export interface SearchHit {
  id: string;
  title: string;
  type: string;
  snippet: string;
}

export function searchApi(q: string, type?: string): Promise<SearchHit[]> {
  const params: Record<string, string> = { q };
  if (type) params.type = type;
  const qs = new URLSearchParams(params).toString();
  return request<SearchHit[]>(`/api/search?${qs}`);
}

export interface StatsOut {
  total_documents: number;
  by_type: Record<string, number>;
  read_started: number;
  completed: number;
  total_reading_minutes: number;
  recent_days: Array<{ date: string; minutes: number }>;
  top_tags: Array<{ name: string; count: number }>;
}

export function getStats(): Promise<StatsOut> {
  return request<StatsOut>('/api/stats');
}

// ---------- 双链 / 图谱 ----------

export interface Backlink {
  id: string;
  title: string;
  type: string;
  snippet: string;
}

export function getBacklinks(id: string): Promise<Backlink[]> {
  return request<Backlink[]>(`/api/documents/${id}/backlinks`);
}

export function getUnlinkedMentions(id: string): Promise<Backlink[]> {
  return request<Backlink[]>(`/api/documents/${id}/unlinked-mentions`);
}

export function createLink(sourceId: string, targetId: string, type = 'mention'): Promise<unknown> {
  return request<unknown>('/api/links', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source_id: sourceId, target_id: targetId, type }),
  });
}

// ---------- 文档版本历史 ----------

export interface DocumentVersionSummary {
  id: string;
  title: string;
  preview: string;
  created_at: string;
}

export interface DocumentVersionDetail {
  id: string;
  document_id: string;
  title: string;
  content: string;
  created_at: string;
}

export function getVersions(docId: string): Promise<DocumentVersionSummary[]> {
  return request<DocumentVersionSummary[]>(`/api/documents/${docId}/versions`);
}

export function getVersion(docId: string, versionId: string): Promise<DocumentVersionDetail> {
  return request<DocumentVersionDetail>(`/api/documents/${docId}/versions/${versionId}`);
}

export function restoreVersion(docId: string, versionId: string): Promise<ApiDocument> {
  return request<ApiDocument>(`/api/documents/${docId}/versions/${versionId}/restore`, {
    method: 'POST',
  });
}

// ---------- 标注搜索（书籍引用）----------

export interface AnnotationHit {
  id: string;
  document_id: string;
  document_title: string;
  document_type: string;
  type: string;
  anchor: Record<string, unknown>;
  quote: string;
  content: string;
  created_at: string;
}

export function searchAnnotations(q: string, limit = 20): Promise<AnnotationHit[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return request<AnnotationHit[]>(`/api/annotations/search?${params}`);
}

// ---------- 健康面板 / 作者聚合 ----------

export interface HealthItem {
  id: string;
  title: string;
  type: string;
}

export interface HealthOut {
  orphans: HealthItem[];
  placeholders: Array<{ mention: string; count: number }>;
  unread: HealthItem[];
}

export function getHealthStats(): Promise<HealthOut> {
  return request<HealthOut>('/api/stats/health');
}

export interface AuthorStat {
  name: string;
  count: number;
}

export function getAuthors(): Promise<AuthorStat[]> {
  return request<AuthorStat[]>('/api/stats/authors');
}

export interface GraphData {
  nodes: Array<{ id: string; title: string; type: string }>;
  edges: Array<{ source: string; target: string; type: string }>;
}

export function getGraph(): Promise<GraphData> {
  return request<GraphData>('/api/links/graph');
}

// ---------- 备份 / 剪藏 ----------

export async function downloadBackup(): Promise<void> {
  const resp = await fetch(`${getApiBaseUrl()}/api/backup`, {
    headers: { 'X-API-Token': getApiToken() },
  });
  if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `knowledge_base_backup_${new Date().toISOString().slice(0, 10)}.zip`;
  a.click();
  URL.revokeObjectURL(url);
}

export interface RestoreResult {
  restored: Record<string, number>;
  files_restored: number;
}

export async function restoreBackup(file: File): Promise<RestoreResult> {
  const form = new FormData();
  form.append('file', file);
  return request<RestoreResult>('/api/backup/restore', { method: 'POST', body: form });
}

// ---------- Markdown 导出 / 协作 ----------

export async function downloadMarkdownExport(): Promise<void> {
  const resp = await fetch(`${getApiBaseUrl()}/api/export/markdown`, {
    headers: { 'X-API-Token': getApiToken() },
  });
  if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `knowledge_base_export_${new Date().toISOString().slice(0, 10)}.zip`;
  a.click();
  URL.revokeObjectURL(url);
}

export function getCollabWsUrl(_docId: string): string {
  return `${getApiBaseUrl().replace(/^http/, 'ws')}/ws/collab`;
}

export interface ClipResult {
  id: string;
  title: string;
  url: string;
  excerpt: string;
}

export function clipUrl(url: string, title?: string, tags: string[] = []): Promise<ClipResult> {
  return request<ClipResult>('/api/clip', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ url, title, tags }),
  });
}

// ---------- 迁移 ----------

export interface MigrateMetaPayload {
  books: unknown[];
  categories: unknown[];
  bookmarks: unknown[];
  notes: unknown[];
  readingRecords: unknown[];
}

export interface MigrateMetaResult {
  collections: number;
  documents: Array<{ old_id: string; new_id: string }>;
  annotations: number;
  reading_records: number;
  tags: number;
}

export function migrateMeta(payload: MigrateMetaPayload): Promise<MigrateMetaResult> {
  return request<MigrateMetaResult>('/api/migrate/meta', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

export async function migrateFile(docId: string, blob: Blob, filename: string): Promise<void> {
  const form = new FormData();
  form.append('doc_id', docId);
  form.append('file', blob, filename);
  await request<void>('/api/migrate/file', { method: 'POST', body: form });
}

export async function migrateCover(docId: string, blob: Blob, filename: string): Promise<void> {
  const form = new FormData();
  form.append('doc_id', docId);
  form.append('file', blob, filename);
  await request<void>('/api/migrate/cover', { method: 'POST', body: form });
}
