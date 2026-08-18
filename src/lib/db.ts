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

export async function initDB(): Promise<IDBDatabase> {
  if (db) return db;

  return new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, DB_VERSION);

    request.onerror = () => reject(request.error);
    request.onsuccess = () => {
      db = request.result;
      resolve(db);
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

export function addPendingWrite(item: PendingWrite): Promise<void> {
  return new Promise(async (resolve, reject) => {
    const database = await initDB();
    const transaction = database.transaction(['pendingWrites'], 'readwrite');
    const request = transaction.objectStore('pendingWrites').put(item);
    request.onsuccess = () => resolve();
    request.onerror = () => reject(request.error);
  });
}

export function getPendingWrites(): Promise<PendingWrite[]> {
  return getAllFromStore<PendingWrite>('pendingWrites');
}

export function deletePendingWrite(id: string): Promise<void> {
  return new Promise(async (resolve, reject) => {
    const database = await initDB();
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

export async function importData(jsonString: string): Promise<void> {
  const data = JSON.parse(jsonString);
  const database = await initDB();

  // 注意：采用 upsert 合并语义，不先清空现有数据（书籍文件存于 files store，
  // 备份不含文件，清空元数据会导致文件失联且不可逆）
  const stores = ['books', 'categories', 'bookmarks', 'readingRecords', 'notes', 'covers'];
  const transaction = database.transaction(stores, 'readwrite');

  for (const storeName of stores) {
    if (!data[storeName] || !Array.isArray(data[storeName])) continue;

    const store = transaction.objectStore(storeName);
    for (const item of data[storeName]) {
      await new Promise<void>((resolve, reject) => {
        const putRequest = store.put(item);
        putRequest.onsuccess = () => resolve();
        putRequest.onerror = () => reject(putRequest.error);
      });
    }
  }
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
