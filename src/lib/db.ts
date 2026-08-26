export interface Book {
  id: string;
  title: string;
  author: string;
  publisher: string;
  category: string;
  format: string;
  filePath: string;
  coverPath: string | null;
  description: string;
  addedAt: number;
  lastReadAt: number | null;
  readProgress: number;
  /** 精确恢复位置：PDF 为页码(1起)，EPUB/MOBI 为章节索引，TXT 为分页索引 */
  lastPosition?: number | null;
  totalPages: number;
  fileSize: number;
  language: string;
  isbn: string;
  tags: string[];
}

export interface Category {
  id: string;
  name: string;
  parentId: string | null;
  sortOrder: number;
}

export interface Bookmark {
  id: string;
  bookId: string;
  page: number;
  note: string;
  createdAt: number;
}

export interface ReadingRecord {
  id: string;
  bookId: string;
  startTime: number;
  endTime: number;
  pagesRead: number;
}

export interface Note {
  id: string;
  bookId: string;
  page: number;
  content: string;
  createdAt: number;
  updatedAt: number;
}

const DB_NAME = 'ebook_manager_db';
const DB_VERSION = 3;

let db: IDBDatabase | null = null;
let dbPromise: Promise<IDBDatabase> | null = null;

function openDatabase(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      const database = request.result;
      // 自愈：另一标签页触发版本升级/删除数据库时，旧连接会卡住升级且后续
      // 操作全部抛 InvalidStateError。这里主动让出并失效缓存，下次调用重开。
      database.onversionchange = () => {
        database.close();
        if (db === database) {
          db = null;
          dbPromise = null;
        }
      };
      database.onclose = () => {
        if (db === database) {
          db = null;
          dbPromise = null;
        }
      };
      db = database;
      resolve(database);
    };

    request.onupgradeneeded = (event) => {
      const database = (event.target as IDBOpenDBRequest).result;

      if (!database.objectStoreNames.contains('books')) {
        const bookStore = database.createObjectStore('books', { keyPath: 'id' });
        bookStore.createIndex('title', 'title', { unique: false });
        bookStore.createIndex('author', 'author', { unique: false });
        bookStore.createIndex('category', 'category', { unique: false });
        bookStore.createIndex('format', 'format', { unique: false });
        bookStore.createIndex('addedAt', 'addedAt', { unique: false });
      }

      if (!database.objectStoreNames.contains('categories')) {
        const catStore = database.createObjectStore('categories', { keyPath: 'id' });
        catStore.createIndex('parentId', 'parentId', { unique: false });
        catStore.createIndex('sortOrder', 'sortOrder', { unique: false });
      }

      if (!database.objectStoreNames.contains('bookmarks')) {
        const bmStore = database.createObjectStore('bookmarks', { keyPath: 'id' });
        bmStore.createIndex('bookId', 'bookId', { unique: false });
      }

      if (!database.objectStoreNames.contains('readingRecords')) {
        const rrStore = database.createObjectStore('readingRecords', { keyPath: 'id' });
        rrStore.createIndex('bookId', 'bookId', { unique: false });
        rrStore.createIndex('startTime', 'startTime', { unique: false });
      }

      if (!database.objectStoreNames.contains('notes')) {
        const noteStore = database.createObjectStore('notes', { keyPath: 'id' });
        noteStore.createIndex('bookId', 'bookId', { unique: false });
      }

      if (!database.objectStoreNames.contains('files')) {
        const fileStore = database.createObjectStore('files', { keyPath: 'id' });
      }

      if (!database.objectStoreNames.contains('covers')) {
        const coverStore = database.createObjectStore('covers', { keyPath: 'id' });
      }

      if (!database.objectStoreNames.contains('pendingWrites')) {
        // 离线写队列：API 不可用时的进度/阅读记录暂存，恢复后重放
        database.createObjectStore('pendingWrites', { keyPath: 'id' });
      }
    };
  });
}

export function initDB(): Promise<IDBDatabase> {
  // 记忆化：并发调用（如 exportData 的 Promise.all）复用同一打开中的连接，
  // 避免多次 indexedDB.open 泄漏多余连接、阻塞后续版本升级。
  if (!dbPromise) {
    dbPromise = openDatabase().catch((err) => {
      dbPromise = null;
      throw err;
    });
  }
  return dbPromise;
}

export async function addBook(book: Book): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['books'], 'readwrite');
    const store = transaction.objectStore('books');
    const request = store.put(book);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function getBooks(): Promise<Book[]> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['books'], 'readonly');
    const store = transaction.objectStore('books');
    const request = store.getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function getBookById(id: string): Promise<Book | undefined> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['books'], 'readonly');
    const store = transaction.objectStore('books');
    const request = store.get(id);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function updateBook(book: Book): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['books'], 'readwrite');
    const store = transaction.objectStore('books');
    const request = store.put(book);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function deleteBook(id: string): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['books'], 'readwrite');
    const store = transaction.objectStore('books');
    const request = store.delete(id);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function addCategory(category: Category): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['categories'], 'readwrite');
    const store = transaction.objectStore('categories');
    const request = store.put(category);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function getCategories(): Promise<Category[]> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['categories'], 'readonly');
    const store = transaction.objectStore('categories');
    const request = store.getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function deleteCategory(id: string): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['categories'], 'readwrite');
    const store = transaction.objectStore('categories');
    const request = store.delete(id);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function addBookmark(bookmark: Bookmark): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['bookmarks'], 'readwrite');
    const store = transaction.objectStore('bookmarks');
    const request = store.put(bookmark);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function getBookmarksByBookId(bookId: string): Promise<Bookmark[]> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['bookmarks'], 'readonly');
    const store = transaction.objectStore('bookmarks');
    const index = store.index('bookId');
    const request = index.getAll(bookId);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function deleteBookmark(id: string): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['bookmarks'], 'readwrite');
    const store = transaction.objectStore('bookmarks');
    const request = store.delete(id);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function addReadingRecord(record: ReadingRecord): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['readingRecords'], 'readwrite');
    const store = transaction.objectStore('readingRecords');
    const request = store.put(record);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function getReadingRecordsByBookId(bookId: string): Promise<ReadingRecord[]> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['readingRecords'], 'readonly');
    const store = transaction.objectStore('readingRecords');
    const index = store.index('bookId');
    const request = index.getAll(bookId);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function getAllReadingRecords(): Promise<ReadingRecord[]> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['readingRecords'], 'readonly');
    const store = transaction.objectStore('readingRecords');
    const request = store.getAll();
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function addNote(note: Note): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['notes'], 'readwrite');
    const store = transaction.objectStore('notes');
    const request = store.put(note);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function getNotesByBookId(bookId: string): Promise<Note[]> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['notes'], 'readonly');
    const store = transaction.objectStore('notes');
    const index = store.index('bookId');
    const request = index.getAll(bookId);
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

export async function updateNote(note: Note): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['notes'], 'readwrite');
    const store = transaction.objectStore('notes');
    const request = store.put({ ...note, updatedAt: Date.now() });
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function deleteNote(id: string): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['notes'], 'readwrite');
    const store = transaction.objectStore('notes');
    const request = store.delete(id);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

async function getAllFromStore<T>(storeName: string): Promise<T[]> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction([storeName], 'readonly');
    const request = transaction.objectStore(storeName).getAll();
    request.onsuccess = () => resolve(request.result as T[]);
    request.onerror = () => reject(request.error);
  });
}

interface CoverRecord {
  id: string;
  data: string;
}

export interface FileRecord {
  id: string;
  data: ArrayBuffer;
}

/** 迁移用：读取 files store 全部原始文件 */
export function getAllFileRecords(): Promise<FileRecord[]> {
  return getAllFromStore<FileRecord>('files');
}

/** 迁移用：读取 covers store 全部封面 */
export function getAllCoverRecords(): Promise<CoverRecord[]> {
  return getAllFromStore<CoverRecord>('covers');
}

// ---------- 离线写队列 ----------

export interface PendingWrite {
  id: string;
  kind: 'progress' | 'record';
  payload: {
    bookId: string;
    progressPercent?: number;
    position?: number | null;
    startTimeMs?: number;
    endTimeMs?: number;
    unitsRead?: number;
  };
  createdAt: number;
}

export async function addPendingWrite(item: PendingWrite): Promise<void> {
  // 注意：不能用 new Promise(async ...) 包裹 await initDB()——若 initDB 拒绝，
  // 异常只会落在 executor 内部，外层 promise 永不 settle（上游永久挂起）。
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['pendingWrites'], 'readwrite');
    const request = transaction.objectStore('pendingWrites').put(item);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export function getPendingWrites(): Promise<PendingWrite[]> {
  return getAllFromStore<PendingWrite>('pendingWrites');
}

export async function deletePendingWrite(id: string): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['pendingWrites'], 'readwrite');
    const request = transaction.objectStore('pendingWrites').delete(id);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function exportData(): Promise<string> {
  const [books, categories, bookmarks, readingRecords, notes, covers] = await Promise.all([
    getBooks(),
    getCategories(),
    getAllFromStore<Bookmark>('bookmarks'),
    getAllReadingRecords(),
    getAllFromStore<Note>('notes'),
    getAllFromStore<CoverRecord>('covers'),
  ]);

  const data = { books, categories, bookmarks, readingRecords, notes, covers, exportTime: Date.now() };
  return JSON.stringify(data, null, 2);
}

const IMPORT_STORES = ['books', 'categories', 'bookmarks', 'readingRecords', 'notes', 'covers'] as const;

export async function importData(jsonString: string): Promise<void> {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(jsonString) as Record<string, unknown>;
  } catch {
    throw new Error('备份文件不是合法 JSON');
  }
  if (typeof data !== 'object' || data === null) {
    throw new Error('备份文件格式不正确');
  }
  const database = await initDB();

  // 注意：采用 upsert 合并语义，不先清空现有数据（书籍文件存于 files store，
  // 备份不含文件，清空元数据会导致文件失联且不可逆）
  const stores = IMPORT_STORES.filter((s) => Array.isArray(data[s]));
  if (stores.length === 0) return;
  const transaction = database.transaction([...stores], 'readwrite');

  // 全部 put 在同一同步任务内发出，事务存活到 oncomplete；
  // 不在事务内 await（部分实现会因微任务间隙提前 commit 而报错）
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
    transaction.onabort = () => reject(transaction.error ?? new Error('导入事务中止'));
    for (const storeName of stores) {
      const store = transaction.objectStore(storeName);
      for (const item of data[storeName] as unknown[]) {
        // 形状校验：必须有 id 主键，防止畸形备份污染持久数据
        if (item && typeof item === 'object' && 'id' in item) {
          store.put(item);
        }
      }
    }
  });
}

export async function storeFileInDB(id: string, arrayBuffer: ArrayBuffer): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['files'], 'readwrite');
    const store = transaction.objectStore('files');
    const request = store.put({ id, data: arrayBuffer });
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function getFileFromDB(id: string): Promise<ArrayBuffer | null> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['files'], 'readonly');
    const store = transaction.objectStore('files');
    const request = store.get(id);
    request.onsuccess = () => {
      const result = request.result;
      resolve(result ? result.data : null);
    };
    request.onerror = () => reject(request.error);
  });
}

export async function deleteFileFromDB(id: string): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['files'], 'readwrite');
    const store = transaction.objectStore('files');
    const request = store.delete(id);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function storeCoverInDB(id: string, coverData: string | ArrayBuffer): Promise<void> {
  const database = await initDB();
  const data = typeof coverData === 'string' ? coverData : arrayBufferToBase64(coverData);
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['covers'], 'readwrite');
    const store = transaction.objectStore('covers');
    const request = store.put({ id, data });
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export async function getCoverFromDB(id: string): Promise<string | null> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['covers'], 'readonly');
    const store = transaction.objectStore('covers');
    const request = store.get(id);
    request.onsuccess = () => {
      const result = request.result;
      resolve(result ? result.data : null);
    };
    request.onerror = () => reject(request.error);
  });
}

export async function deleteCoverFromDB(id: string): Promise<void> {
  const database = await initDB();
  return new Promise((resolve, reject) => {
    const transaction = database.transaction(['covers'], 'readwrite');
    const store = transaction.objectStore('covers');
    const request = store.delete(id);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

function arrayBufferToBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = '';
  for (let i = 0; i < bytes.byteLength; i++) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary);
}
