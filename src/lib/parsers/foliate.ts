import { escapeRegExp, extractChapterTitle, getImageMimeType } from './epub';

export interface ParsedChapter {
  href: string;
  title: string;
  content: string;
}

type ZipEntry = {
  async: (type: 'base64' | 'text' | 'arraybuffer' | 'uint8array') => Promise<unknown>;
};

type ZipFileFn = (path: string) => ZipEntry | null;

function blobToDataUrl(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(reader.result as string);
    reader.onerror = () => reject(reader.error);
    reader.readAsDataURL(blob);
  });
}

async function fetchBlobUrlToDataUrl(url: string): Promise<string | null> {
  try {
    const res = await fetch(url);
    if (!res.ok) return null;
    return await blobToDataUrl(await res.blob());
  } catch {
    return null;
  }
}

export function detectImageMime(bytes: Uint8Array): string {
  if (bytes.length > 1 && bytes[0] === 0xff && bytes[1] === 0xd8) return 'image/jpeg';
  if (bytes.length > 3 && bytes[0] === 0x89 && bytes[1] === 0x50) return 'image/png';
  if (bytes.length > 3 && bytes[0] === 0x47 && bytes[1] === 0x49) return 'image/gif';
  if (bytes.length > 1 && bytes[0] === 0x42 && bytes[1] === 0x4d) return 'image/bmp';
  if (bytes.length > 11 && bytes[0] === 0x52 && bytes[1] === 0x49 && bytes[2] === 0x46 && bytes[3] === 0x46) return 'image/webp';
  return 'image/jpeg';
}

export function resolveZipEntry(file: ZipFileFn, baseDir: string, href: string): ZipEntry | null {
  const clean = href.split('#')[0].split('?')[0];
  const candidates = [clean];
  if (clean.startsWith('/')) candidates.push(clean.slice(1));
  else candidates.push(baseDir + clean);
  for (const path of candidates) {
    const entry = file(path);
    if (entry) return entry;
    try {
      const entryDecoded = file(decodeURIComponent(path));
      if (entryDecoded) return entryDecoded;
    } catch {
      continue;
    }
  }
  return null;
}

async function inlineChapterResources(html: string, baseDir: string, file: ZipFileFn): Promise<string> {
  let processed = html;

  const imageRegex = /(src|href)=["']([^"']+\.(?:jpg|jpeg|png|gif|bmp|webp|svg))["']/gi;
  for (const match of [...processed.matchAll(imageRegex)]) {
    const attr = match[1];
    const originalPath = match[2];
    if (/^(https?:|data:|blob:)/i.test(originalPath)) continue;

    const resourceFile = resolveZipEntry(file, baseDir, originalPath);
    if (resourceFile) {
      try {
        const ext = originalPath.split('.').pop()?.toLowerCase() || 'jpg';
        const mimeType = getImageMimeType(ext);
        const base64Data = (await resourceFile.async('base64')) as string;
        const dataUrl = `data:${mimeType};base64,${base64Data}`;
        processed = processed.replace(
          new RegExp(`${attr}=["']${escapeRegExp(originalPath)}["']`, 'g'),
          `${attr}="${dataUrl}"`
        );
      } catch (e) {
        console.warn(`Failed to inline resource: ${originalPath}`, e);
      }
    }
  }

  const cssRegex = /<link[^>]*rel=["']stylesheet["'][^>]*href=["']([^"']+)["'][^>]*>/gi;
  for (const match of [...processed.matchAll(cssRegex)]) {
    const originalPath = match[1];
    const fullMatch = match[0];
    if (/^(https?:|data:|blob:)/i.test(originalPath)) continue;

    const resourceFile = resolveZipEntry(file, baseDir, originalPath);
    if (resourceFile) {
      try {
        const cssContent = (await resourceFile.async('text')) as string;
        processed = processed.replace(fullMatch, `<style>${cssContent}</style>`);
      } catch (e) {
        console.warn(`Failed to inline CSS: ${originalPath}`, e);
      }
    }
  }

  return processed;
}

export async function parseEpubChapters(arrayBuffer: ArrayBuffer): Promise<ParsedChapter[]> {
  const JSZip = (await import('jszip')).default;
  const zip = await JSZip.loadAsync(arrayBuffer);
  const { EPUB } = await import('./foliate-js/epub.js');

  const file: ZipFileFn = (path) => zip.file(path) || null;
  const loadText = async (path: string) => {
    const entry = file(path);
    return entry ? ((await entry.async('text')) as string) : null;
  };
  const loadBlob = async (path: string) => {
    const entry = file(path);
    if (!entry) throw new Error(`EPUB 资源不存在: ${path}`);
    return new Blob([(await entry.async('arraybuffer')) as ArrayBuffer]);
  };
  const getSize = () => 0;

  const book = new EPUB({ loadText, loadBlob, getSize, sha1: undefined });
  await book.init();

  const tocTitles = new Map<string, string>();
  const walk = (items: unknown = []) => {
    for (const item of items as Array<{ href?: string; label?: string; subitems?: unknown }>) {
      if (item.href) {
        const [path] = item.href.split('#');
        if (path && !tocTitles.has(path)) tocTitles.set(path, item.label || '');
      }
      if (item.subitems) walk(item.subitems);
    }
  };
  walk((book as { toc?: unknown }).toc);

  const chapters: ParsedChapter[] = [];
  let index = 0;
  for (const section of (book as { sections: Array<{ id: string }> }).sections) {
    const raw = await loadText(section.id);
    if (!raw) continue;
    const baseDir = section.id.slice(0, section.id.lastIndexOf('/') + 1);
    const content = await inlineChapterResources(raw, baseDir, file);
    const title = tocTitles.get(section.id) || extractChapterTitle(raw, section.id, index + 1);
    chapters.push({ href: section.id, title, content });
    index += 1;
  }

  (book as { destroy?: () => void }).destroy?.();
  return chapters;
}

type MobiBook = {
  sections: Array<{ createDocument: () => Promise<Document> }>;
  toc?: unknown;
  splitTOCHref?: (href: string) => unknown;
  loadRecindex?: (recindex: string) => Promise<string>;
  loadResource?: (uri: string) => Promise<string>;
  metadata?: { title?: string; author?: string | string[]; identifier?: string };
};

async function inlineMobiResources(doc: Document, book: MobiBook): Promise<void> {
  for (const img of Array.from(doc.querySelectorAll('img[recindex]'))) {
    const recindex = img.getAttribute('recindex');
    if (!recindex || typeof book.loadRecindex !== 'function') continue;
    try {
      const dataUrl = await fetchBlobUrlToDataUrl(await book.loadRecindex(recindex));
      if (dataUrl) img.setAttribute('src', dataUrl);
    } catch (e) {
      console.warn(`MOBI 图片内联失败 (recindex=${recindex})`, e);
    }
  }
  for (const media of Array.from(doc.querySelectorAll('[mediarecindex]'))) {
    const recindex = media.getAttribute('mediarecindex') || media.getAttribute('recindex');
    if (!recindex || typeof book.loadRecindex !== 'function') continue;
    try {
      const dataUrl = await fetchBlobUrlToDataUrl(await book.loadRecindex(recindex));
      if (dataUrl) media.setAttribute('src', dataUrl);
    } catch (e) {
      console.warn(`MOBI 媒体内联失败 (recindex=${recindex})`, e);
    }
  }
  if (typeof book.loadResource === 'function') {
    for (const img of Array.from(doc.querySelectorAll('img[src]'))) {
      const src = img.getAttribute('src') || '';
      if (!/^kindle:/i.test(src)) continue;
      try {
        const dataUrl = await fetchBlobUrlToDataUrl(await book.loadResource(src));
        if (dataUrl) img.setAttribute('src', dataUrl);
      } catch (e) {
        console.warn(`AZW3 图片内联失败: ${src}`, e);
      }
    }
  }
}

export async function parseMobiChapters(arrayBuffer: ArrayBuffer): Promise<ParsedChapter[]> {
  const { MOBI } = await import('./foliate-js/mobi.js');
  const file = new Blob([arrayBuffer]);
  const mobi = new MOBI({ unzlib: undefined });
  const book = (await mobi.open(file)) as MobiBook;

  const titleByIndex = new Map<number, string>();
  const walk = (items: unknown = []) => {
    for (const item of items as Array<{ href?: string; label?: string; subitems?: unknown }>) {
      if (item.href && typeof book.splitTOCHref === 'function') {
        const resolved = book.splitTOCHref(item.href) as [number] | undefined;
        const sectionIndex = resolved?.[0];
        if (typeof sectionIndex === 'number' && sectionIndex >= 0 && !titleByIndex.has(sectionIndex)) {
          titleByIndex.set(sectionIndex, item.label || '');
        }
      }
      if (item.subitems) walk(item.subitems);
    }
  };
  walk(book.toc);

  const chapters: ParsedChapter[] = [];
  for (let i = 0; i < book.sections.length; i += 1) {
    try {
      const doc = await book.sections[i].createDocument();
      await inlineMobiResources(doc, book);
      const root = doc.body ?? doc.documentElement;
      const html = root ? root.innerHTML : '';
      chapters.push({ href: String(i), title: titleByIndex.get(i) || `章节 ${i + 1}`, content: html });
    } catch (e) {
      console.warn(`MOBI/AZW3 章节 ${i} 解析失败`, e);
    }
  }

  (book as { destroy?: () => void }).destroy?.();
  return chapters;
}

export async function parseMobiMetadata(
  file: Blob
): Promise<{ title: string; author: string; publisher: string; description: string; language: string; isbn: string; cover: string | null } | null> {
  try {
    const { MOBI } = await import('./foliate-js/mobi.js');
    const mobi = new MOBI({ unzlib: undefined });
    const book = (await mobi.open(file)) as MobiBook;
    const md = book.metadata || {};
    const author = Array.isArray(md.author) ? md.author[0] : md.author;
    let cover: string | null = null;
    const getCover = (book as { getCover?: () => Promise<Blob | null> }).getCover;
    if (typeof getCover === 'function') {
      const coverBlob = await getCover();
      if (coverBlob) cover = await blobToDataUrl(coverBlob);
    }
    (book as { destroy?: () => void }).destroy?.();
    return {
      title: md.title || '',
      author: author || '',
      publisher: ((md as { publisher?: string }).publisher) || '',
      description: ((md as { description?: string }).description) || '',
      language: ((md as { language?: string }).language) || 'zh',
      isbn: md.identifier || '',
      cover,
    };
  } catch (e) {
    console.warn('MOBI/AZW3 元数据提取失败:', e);
    return null;
  }
}
