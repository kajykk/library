import {
  storeFileInDB,
  getFileFromDB,
  deleteFileFromDB,
  storeCoverInDB,
  getCoverFromDB,
  deleteCoverFromDB
} from './db';
import { parseMobiMetadata } from './parsers/foliate';

export async function storeFile(id: string, file: File): Promise<void> {
  const arrayBuffer = await file.arrayBuffer();
  await storeFileInDB(id, arrayBuffer);
}

export async function storeCover(id: string, coverData: string | ArrayBuffer): Promise<void> {
  await storeCoverInDB(id, coverData);
}

export async function deleteFile(id: string): Promise<void> {
  await deleteFileFromDB(id);
}

export async function deleteCover(id: string): Promise<void> {
  await deleteCoverFromDB(id);
}

export async function getFileArrayBuffer(id: string): Promise<ArrayBuffer | null> {
  return await getFileFromDB(id);
}

export async function getCoverUrl(id: string): Promise<string | null> {
  const data = await getCoverFromDB(id);
  if (!data) return null;
  if (data.startsWith('data:')) return data;
  return `data:image/jpeg;base64,${data}`;
}

export async function extractMetadataFromFile(file: File): Promise<{
  title: string;
  author: string;
  publisher: string;
  description: string;
  language: string;
  isbn: string;
  cover: string | null;
}> {
  const format = file.name.split('.').pop()?.toLowerCase() || '';
  
  const defaultMeta = {
    title: file.name.replace(/\.[^/.]+$/, ''),
    author: '未知作者',
    publisher: '',
    description: '',
    language: 'zh',
    isbn: '',
    cover: null as string | null,
  };

  if (format === 'mobi') {
    try {
      const { initMobiFile } = await import('@lingo-reader/mobi-parser');
      const mobi = await initMobiFile(file);
      const metadata = mobi.getMetadata();
      const coverImage = mobi.getCoverImage();

      defaultMeta.title = metadata.title || defaultMeta.title;
      defaultMeta.author = metadata.author?.[0] || defaultMeta.author;
      defaultMeta.publisher = metadata.publisher || '';
      defaultMeta.description = metadata.description || '';
      defaultMeta.language = metadata.language || 'zh';
      defaultMeta.isbn = metadata.identifier || '';
      defaultMeta.cover = coverImage || null;
      mobi.destroy();
    } catch (error) {
      console.error('MOBI metadata extraction failed:', error);
    }
  } else if (format === 'azw3' || format === 'azw') {
    const parsed = await parseMobiMetadata(file);
    if (parsed) {
      defaultMeta.title = parsed.title || defaultMeta.title;
      defaultMeta.author = parsed.author || defaultMeta.author;
      defaultMeta.publisher = parsed.publisher || '';
      defaultMeta.description = parsed.description || '';
      defaultMeta.language = parsed.language || 'zh';
      defaultMeta.isbn = parsed.isbn || '';
      defaultMeta.cover = parsed.cover || null;
    }
  } else if (format === 'epub') {
    try {
      const JSZip = (await import('jszip')).default;
      const zip = await JSZip.loadAsync(file);
      
      const containerXml = await zip.file('META-INF/container.xml')?.async('text');
      if (containerXml) {
        const opfPathMatch = containerXml.match(/full-path="([^"]+)"/);
        if (opfPathMatch) {
          const opfPath = opfPathMatch[1];
          const opfContent = await zip.file(opfPath)?.async('text');
          if (opfContent) {
            const parser = new DOMParser();
            const opfDoc = parser.parseFromString(opfContent, 'application/xml');
            
            const titleEl = opfDoc.querySelector('dc\\:title, title');
            const authorEl = opfDoc.querySelector('dc\\:creator, creator');
            const publisherEl = opfDoc.querySelector('dc\\:publisher, publisher');
            const descEl = opfDoc.querySelector('dc\\:description, description');
            const langEl = opfDoc.querySelector('dc\\:language, language');
            const isbnEl = opfDoc.querySelector('dc\\:identifier[opf\\:scheme="ISBN"], identifier[scheme="ISBN"]');
            
            if (titleEl) defaultMeta.title = titleEl.textContent || defaultMeta.title;
            if (authorEl) defaultMeta.author = authorEl.textContent || defaultMeta.author;
            if (publisherEl) defaultMeta.publisher = publisherEl.textContent || '';
            if (descEl) defaultMeta.description = descEl.textContent || '';
            if (langEl) defaultMeta.language = langEl.textContent || 'zh';
            if (isbnEl) defaultMeta.isbn = isbnEl.textContent || '';
            
            const manifest = opfDoc.querySelector('manifest');
            if (manifest) {
              const coverItem = manifest.querySelector('item[id="cover"]') || 
                               manifest.querySelector('item[properties="cover-image"]') ||
                               manifest.querySelector('item[id*="cover"]');
              if (coverItem) {
                const coverHref = coverItem.getAttribute('href');
                if (coverHref) {
                  const opfDir = opfPath.substring(0, opfPath.lastIndexOf('/') + 1);
                  const coverPath = opfDir + coverHref;
                  const coverFile = zip.file(coverPath);
                  if (coverFile) {
                    const coverData = await coverFile.async('base64');
                    const ext = coverHref.split('.').pop()?.toLowerCase() || 'jpg';
                    defaultMeta.cover = `data:image/${ext === 'png' ? 'png' : 'jpeg'};base64,${coverData}`;
                  }
                }
              }
            }
          }
        }
      }
    } catch (e) {
      console.error('EPUB metadata extraction failed:', e);
    }
  }

  return defaultMeta;
}
