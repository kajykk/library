import { describe, expect, it } from 'vitest';
import { getImageMimeType } from '../parsers/epub';
import { detectImageMime, resolveZipEntry } from '../parsers/foliate';
import { fuzzyScore } from '../fuzzy';
import { escapeFtsQuery } from '../fts';

// ---------- epub.ts: MIME 映射 ----------

describe('getImageMimeType', () => {
  it('映射常见图片扩展名', () => {
    expect(getImageMimeType('jpg')).toBe('image/jpeg');
    expect(getImageMimeType('jpeg')).toBe('image/jpeg');
    expect(getImageMimeType('png')).toBe('image/png');
    expect(getImageMimeType('svg')).toBe('image/svg+xml');
  });

  it('未知扩展名回退为 image/jpeg', () => {
    expect(getImageMimeType('xyz')).toBe('image/jpeg');
    expect(getImageMimeType('')).toBe('image/jpeg');
  });
});

// ---------- foliate.ts: 字节嗅探 MIME ----------

describe('detectImageMime', () => {
  it('通过魔数识别 JPEG 与 PNG', () => {
    expect(detectImageMime(new Uint8Array([0xff, 0xd8, 0xff, 0xe0]))).toBe('image/jpeg');
    expect(detectImageMime(new Uint8Array([0x89, 0x50, 0x4e, 0x47]))).toBe('image/png');
  });

  it('识别 GIF/BMP/RIFF(WebP)，其余回退 JPEG', () => {
    expect(detectImageMime(new Uint8Array([0x47, 0x49, 0x46, 0x38]))).toBe('image/gif');
    expect(detectImageMime(new Uint8Array([0x42, 0x4d, 0x00, 0x00]))).toBe('image/bmp');
    expect(detectImageMime(new Uint8Array([0x52, 0x49, 0x46, 0x46, 0, 0, 0, 0, 0x57, 0x45, 0x42, 0x50]))).toBe('image/webp');
    expect(detectImageMime(new Uint8Array([0x00, 0x01, 0x02]))).toBe('image/jpeg');
  });
});

// ---------- foliate.ts: zip 内 href 解析 ----------

describe('resolveZipEntry', () => {
  const makeFile = (paths: string[]) => {
    const entries = new Map(paths.map((p) => [p, { async: async () => p }]));
    return (path: string) => entries.get(path) ?? null;
  };

  it('按 baseDir 拼接相对路径', () => {
    const file = makeFile(['OEBPS/text/ch1.xhtml']);
    expect(resolveZipEntry(file, 'OEBPS/text/', 'ch1.xhtml')).not.toBeNull();
  });

  it('剥掉 #fragment 与 ?query，并支持根相对路径与 URL 编码回退', () => {
    const file = makeFile(['OEBPS/content/ch1.xhtml', 'content/ch2.xhtml', '图 片.png', 'docs/a b.xhtml']);
    expect(resolveZipEntry(file, 'OEBPS/content/', 'ch1.xhtml#sec-2')).not.toBeNull();
    expect(resolveZipEntry(file, '', '/content/ch2.xhtml?q=1')).not.toBeNull();
    expect(resolveZipEntry(file, '', '%E5%9B%BE%20%E7%89%87.png')).not.toBeNull();
    expect(resolveZipEntry(file, 'docs/', 'a%20b.xhtml')).not.toBeNull();
  });

  it('找不到资源时返回 null', () => {
    expect(resolveZipEntry(makeFile([]), 'OEBPS/', 'missing.xhtml')).toBeNull();
  });
});

// ---------- fuzzy.ts: 模糊匹配打分 ----------

describe('fuzzyScore', () => {
  it('空 query 直接命中返回满分 1', () => {
    expect(fuzzyScore('', '任意标题')).toBe(1);
  });

  it('字符序列不匹配时返回 null', () => {
    expect(fuzzyScore('xz', '读书笔记')).toBeNull();
    expect(fuzzyScore('ba', 'abc')).toBeNull();
  });

  it('连续命中的得分高于离散命中', () => {
    const streak = fuzzyScore('ab', 'abc') ?? 0;
    const scattered = fuzzyScore('ab', 'axb') ?? 0;
    expect(streak).toBeGreaterThan(scattered);
  });

  it('target 越长得分越低（同匹配下偏好短标题）', () => {
    const short = fuzzyScore('ab', 'ab') ?? 0;
    const long = fuzzyScore('ab', 'ab and a much longer title') ?? 0;
    expect(short).toBeGreaterThan(long);
  });
});

// ---------- fts.ts: FTS5 查询转义（与 server _escape_fts_query 对齐）----------

describe('escapeFtsQuery', () => {
  it('整词包引号，避免被解析为 FTS 语法', () => {
    expect(escapeFtsQuery('hello')).toBe('"hello"');
    expect(escapeFtsQuery('AND OR NOT')).toBe('"AND OR NOT"');
  });

  it('内部双引号双写转义', () => {
    expect(escapeFtsQuery('say "hi"')).toBe('"say ""hi"""');
  });

  it('空字符串仍返回合法的空短语查询', () => {
    expect(escapeFtsQuery('')).toBe('""');
  });
});
