/**
 * 后端 API client
 * 地址优先取 localStorage（设置页可改），回退构建时环境变量；
 * Token 只取 localStorage —— 见下方 getApiToken 的安全说明。
 */

export function getApiBaseUrl(): string {
  if (typeof window !== 'undefined') {
    const stored = window.localStorage.getItem('kb_api_base');
    if (stored) return stored.replace(/\/$/, '');
  }
  return (process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000').replace(/\/$/, '');
}

/**
 * 读取 API Token。
 *
 * 安全约束（勿回退）：此函数**刻意不读** NEXT_PUBLIC_API_TOKEN。
 * NEXT_PUBLIC_* 变量会在 `next build` 时内联进客户端 JS bundle，任何以它命名的
 * 密钥都等价于公开发布（view-source 即可取得全站写权限）。Token 只能来自
 * 运行时写入的 localStorage（设置页 saveApiConfig），否则留空由后端返回 401。
 */
export function getApiToken(): string {
  if (typeof window !== 'undefined') {
    return window.localStorage.getItem('kb_api_token') || '';
  }
  return '';
}

export function saveApiConfig(baseUrl: string, token: string): void {
  window.localStorage.setItem('kb_api_base', baseUrl.replace(/\/$/, ''));
  window.localStorage.setItem('kb_api_token', token);
}

/**
 * 带状态码的错误类型。
 *
 * 此前只抛 `new Error(message)`，状态码在字符串化时就丢了 —— 调用方只能靠
 * `message.includes('409')` 这种不可靠的字符串匹配（实际上永远不会命中，
 * message 里只有后端的 detail 文本）。现在调用方按 `err.status` 判断。
 */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
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
    throw new ApiError(resp.status, message);
  }
  if (resp.status === 204) return undefined as T;
  return (await resp.json()) as T;
}

export interface SystemHealth {
  status: 'ok' | 'degraded' | 'error';
  checks: Record<string, string>;
}

export async function checkHealth(): Promise<boolean> {
  try {
    const resp = await fetch(`${getApiBaseUrl()}/api/health`);
    return resp.ok;
  } catch {
    return false;
  }
}

export async function fetchSystemHealth(): Promise<SystemHealth> {
  const resp = await fetch(`${getApiBaseUrl()}/api/health`);
  if (!resp.ok) throw new Error(`健康检查失败：${resp.status}`);
  return (await resp.json()) as SystemHealth;
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

export interface DocumentInsight {
  summary: string;
  suggested_tags: string[];
}

export function getDocumentInsight(id: string): Promise<DocumentInsight> {
  return request<DocumentInsight>(`/api/documents/${id}/insight`);
}

// ---------- documents / collections ----------

/**
 * 列表接口的单页上限（与后端 `documents.LIST_HARD_LIMIT` 对齐）。
 *
 * 后端未显式传 limit 时按默认页大小（200）截断，避免任何一次列表都把整库读进内存。
 * 真正需要全量的场景（书架、命令面板、双链补全）必须显式传这个值；
 * 只做"找最近的 N 条"的场景不传，接受默认分页。
 */
export const LIST_HARD_LIMIT = 1000;

export function listDocuments(params?: Record<string, string>): Promise<ApiDocument[]> {
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return request<ApiDocument[]>(`/api/documents${qs}`);
}

export interface ApiLibraryStats {
  total: number;
  reading: number;
  unread: number;
  done: number;
  /** key = collection_id */
  by_collection: Record<string, number>;
  uncategorized: number;
}

/** 书架侧栏计数：服务端聚合一次取全，前端无需为算数量而拉整库 */
export function getLibraryStats(): Promise<ApiLibraryStats> {
  return request<ApiLibraryStats>('/api/documents/stats');
}

/** 与 listDocuments 同参的计数，用于"共 N 本"/剩余量显示 */
export function countDocuments(params?: Record<string, string>): Promise<number> {
  const qs = params ? '?' + new URLSearchParams(params).toString() : '';
  return request<{ count: number }>(`/api/documents/count${qs}`).then((r) => r.count);
}

/**
 * 按 offset 逐页取完整结果集。
 *
 * 后端单页硬上限是 LIST_HARD_LIMIT，超过就 422，所以"全量"语义不能靠一次大 limit
 * 表达 —— 库里有 1300+ 本时单请求最多只能拿到 1000 条，剩下的静默消失。
 * 双链补全、命令面板这类确实需要全量的场景走这里翻页。
 */
export async function listAllDocuments(
  params?: Record<string, string>,
): Promise<ApiDocument[]> {
  const all: ApiDocument[] = [];
  for (let offset = 0; ; offset += LIST_HARD_LIMIT) {
    const page = await listDocuments({
      ...params,
      limit: String(LIST_HARD_LIMIT),
      offset: String(offset),
    });
    all.push(...page);
    if (page.length < LIST_HARD_LIMIT) return all;
    // 防御：后端异常时避免无限翻页
    if (offset > LIST_HARD_LIMIT * 100) return all;
  }
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
}

export function createCollection(name: string, sortOrder = 0): Promise<ApiCollection> {
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

export interface SearchSummary {
  query: string;
  total: number;
  by_type: Record<string, number>;
  suggestions: string[];
}

export function searchApi(q: string, type?: string): Promise<SearchHit[]> {
  const params: Record<string, string> = { q };
  if (type) params.type = type;
  const qs = new URLSearchParams(params).toString();
  return request<SearchHit[]>(`/api/search?${qs}`);
}

export function getSearchSummary(q: string): Promise<SearchSummary> {
  const params = new URLSearchParams({ q });
  return request<SearchSummary>(`/api/search/summary?${params}`);
}

export interface StatsOut {
  total_documents: number;
  by_type: Record<string, number>;
  read_started: number;
  completed: number;
  total_reading_minutes: number;
  recent_days: Array<{ date: string; minutes: number }>;
  heatmap: Array<{ date: string; minutes: number }>;
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

// ---------- 标注 → 知识流转（每日回顾 / 转笔记 / 导出） ----------

export interface ReviewParams {
  since?: string;
  type?: 'bookmark' | 'highlight' | 'note';
  limit?: number;
  offset?: number;
}

export function reviewAnnotations(params: ReviewParams = {}): Promise<AnnotationHit[]> {
  const qs: Record<string, string> = {};
  if (params.since) qs.since = params.since;
  if (params.type) qs.type = params.type;
  if (params.limit != null) qs.limit = String(params.limit);
  if (params.offset != null) qs.offset = String(params.offset);
  const query = new URLSearchParams(qs).toString();
  return request<AnnotationHit[]>(`/api/annotations/review${query ? `?${query}` : ''}`);
}

/** 标注一键转笔记：返回新笔记文档（含 [[书名]] 双链与来源锚点） */
export function annotationToNote(annotationId: string): Promise<ApiDocument> {
  return request<ApiDocument>(`/api/annotations/${annotationId}/to-note`, { method: 'POST' });
}

/** 通用二进制下载（带鉴权头），触发浏览器保存 */
async function downloadAttachment(path: string, filename: string): Promise<void> {
  const resp = await fetch(`${getApiBaseUrl()}${path}`, {
    headers: { 'X-API-Token': getApiToken() },
  });
  if (!resp.ok) throw new Error(`API ${resp.status}: ${resp.statusText}`);
  const blob = await resp.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 同步 revoke 会在部分浏览器上取消尚未开始的下载（封面 blob URL 那套生命周期
  // 管理就是为此写的）。推迟到下一轮事件循环再释放。
  setTimeout(() => URL.revokeObjectURL(url), 0);
}

/** 全部标注导出：按书分组 Markdown zip */
export async function downloadAnnotationsExport(): Promise<void> {
  await downloadAttachment(
    '/api/export/annotations',
    `annotations_export_${new Date().toISOString().slice(0, 10)}.zip`,
  );
}

/** 标注 TSV 导出（Anki/表格可导入） */
export async function downloadAnnotationsTsv(): Promise<void> {
  await downloadAttachment(
    '/api/export/annotations.tsv',
    `annotations_${new Date().toISOString().slice(0, 10)}.tsv`,
  );
}

// ---------- 在线元数据 ----------

export interface MetadataCandidate {
  source: string;
  title: string;
  author: string;
  publisher: string;
  isbn: string;
  description: string;
  language: string;
  year: string;
  cover_url: string | null;
}

export function searchMetadataOnline(q: string, limit = 6): Promise<MetadataCandidate[]> {
  const params = new URLSearchParams({ q, limit: String(limit) });
  return request<MetadataCandidate[]>(`/api/documents/metadata/search?${params}`);
}

export interface MetadataApplyPayload {
  title?: string;
  author?: string;
  publisher?: string;
  isbn?: string;
  description?: string;
  language?: string;
  cover_url?: string | null;
}

export function applyMetadata(docId: string, payload: MetadataApplyPayload): Promise<ApiDocument> {
  return request<ApiDocument>(`/api/documents/${docId}/metadata`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
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
