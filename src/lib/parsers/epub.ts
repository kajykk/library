/**
 * EPUB 解析工具：从 BookReader 抽离的纯函数，供前端章节解析使用
 */

export function escapeRegExp(string: string): string {
  return string.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function resolveEpubPath(opfDir: string, href: string): string {
  if (href.startsWith('/') || href.includes('://')) return href.replace(/^\//, '');
  return `${opfDir}${href}`;
}

const MIME_TYPES: Record<string, string> = {
  jpg: 'image/jpeg',
  jpeg: 'image/jpeg',
  png: 'image/png',
  gif: 'image/gif',
  bmp: 'image/bmp',
  webp: 'image/webp',
  svg: 'image/svg+xml',
};

export function getImageMimeType(ext: string): string {
  return MIME_TYPES[ext] || 'image/jpeg';
}

export function extractChapterTitle(html: string, fallback: string, index: number): string {
  const title = html.match(/<title[^>]*>([^<]*)<\/title>/i)?.[1]?.trim();
  if (title) return title;
  const heading =
    html.match(/<h1[^>]*>([^<]*)<\/h1>/i)?.[1]?.trim() ||
    html.match(/<h2[^>]*>([^<]*)<\/h2>/i)?.[1]?.trim();
  if (heading) return heading;
  return fallback ? fallback.split('/').pop() || `章节 ${index}` : `章节 ${index}`;
}

/** 将章节 HTML 内的图片/CSS 资源内联为 data:URL（EPUB 为 zip 包，浏览器无法直接引用） */
export async function processEpubResources(
  htmlContent: string,
  opfDir: string,
  zip: {
    file: (path: string) => { async: (type: 'base64' | 'text') => Promise<string> } | null;
  }
): Promise<string> {
  let processedContent = htmlContent;

  const imageRegex = /(src|href)=["']([^"']+\.(?:jpg|jpeg|png|gif|bmp|webp|svg))["']/gi;
  for (const match of [...processedContent.matchAll(imageRegex)]) {
    const attr = match[1];
    const originalPath = match[2];

    if (originalPath.startsWith('http://') || originalPath.startsWith('https://') || originalPath.startsWith('data:')) {
      continue;
    }

    const resourcePath = originalPath.startsWith('/')
      ? originalPath.substring(1)
      : opfDir + originalPath;

    const resourceFile = zip.file(resourcePath);
    if (resourceFile) {
      try {
        const ext = originalPath.split('.').pop()?.toLowerCase() || 'jpg';
        const mimeType = getImageMimeType(ext);
        const base64Data = await resourceFile.async('base64');
        const dataUrl = `data:${mimeType};base64,${base64Data}`;
        processedContent = processedContent.replace(
          new RegExp(`${attr}=["']${escapeRegExp(originalPath)}["']`, 'g'),
          `${attr}="${dataUrl}"`
        );
      } catch (e) {
        console.warn(`Failed to inline resource: ${resourcePath}`, e);
      }
    }
  }

  const cssRegex = /<link[^>]*rel=["']stylesheet["'][^>]*href=["']([^"']+)["'][^>]*>/gi;
  for (const match of [...processedContent.matchAll(cssRegex)]) {
    const originalPath = match[1];
    const fullMatch = match[0];

    if (originalPath.startsWith('http://') || originalPath.startsWith('https://') || originalPath.startsWith('data:')) {
      continue;
    }

    const resourcePath = originalPath.startsWith('/')
      ? originalPath.substring(1)
      : opfDir + originalPath;

    const resourceFile = zip.file(resourcePath);
    if (resourceFile) {
      try {
        const cssContent = await resourceFile.async('text');
        processedContent = processedContent.replace(fullMatch, `<style>${cssContent}</style>`);
      } catch (e) {
        console.warn(`Failed to inline CSS: ${resourcePath}`, e);
      }
    }
  }

  return processedContent;
}
