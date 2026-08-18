/**
 * 统一数据源：API 优先，IndexedDB 兜底
 * 后端可用（健康检查通过且已配置 Token）→ 走 REST API；
 * 否则回退本地 IndexedDB（原纯前端模式）。
 */

import { v4 as uuidv4 } from 'uuid';
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
  ApiAnnotation,
  ApiCollection,
  ApiDocument,
  checkHealth,
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

const BOOK_TYPES = new Set(['book', 'pdf']);

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

export interface LibraryData {
  books: Book[];
  categories: Category[];
  mode: DataSourceMode;
}

export async function loadLibrary(): Promise<LibraryData> {
  const mode = await resolveMode();
  if (mode === 'local') {
    await initDB();
    const [books, categories] = await Promise.all([getBooks(), getCategories()]);
    return { books, categories, mode };
  }

  // 后端恢复可用时，重放离线期间积压的写操作
  flushPendingWrites().catch((err) => console.warn('重放离线队列失败:', err));

  const [docs, collections] = await Promise.all([listDocuments(), listCollections()]);
  const nameById = new Map(collections.map((c) => [c.id, c.name]));
  const books = docs.filter((d) => BOOK_TYPES.has(d.type)).map((d) => apiDocToBook(d, nameById));
  const categories = collections.map(apiCollectionToCategory);
  return { books, categories, mode };
}

// ---------- 离线写队列 ----------

async function queueOfflineWrite(kind: PendingWrite['kind'], payload: PendingWrite['payload']): Promise<void> {
  try {
    await initDB();
    await addPendingWrite({
      id: uuidv4(),
      kind,
      payload,
      createdAt: Date.now(),
    });
    console.info('后端不可用，写操作已进入离线队列', kind);
  } catch (err) {
    console.warn('离线队列入队失败:', err);
  }
}

/** 将离线积压的进度/阅读记录重放到后端，成功后出队 */
export async function flushPendingWrites(): Promise<number> {
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

/** 按 id 获取单本书（reader 路由用） */
export async function getBookById(bookId: string): Promise<Book | null> {
  const mode = await resolveMode();
  if (mode === 'api') {
    try {
      const doc = await getDocument(bookId);
      const collections = await listCollections();
      return apiDocToBook(doc, new Map(collections.map((c) => [c.id, c.name])));
    } catch {
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
  return created.id;
}

async function addLocalCategoryIfNotExists(name: string): Promise<void> {
  const current = await getCategories();
  if (!current.some((c) => c.name === name)) {
    await addCategory({ id: uuidv4(), name, parentId: null, sortOrder: current.length });
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
  const id = uuidv4();
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
    return;
  }
  await dbUpdateBook(book);
}

export async function addCategoryByName(name: string): Promise<Category> {
  const mode = await resolveMode();
  if (mode === 'api') {
    try {
      return apiCollectionToCategory(await createCollection(name));
    } catch (err) {
      throw new Error((err as Error).message.includes('409') ? `分类"${name}"已存在` : (err as Error).message);
    }
  }
  await initDB();
  const current = await getCategories();
  if (current.some((c) => c.name === name)) {
    throw new Error(`分类"${name}"已存在`);
  }
  const category: Category = { id: uuidv4(), name, parentId: null, sortOrder: current.length };
  await addCategory(category);
  return category;
}

/** 删除分类：API 模式后端将书籍移出分类；本地模式书籍归入"未分类" */
export async function deleteCategoryAndReset(category: Category): Promise<void> {
  const mode = await resolveMode();
  if (mode === 'api') {
    await deleteCollection(category.id);
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
    id: uuidv4(),
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
    id: uuidv4(),
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
    id: uuidv4(),
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
