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

  // 统一使用函数式替换：字符串替换串中的 `$&`/`` $` ``/`$'` 会被特殊解释，
  // CSS 内容（如 content:"$"、压缩产物）极易触发，导致内联结果被静默破坏
  const replaceAll = (source: string, pattern: RegExp, to: string): string =>
    source.replace(pattern, () => to);

  const imageRegex = /(src|href)=["']([^"']+\.(?:jpg|jpeg|png|gif|bmp|webp|svg))["']/gi;
  for (const match of [...processedContent.matchAll(imageRegex)]) {
    const attr = match[1];
    const originalPath = match[2];
    if (!attr || !originalPath) continue;

    if (originalPath.startsWith('http://') || originalPath.startsWith('https://') || originalPath.startsWith('data:')) {
      continue;
    }

    const resourcePath = decodeURIComponent(
      originalPath.startsWith('/') ? originalPath.substring(1) : opfDir + originalPath,
    );

    const resourceFile = zip.file(resourcePath);
    if (resourceFile) {
      try {
        const ext = originalPath.split('.').pop()?.toLowerCase() || 'jpg';
        const mimeType = getImageMimeType(ext);
        const base64Data = await resourceFile.async('base64');
        const dataUrl = `data:${mimeType};base64,${base64Data}`;
        processedContent = processedContent.replace(
          new RegExp(`${attr}=["']${escapeRegExp(originalPath)}["']`, 'g'),
          () => `${attr}="${dataUrl}"`
        );
      } catch (e) {
        console.warn(`Failed to inline resource: ${resourcePath}`, e);
      }
    }
  }

  /** 内联 CSS 内部的 url(...) / 字体等引用（相对路径基于该 CSS 自身目录解析） */
  const inlineCssUrls = async (cssContent: string, cssPath: string): Promise<string> => {
    const cssDir = cssPath.substring(0, cssPath.lastIndexOf('/') + 1);
    let result = cssContent;
    const urlRegex = /url\(\s*(['"]?)([^'")]+?)\1\s*\)/gi;
    for (const match of [...cssContent.matchAll(urlRegex)]) {
      const rawTarget = match[2];
      if (!rawTarget) continue;
      const target = rawTarget.trim();
      if (
        !target ||
        target.startsWith('data:') ||
        target.startsWith('http://') ||
        target.startsWith('https://') ||
        target.startsWith('#')
      ) {
        continue;
      }
      const resourcePath = decodeURIComponent(target.startsWith('/') ? target.substring(1) : cssDir + target);
      const resourceFile = zip.file(resourcePath);
      if (!resourceFile) continue;
      try {
        const ext = resourcePath.split('.').pop()?.toLowerCase() || '';
        const fontMime: Record<string, string> = { ttf: 'font/ttf', otf: 'font/otf', woff: 'font/woff', woff2: 'font/woff2' };
        const mimeType = fontMime[ext] ?? getImageMimeType(ext);
        const base64Data = await resourceFile.async('base64');
        result = replaceAll(result, new RegExp(escapeRegExp(match[0]), 'g'), `url("data:${mimeType};base64,${base64Data}")`);
      } catch (e) {
        console.warn(`Failed to inline css url(): ${resourcePath}`, e);
      }
    }
    return result;
  };

  const cssRegex = /<link[^>]*rel=["']stylesheet["'][^>]*href=["']([^"']+)["'][^>]*>/gi;
  for (const match of [...processedContent.matchAll(cssRegex)]) {
    const originalPath = match[1];
    const fullMatch = match[0];
    if (!originalPath) continue;

    if (originalPath.startsWith('http://') || originalPath.startsWith('https://') || originalPath.startsWith('data:')) {
      continue;
    }

    const resourcePath = decodeURIComponent(
      originalPath.startsWith('/') ? originalPath.substring(1) : opfDir + originalPath,
    );

    const resourceFile = zip.file(resourcePath);
    if (resourceFile) {
      try {
        const rawCss = await resourceFile.async('text');
        const cssContent = await inlineCssUrls(rawCss, resourcePath);
        processedContent = replaceAll(processedContent, new RegExp(escapeRegExp(fullMatch), 'g'), `<style>${cssContent}</style>`);
      } catch (e) {
        console.warn(`Failed to inline CSS: ${resourcePath}`, e);
      }
    }
  }

  return processedContent;
}
