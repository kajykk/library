/**
 * 书架筛选模型：URL ⇄ 类型化对象。
 *
 * 纯函数，便于测试，也让页面、React Query key 与请求参数共用同一份解析结果，
 * 避免"切了筛选但缓存复用"或列表与计数条件漂移。
 */

import type { LibrarySort, QualityFilter } from './api/client';
import type { ShelfFilter } from './dataSource';
import { normalizeLanguageCode } from './language';

export type LibraryView = 'grid' | 'list';

export interface LibraryFilters {
  q: string;
  shelf: ShelfFilter;
  category: string | null;
  author: string | null;
  quality: QualityFilter | null;
  format: string | null;
  language: string | null;
  tag: string | null;
  sort: LibrarySort;
  view: LibraryView;
}

export const DEFAULT_FILTERS: LibraryFilters = {
  q: '',
  shelf: 'all',
  category: null,
  author: null,
  quality: null,
  format: null,
  language: null,
  tag: null,
  sort: 'created_desc',
  view: 'grid',
};

export const SHELF_LABELS: Record<ShelfFilter, string> = {
  all: '全部',
  reading: '在读',
  unread: '未读',
  done: '读完',
  recent: '最近添加',
};

export const SORT_LABELS: Record<LibrarySort, string> = {
  created_desc: '最近添加',
  created_asc: '最早添加',
  title_asc: '书名 A→Z',
  author_asc: '作者 A→Z',
  last_read_desc: '上次阅读',
  progress_desc: '阅读进度',
  file_size_desc: '文件大小',
};

export const QUALITY_LABELS: Record<QualityFilter, string> = {
  issues: '有质量问题',
  empty_content: '正文为空',
  short_content: '正文过短',
  no_cover: '无封面',
  unclassified: '未分类',
  needs_ocr: '需 OCR',
  missing_file: '文件缺失',
  duplicate: '重复文件',
  not_audited: '未审计',
};

const SHELF_VALUES = new Set<string>(Object.keys(SHELF_LABELS));
const SORT_VALUES = new Set<string>(Object.keys(SORT_LABELS));
const QUALITY_VALUES = new Set<string>(Object.keys(QUALITY_LABELS));

function nullableText(raw: string | null): string | null {
  const value = (raw || '').trim();
  return value ? value : null;
}

/** URL 是用户可编辑输入：未知枚举一律回退默认，不抛错。 */
export function parseLibraryFilters(sp: URLSearchParams): LibraryFilters {
  const shelfRaw = sp.get('shelf') || '';
  const shelf = SHELF_VALUES.has(shelfRaw) ? (shelfRaw as ShelfFilter) : DEFAULT_FILTERS.shelf;

  const sortRaw = sp.get('sort') || '';
  const sort = SORT_VALUES.has(sortRaw) ? (sortRaw as LibrarySort) : DEFAULT_FILTERS.sort;

  const qualityRaw = sp.get('quality') || '';
  const quality = QUALITY_VALUES.has(qualityRaw) ? (qualityRaw as QualityFilter) : null;

  const viewRaw = sp.get('view') || '';
  const view: LibraryView = viewRaw === 'list' ? 'list' : 'grid';

  const languageRaw = nullableText(sp.get('language'));
  const formatRaw = nullableText(sp.get('format'));

  return {
    q: (sp.get('q') || '').trim(),
    shelf,
    category: nullableText(sp.get('category')),
    author: nullableText(sp.get('author')),
    quality,
    format: formatRaw ? formatRaw.toLowerCase() : null,
    language: languageRaw ? normalizeLanguageCode(languageRaw) : null,
    tag: nullableText(sp.get('tag')),
    // 最近添加是固定视图：排序由视图决定，URL 里的 sort 不生效
    sort: shelf === 'recent' ? 'created_desc' : sort,
    view,
  };
}

/** 输出顺序固定，默认值不写，`shelf=recent` 时省略 sort。 */
export function serializeLibraryFilters(filters: LibraryFilters): URLSearchParams {
  const sp = new URLSearchParams();
  if (filters.q.trim()) sp.set('q', filters.q.trim());
  if (filters.shelf !== DEFAULT_FILTERS.shelf) sp.set('shelf', filters.shelf);
  if (filters.category) sp.set('category', filters.category);
  if (filters.author) sp.set('author', filters.author);
  if (filters.quality) sp.set('quality', filters.quality);
  if (filters.format) sp.set('format', filters.format);
  if (filters.language) sp.set('language', filters.language);
  if (filters.tag) sp.set('tag', filters.tag);
  if (filters.shelf !== 'recent' && filters.sort !== DEFAULT_FILTERS.sort) {
    sp.set('sort', filters.sort);
  }
  if (filters.view !== DEFAULT_FILTERS.view) sp.set('view', filters.view);
  return sp;
}

export function filtersEqual(a: LibraryFilters, b: LibraryFilters): boolean {
  return (Object.keys(DEFAULT_FILTERS) as Array<keyof LibraryFilters>).every(
    (key) => a[key] === b[key],
  );
}

/** 列表/计数/分面共用的后端筛选参数（分类名由数据源解析成 collection_id）。 */
export function buildApiParams(filters: LibraryFilters): Record<string, string> {
  const params: Record<string, string> = { sort: filters.shelf === 'recent' ? 'created_desc' : filters.sort };
  if (filters.q.trim()) params.q = filters.q.trim();
  if (filters.shelf !== 'all' && filters.shelf !== 'recent') params.status = filters.shelf;
  if (filters.author) params.author = filters.author;
  if (filters.quality) params.quality = filters.quality;
  if (filters.format) params.format = filters.format;
  if (filters.language) params.language = filters.language;
  if (filters.tag) params.tag = filters.tag;
  return params;
}

export interface ActiveFilterChip {
  key: keyof LibraryFilters;
  label: string;
}

/** 工具栏活跃筛选 chips；sort/view 是视图偏好，不算筛选。 */
export function buildActiveChips(filters: LibraryFilters): ActiveFilterChip[] {
  const chips: ActiveFilterChip[] = [];
  if (filters.q.trim()) chips.push({ key: 'q', label: `搜索：${filters.q.trim()}` });
  if (filters.shelf !== 'all') {
    chips.push({ key: 'shelf', label: `书架：${SHELF_LABELS[filters.shelf]}` });
  }
  if (filters.category) chips.push({ key: 'category', label: `分类：${filters.category}` });
  if (filters.author) chips.push({ key: 'author', label: `作者：${filters.author}` });
  if (filters.quality) {
    chips.push({ key: 'quality', label: `质量：${QUALITY_LABELS[filters.quality]}` });
  }
  if (filters.format) chips.push({ key: 'format', label: `格式：${filters.format.toUpperCase()}` });
  if (filters.language) chips.push({ key: 'language', label: `语言：${filters.language}` });
  if (filters.tag) chips.push({ key: 'tag', label: `标签：${filters.tag}` });
  return chips;
}

export function isLibraryFiltered(filters: LibraryFilters): boolean {
  return buildActiveChips(filters).length > 0;
}

/** 清除全部筛选，保留用户的排序与视图偏好。 */
export function clearLibraryFilters(filters: LibraryFilters): LibraryFilters {
  return {
    ...DEFAULT_FILTERS,
    sort: filters.sort,
    view: filters.view,
  };
}
