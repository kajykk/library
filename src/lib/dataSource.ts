/**
 * 统一数据源：API 优先，IndexedDB 兜底
 * 后端可用（健康检查通过且已配置 Token）→ 走 REST API；
 * 否则回退本地 IndexedDB（原纯前端模式）。
 */

import {
  Book,
  Category,
  PendingWrite,
  initDB,
  getBooks,
  getBookById as dbGetBookById,
  addBook,
  updateBook as dbUpdateBook,
  deleteBook as dbDeleteBook,
  getCategories,
  addCategory,
  deleteCategory as dbDeleteCategory,
  addReadingRecord,
  addPendingWrite,
  getPendingWrites,
  deletePendingWrite,
} from './db';
import {
  storeFile,
  storeCover,
  deleteFile,
  deleteCover,
  getCoverUrl,
  extractMetadataFromFile,
  getFileArrayBuffer as localGetFileArrayBuffer,
} from './fileStorage';
import {
  ApiError,
  ApiAnnotation,
  ApiCollection,
  ApiDocument,
  annotationToNote,
  checkHealth,
  countDocuments,
  createAnnotationApi,
  createCollection,
  createReadingRecord,
  deleteAnnotationApi,
  deleteCollection,
  deleteDocument,
  fetchDocumentCoverBlobUrl,
  fetchDocumentFileArrayBuffer,
  getApiToken,
  getDocument,
  getLibraryStats,
  listAnnotationsApi,
  listCollections,
  listDocuments,
  patchDocument,
  setDocumentTags,
  uploadDocument,
} from './api/client';
import {
  Bookmark,
  Note as DbNote,
  addBookmark,
  getBookmarksByBookId,
  deleteBookmark,
  addNote,
  getNotesByBookId,
  deleteNote,
} from './db';

export type DataSourceMode = 'api' | 'local';

/** RFC4122 v4 id：浏览器/Node20 原生支持，替代 uuid 依赖 */
function generateId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  // 极老环境兜底：手工拼接 v4 格式
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === 'x' ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

let modeCache: DataSourceMode | null = null;

export function resetModeCache(): void {
  modeCache = null;
}

export async function resolveMode(force = false): Promise<DataSourceMode> {
  if (modeCache !== null && !force) return modeCache;
  modeCache = getApiToken() && (await checkHealth()) ? 'api' : 'local';
  return modeCache;
}

// ---------- 映射 ----------

function apiDocToBook(doc: ApiDocument, collectionNames: Map<string, string>): Book {
  const meta = (doc.meta || {}) as { totalPages?: number };
  const position = (doc.position || null) as { page?: number } | null;
  return {
    id: doc.id,
    title: doc.title,
    author: doc.author || '未知作者',
    publisher: doc.publisher || '',
    category: (doc.collection_id && collectionNames.get(doc.collection_id)) || '未分类',
    format: doc.format || '',
    filePath: doc.file_path || '',
    coverPath: doc.cover_path || null,
    description: doc.description || '',
    addedAt: Date.parse(doc.created_at) || 0,
    lastReadAt: doc.last_read_at ? Date.parse(doc.last_read_at) : null,
    readProgress: Math.round((doc.read_progress || 0) * 100),
    lastPosition: position?.page ?? null,
    totalPages: meta.totalPages || 0,
    fileSize: doc.file_size || 0,
    language: doc.language || 'zh',
    isbn: doc.isbn || '',
    tags: (doc.tags || []).map((t) => t.name),
  };
}

function apiCollectionToCategory(c: ApiCollection): Category {
  return { id: c.id, name: c.name, parentId: c.parent_id, sortOrder: c.sort_order };
}

// ---------- 查询 ----------

/** 书架筛选项（与页面智能书架一致） */
export type ShelfFilter = 'all' | 'reading' | 'unread' | 'done' | 'recent';

export interface LibraryPageQuery {
  offset: number;
  limit: number;
  search?: string;
  /** 分类名（页面按名字选分类，API 侧再解析成 collection_id） */
  category?: string | null;
  author?: string | null;
  shelf?: ShelfFilter;
}

export interface LibraryPage {
  books: Book[];
  hasMore: boolean;
  /** 当前筛选条件下的总数；分类名在后端不存在时为 0 */
  total: number;
}

export interface LibraryMeta {
  categories: Category[];
  mode: DataSourceMode;
  /** 书架总册数（只算 book/pdf） */
  total: number;
  shelfCounts: { all: number; reading: number; unread: number; done: number; recent: number };
  /** key = 分类名，与 Category.name / Book.category 对齐 */
  categoryCounts: Map<string, number>;
  /** API 模式下本地仍有未迁移书籍时为其数量，用于提示 */
  migrationHint: number;
}

const BOOK_TYPES = ['book', 'pdf'];
const UNCATEGORIZED = '未分类';

/** 阅读状态分桶：与后端 documents.apply_status_filter 保持一致 */
function bookStatus(book: Book): 'reading' | 'unread' | 'done' {
  const progress = book.readProgress ?? 0;
  if (progress >= 100) return 'done';
  if (progress > 0) return 'reading';
  return 'unread';
}

function matchesLocalFilters(book: Book, query: LibraryPageQuery): boolean {
  const normalizedQuery = (query.search || '').trim().toLowerCase();
  if (normalizedQuery) {
    const searchable = [book.title, book.author, book.publisher, book.description]
      .join(' ')
      .toLowerCase();
    if (!searchable.includes(normalizedQuery)) return false;
  }
  if (query.category && book.category !== query.category) return false;
  if (query.author && !(book.author || '').includes(query.author)) return false;
  if (query.shelf && query.shelf !== 'all') {
    if (query.shelf === 'recent') return true; // 由调用方先截取最新 N 本
    if (bookStatus(book) !== query.shelf) return false;
  }
  return true;
}

export async function loadLibraryMeta(): Promise<LibraryMeta> {
  const mode = await resolveMode();

  if (mode === 'local') {
    await initDB();
    const [books, categories] = await Promise.all([getBooks(), getCategories()]);
    const counts = { all: books.length, reading: 0, unread: 0, done: 0, recent: 0 };
    const categoryCounts = new Map<string, number>();
    books.forEach((b) => {
      counts[bookStatus(b)] += 1;
      categoryCounts.set(b.category, (categoryCounts.get(b.category) || 0) + 1);
    });
    return {
      categories,
      mode,
      total: books.length,
      shelfCounts: counts,
      categoryCounts,
      migrationHint: 0,
    };
  }

  // 后端恢复可用时，重放离线期间积压的写操作
  flushPendingWrites().catch((err) => console.warn('重放离线队列失败:', err));

  const [collections, stats, localBooks] = await Promise.all([
    listCollections(),
    getLibraryStats(),
    countLocalBooks(),
  ]);
  const nameById = new Map(collections.map((c) => [c.id, c.name]));
  const categoryCounts = new Map<string, number>();
  for (const [collectionId, count] of Object.entries(stats.by_collection)) {
    const name = nameById.get(collectionId);
    if (!name) continue;
    categoryCounts.set(name, count);
  }
  if (stats.uncategorized > 0) {
    categoryCounts.set(UNCATEGORIZED, stats.uncategorized);
  }
  return {
    categories: collections.map(apiCollectionToCategory),
    mode,
    total: stats.total,
    shelfCounts: {
      all: stats.total,
      reading: stats.reading,
      unread: stats.unread,
      done: stats.done,
      recent: 0, // 由页面按 total 与 RECENT_SHELF_SIZE 计算
    },
    categoryCounts,
    migrationHint: stats.total === 0 ? localBooks : 0,
  };
}

/** 分页取书架：API 模式把筛选条件下推到后端（offset/limit），本地模式在内存里过滤后切片 */
export async function loadLibraryPage(query: LibraryPageQuery): Promise<LibraryPage> {
  const mode = await resolveMode();

  if (mode === 'local') {
    await initDB();
    const all = (await getBooks()).sort((a, b) => (b.addedAt ?? 0) - (a.addedAt ?? 0));
    const base = query.shelf === 'recent' ? all.slice(0, query.limit) : all;
    const filtered = base.filter((book) => matchesLocalFilters(book, query));
    const slice = filtered.slice(query.offset, query.offset + query.limit);
    return {
      books: slice,
      hasMore: query.offset + slice.length < filtered.length,
      total: filtered.length,
    };
  }

  const category = query.category;
  let collectionId: string | null = null;
  if (category) {
    const collections = await listCollections();
    const found = collections.find((c) => c.name === category);
    // 分类名在后端不存在（已删除/改名的旧状态）：直接给空结果，避免退化成"全部书籍"
    if (!found) return { books: [], hasMore: false, total: 0 };
    collectionId = found.id;
  }

  const params: Record<string, string> = {
    types: BOOK_TYPES.join(','),
    include_content: 'false',
    limit: String(query.limit),
    offset: String(query.offset),
  };
  if (collectionId) params.collection_id = collectionId;
  // 最近添加 = 按 created_at 倒序的第一页，不需要额外条件
  if (query.shelf && query.shelf !== 'all' && query.shelf !== 'recent') {
    params.status = query.shelf;
  }
  if (query.search?.trim()) params.q = query.search.trim();
  if (query.author) params.author = query.author;

  const [docs, total] = await Promise.all([
    listDocuments(params),
    countDocuments(params),
  ]);
  const nameById = await getCollectionNames();
  // 最近添加是一次性视图：只取第一页，总数即本页条数
  if (query.shelf === 'recent') {
    return {
      books: docs.map((d) => apiDocToBook(d, nameById)),
      hasMore: false,
      total: Math.min(query.limit, total),
    };
  }
  return {
    books: docs.map((d) => apiDocToBook(d, nameById)),
    hasMore: query.offset + docs.length < total,
    total,
  };
}

// ---------- 离线写队列 ----------

async function queueOfflineWrite(kind: PendingWrite['kind'], payload: PendingWrite['payload']): Promise<void> {
  try {
    await initDB();
    await addPendingWrite({
      id: generateId(),
      kind,
      payload,
      createdAt: Date.now(),
    });
    console.info('后端不可用，写操作已进入离线队列', kind);
  } catch (err) {
    console.warn('离线队列入队失败:', err);
  }
}

/** 将离线积压的进度/阅读记录重放到后端，成功后出队。
 * 单飞锁：React Query refetch/多组件并发触发时只允许一个 flush 在跑，
 * 否则两批会读到同一队列、重复上报阅读记录（record 重放非幂等）。 */
let flushInFlight: Promise<number> | null = null;

export function flushPendingWrites(): Promise<number> {
  if (flushInFlight) return flushInFlight;
  flushInFlight = doFlushPendingWrites().finally(() => {
    flushInFlight = null;
  });
  return flushInFlight;
}

async function doFlushPendingWrites(): Promise<number> {
  await initDB();
  const pending = await getPendingWrites();
  let replayed = 0;
  for (const item of pending) {
    try {
      if (item.kind === 'progress') {
        await patchDocument(item.payload.bookId, {
          read_progress: Math.min(1, Math.max(0, (item.payload.progressPercent ?? 0) / 100)),
          position: item.payload.position == null ? null : { page: item.payload.position },
          last_read_at: new Date().toISOString(),
        });
      } else if (item.kind === 'record') {
        await createReadingRecord({
          document_id: item.payload.bookId,
          start_time: new Date(item.payload.startTimeMs ?? Date.now()).toISOString(),
          end_time: new Date(item.payload.endTimeMs ?? Date.now()).toISOString(),
          units_read: item.payload.unitsRead ?? 0,
        });
      }
      await deletePendingWrite(item.id);
      replayed += 1;
    } catch (err) {
      console.warn('重放失败，保留在队列中:', item.id, err);
    }
  }
  return replayed;
}

/** API 模式下检查本地是否有未迁移数据（用于提示） */
export async function countLocalBooks(): Promise<number> {
  try {
    await initDB();
    return (await getBooks()).length;
  } catch {
    return 0;
  }
}

/** collection 名称缓存：getBookById 高频调用（每次打开阅读器），
 * 无需为映射一个分类名而反复全量拉取 collections。
 * TTL 60s；写操作路径会调用 invalidateCollectionCache 保持新鲜。 */
let collectionNameCache: Map<string, string> | null = null;
let collectionNameCacheAt = 0;
const COLLECTION_CACHE_TTL_MS = 60_000;

async function getCollectionNames(): Promise<Map<string, string>> {
  if (collectionNameCache && Date.now() - collectionNameCacheAt < COLLECTION_CACHE_TTL_MS) {
    return collectionNameCache;
  }
  const collections = await listCollections();
  collectionNameCache = new Map(collections.map((c) => [c.id, c.name]));
  collectionNameCacheAt = Date.now();
  return collectionNameCache;
}

function invalidateCollectionCache(): void {
  collectionNameCache = null;
  collectionNameCacheAt = 0;
}

/** 按 id 获取单本书（reader 路由用） */
export async function getBookById(bookId: string): Promise<Book | null> {
  const mode = await resolveMode();
  if (mode === 'api') {
    try {
      const doc = await getDocument(bookId);
      const nameById = await getCollectionNames();
      return apiDocToBook(doc, nameById);
    } catch (err) {
      console.warn('获取书籍详情失败:', err);
      return null;
    }
  }
  await initDB();
  return (await dbGetBookById(bookId)) || null;
}

// ---------- 写操作 ----------

async function ensureApiCollection(name: string): Promise<string> {
  const collections = await listCollections();
  const found = collections.find((c) => c.name === name);
  if (found) return found.id;
  const created = await createCollection(name, collections.length);
  invalidateCollectionCache();
  return created.id;
}

async function addLocalCategoryIfNotExists(name: string): Promise<void> {
  const current = await getCategories();
  if (!current.some((c) => c.name === name)) {
    await addCategory({ id: generateId(), name, parentId: null, sortOrder: current.length });
  }
}

/** 导入书籍文件：API 模式由后端提取元数据；本地模式沿用前端提取 */
export async function importBookFile(file: File, categoryName: string): Promise<Book> {
  const mode = await resolveMode();

  if (mode === 'api') {
    const collectionId = await ensureApiCollection(categoryName);
    const doc = await uploadDocument(file, file.name, collectionId);
    const nameById = new Map([[doc.collection_id || '', categoryName]]);
    return apiDocToBook(doc, nameById);
  }

  await initDB();
  const format = file.name.split('.').pop()?.toLowerCase() || '';
  const id = generateId();
  const metadata = await extractMetadataFromFile(file);
  await Promise.all([
    storeFile(id, file),
    metadata.cover ? storeCover(id, metadata.cover) : Promise.resolve(),
  ]);
  const book: Book = {
    id,
    title: metadata.title,
    author: metadata.author,
    publisher: metadata.publisher,
    category: categoryName,
    format,
    filePath: id,
    coverPath: metadata.cover ? id : null,
    description: metadata.description,
    addedAt: Date.now(),
    lastReadAt: null,
    readProgress: 0,
    lastPosition: null,
    totalPages: 0,
    fileSize: file.size,
    language: metadata.language,
    isbn: metadata.isbn,
    tags: [],
  };
  await addLocalCategoryIfNotExists(categoryName);
  await addBook(book);
  return book;
}

/** 删除书籍：API 模式软删除；本地模式连带清理文件与封面 */
export async function deleteBookCompletely(book: Book): Promise<void> {
  const mode = await resolveMode();
  if (mode === 'api') {
    await deleteDocument(book.id);
    return;
  }
  await dbDeleteBook(book.id);
  await deleteFile(book.id);
  if (book.coverPath) {
    await deleteCover(book.id);
  }
}

/** 编辑保存（书名/作者/分类/标签等） */
export async function saveBookEdit(book: Book): Promise<void> {
  const mode = await resolveMode();
  if (mode === 'api') {
    const collectionId = await ensureApiCollection(book.category);
    await patchDocument(book.id, {
      title: book.title,
      author: book.author,
      publisher: book.publisher,
      isbn: book.isbn,
      language: book.language,
      description: book.description,
      collection_id: collectionId,
    });
    await setDocumentTags(book.id, book.tags);
    invalidateCollectionCache();
    return;
  }
  await dbUpdateBook(book);
}

export async function addCategoryByName(name: string): Promise<Category> {
  const mode = await resolveMode();
  if (mode === 'api') {
    try {
      const created = await createCollection(name);
      invalidateCollectionCache();
      return apiCollectionToCategory(created);
    } catch (err) {
      // 按状态码判断：此前用 message.includes('409') 是死分支 —— Error 只带 detail 文本，
      // 字符串里不会出现状态码，用户看到的永远是英文原文
      const status = err instanceof ApiError ? err.status : undefined;
      throw new Error(status === 409 ? `分类"${name}"已存在` : (err as Error).message);
    }
  }
  await initDB();
  const current = await getCategories();
  if (current.some((c) => c.name === name)) {
    throw new Error(`分类"${name}"已存在`);
  }
  const category: Category = { id: generateId(), name, parentId: null, sortOrder: current.length };
  await addCategory(category);
  return category;
}

/** 删除分类：API 模式后端将书籍移出分类；本地模式书籍归入"未分类" */
export async function deleteCategoryAndReset(category: Category): Promise<void> {
  const mode = await resolveMode();
  if (mode === 'api') {
    await deleteCollection(category.id);
    invalidateCollectionCache();
    return;
  }
  await initDB();
  await dbDeleteCategory(category.id);
  const books = await getBooks();
  const affected = books.filter((b) => b.category === category.name);
  if (affected.length > 0) {
    await addLocalCategoryIfNotExists('未分类');
    await Promise.all(affected.map((b) => dbUpdateBook({ ...b, category: '未分类' })));
  }
}

// ---------- 阅读器 ----------

export async function getFileArrayBuffer(bookId: string): Promise<ArrayBuffer | null> {
  const mode = await resolveMode();
  if (mode === 'api') {
    return fetchDocumentFileArrayBuffer(bookId);
  }
  await initDB();
  return localGetFileArrayBuffer(bookId);
}

export async function getBookCoverUrl(bookId: string): Promise<string | null> {
  const mode = await resolveMode();
  if (mode === 'api') {
    return fetchDocumentCoverBlobUrl(bookId);
  }
  await initDB();
  return getCoverUrl(bookId);
}

/** 保存阅读进度（前端百分比 0~100 + 位置索引），API 失败时进入离线队列 */
export async function saveReadingProgress(
  bookId: string,
  progressPercent: number,
  position: number | null,
): Promise<void> {
  const mode = await resolveMode();
  if (mode === 'api') {
    try {
      await patchDocument(bookId, {
        read_progress: Math.min(1, Math.max(0, progressPercent / 100)),
        position: position === null ? null : { page: position },
        last_read_at: new Date().toISOString(),
      });
    } catch (err) {
      console.warn('保存进度失败，已进入离线队列:', err);
      await queueOfflineWrite('progress', { bookId, progressPercent, position });
    }
    return;
  }
  await initDB();
  const book = await getBookById(bookId);
  if (book) {
    await dbUpdateBook({
      ...book,
      readProgress: Math.min(100, Math.max(0, Math.round(progressPercent))),
      lastPosition: position,
      lastReadAt: Date.now(),
    });
  }
}

/** 记录一次阅读会话（时长/阅读量），API 失败时进入离线队列 */
export async function saveReadingSession(
  bookId: string,
  startTimeMs: number,
  endTimeMs: number,
  unitsRead: number,
): Promise<void> {
  const mode = await resolveMode();
  if (mode === 'api') {
    try {
      await createReadingRecord({
        document_id: bookId,
        start_time: new Date(startTimeMs).toISOString(),
        end_time: new Date(endTimeMs).toISOString(),
        units_read: unitsRead,
      });
    } catch (err) {
      console.warn('保存阅读记录失败，已进入离线队列:', err);
      await queueOfflineWrite('record', { bookId, startTimeMs, endTimeMs, unitsRead });
    }
    return;
  }
  await initDB();
  await addReadingRecord({
    id: generateId(),
    bookId,
    startTime: startTimeMs,
    endTime: endTimeMs,
    pagesRead: unitsRead,
  });
}

// ---------- 统一批注（书签 / 划线 / 笔记）----------

export interface UnifiedAnnotation {
  id: string;
  type: 'bookmark' | 'highlight' | 'note';
  /** 章节索引（EPUB/MOBI），用于划线恢复与跳转 */
  chapter: number;
  /** 页码/位置（PDF 页码、TXT 分页索引） */
  page: number;
  /** 划线摘录原文 */
  quote: string;
  /** 书签说明 / 笔记内容 */
  content: string;
  createdAt: number;
}

function apiAnnToUnified(a: ApiAnnotation): UnifiedAnnotation {
  const anchor = (a.anchor || {}) as { chapter?: number; page?: number };
  return {
    id: a.id,
    type: a.type,
    chapter: anchor.chapter ?? anchor.page ?? 0,
    page: anchor.page ?? anchor.chapter ?? 0,
    quote: a.quote || '',
    content: a.content || '',
    createdAt: Date.parse(a.created_at) || 0,
  };
}

export async function listAnnotations(bookId: string): Promise<UnifiedAnnotation[]> {
  const mode = await resolveMode();
  if (mode === 'api') {
    return (await listAnnotationsApi(bookId)).map(apiAnnToUnified);
  }
  await initDB();
  const [bookmarks, notes] = await Promise.all([
    getBookmarksByBookId(bookId),
    getNotesByBookId(bookId),
  ]);
  const fromBookmarks: UnifiedAnnotation[] = bookmarks.map((b: Bookmark) => ({
    id: b.id,
    type: 'bookmark',
    chapter: b.page,
    page: b.page,
    quote: '',
    content: b.note,
    createdAt: b.createdAt,
  }));
  const fromNotes: UnifiedAnnotation[] = notes.map((n: DbNote) => ({
    id: n.id,
    type: 'note',
    chapter: n.page,
    page: n.page,
    quote: '',
    content: n.content,
    createdAt: n.createdAt,
  }));
  return [...fromBookmarks, ...fromNotes].sort((a, b) => b.createdAt - a.createdAt);
}

/** 添加书签 */
export async function addBookmarkAnnotation(
  bookId: string,
  page: number,
  chapter: number,
  note: string,
): Promise<UnifiedAnnotation> {
  const mode = await resolveMode();
  const anchor = { page, chapter };
  if (mode === 'api') {
    return apiAnnToUnified(
      await createAnnotationApi({ document_id: bookId, type: 'bookmark', anchor, content: note }),
    );
  }
  await initDB();
  const bookmark: Bookmark = {
    id: generateId(),
    bookId,
    page,
    note,
    createdAt: Date.now(),
  };
  await addBookmark(bookmark);
  return { id: bookmark.id, type: 'bookmark', chapter, page, quote: '', content: note, createdAt: bookmark.createdAt };
}

/** 添加页面级笔记（非划线） */
export async function addNoteAnnotation(
  bookId: string,
  page: number,
  chapter: number,
  content: string,
): Promise<UnifiedAnnotation> {
  const mode = await resolveMode();
  const anchor = { page, chapter };
  if (mode === 'api') {
    return apiAnnToUnified(
      await createAnnotationApi({ document_id: bookId, type: 'note', anchor, content }),
    );
  }
  await initDB();
  const note: DbNote = {
    id: generateId(),
    bookId,
    page,
    content,
    createdAt: Date.now(),
    updatedAt: Date.now(),
  };
  await addNote(note);
  return { id: note.id, type: 'note', chapter, page, quote: '', content, createdAt: note.createdAt };
}

/** 正文划线高亮（本地模式不支持，需要后端 annotations 存储） */
export async function createHighlight(
  bookId: string,
  chapter: number,
  page: number,
  quote: string,
  note = '',
): Promise<UnifiedAnnotation> {
  const mode = await resolveMode();
  if (mode === 'local') {
    throw new Error('划线高亮需要连接后端服务');
  }
  return apiAnnToUnified(
    await createAnnotationApi({
      document_id: bookId,
      type: 'highlight',
      anchor: { chapter, page },
      quote,
      content: note,
      color: 'yellow',
    }),
  );
}

export async function deleteAnnotation(annotation: UnifiedAnnotation): Promise<void> {
  const mode = await resolveMode();
  if (mode === 'api') {
    await deleteAnnotationApi(annotation.id);
    return;
  }
  await initDB();
  if (annotation.type === 'bookmark') {
    await deleteBookmark(annotation.id);
  } else {
    await deleteNote(annotation.id);
  }
}

/** 标注一键转笔记（API 模式）。返回新笔记 id 与标题；本地模式不支持。 */
export async function convertAnnotationToNote(
  annotationId: string,
): Promise<{ noteId: string; noteTitle: string }> {
  const mode = await resolveMode();
  if (mode === 'local') {
    throw new Error('转为笔记需要连接后端服务');
  }
  const doc = await annotationToNote(annotationId);
  return { noteId: doc.id, noteTitle: doc.title };
}
