'use client';

import { useState, useEffect, useMemo, useRef, Suspense } from 'react';
import { Book, Category, exportData, importData } from '@/lib/db';
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  loadLibraryFacets,
  loadLibraryMeta,
  loadLibraryPage,
  importBookFile,
  deleteBookCompletely,
  addCategoryByName,
  deleteCategoryAndReset,
} from '@/lib/dataSource';
import { getQualitySummary } from '@/lib/api/client';
import type { FacetItem, LibrarySort } from '@/lib/api/client';
import { useDataSourceStatus } from '@/lib/useDataSourceStatus';
import { formatFileSize, formatDate } from '@/lib/utils';
import {
  Search,
  Upload,
  FolderPlus,
  Trash2,
  Edit3,
  Library,
  Grid3X3,
  List,
  Download,
  RotateCcw,
  BarChart3,
  Eye
} from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import BookCover from '@/components/BookCover';
import BookEditModal from '@/components/BookEditModal';
import StatisticsPanel from '@/components/StatisticsPanel';
import OnboardingBanner from '@/components/OnboardingBanner';
import StatePanel from '@/components/ui/StatePanel';
import FilterChip from '@/components/ui/FilterChip';
import { useDialog, useToast } from '@/components/ui';
import { useLibraryFilters } from '@/lib/useLibraryFilters';
import {
  DEFAULT_FILTERS,
  SHELF_LABELS,
  SORT_LABELS,
  buildActiveChips,
  buildApiParams,
  type LibraryFilters,
} from '@/lib/libraryFilters';
import { languageLabel } from '@/lib/language';

export default function LibraryPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-gray-50 p-16 text-center text-gray-400">加载中...</div>}>
      <LibraryPageInner />
    </Suspense>
  );
}

const LIBRARY_QUERY_KEY = ['documents', 'library'] as const;

// 与 batch_import.py 一致的可导入格式
const SUPPORTED_FORMATS = ['epub', 'pdf', 'mobi', 'azw3', 'azw', 'txt'];

// 智能书架（规则视图）：在读 / 未读 / 读完 / 最近添加
const RECENT_SHELF_SIZE = 20;

// 书架每次向后端取 60 本（而不是先拉整库再前端切片），滚动到底再取下一页
const PAGE_STEP = 60;
const LIBRARY_META_QUERY_KEY = [...LIBRARY_QUERY_KEY, 'meta'] as const;

function limitFacetItems(items: FacetItem[], active: string | null, limit: number): FacetItem[] {
  const head = items.slice(0, limit);
  if (!active || head.some((item) => item.value === active)) return head;
  const selected = items.find((item) => item.value === active);
  return selected ? [...head, selected] : head;
}

function LibraryPageInner() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const dialog = useDialog();
  const toast = useToast();
  const { status: dsStatus } = useDataSourceStatus();
  const dataSourceMode = dsStatus && dsStatus.mode !== 'api' ? 'local' : 'api';
  const {
    filters,
    searchInput,
    setSearchInput,
    setFilter,
    patchFilters,
    clearFilters,
    isFiltered,
    droppedFilters,
  } = useLibraryFilters(dataSourceMode);
  const [editingBook, setEditingBook] = useState<Book | null>(null);
  const [showStats, setShowStats] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showAddCategory, setShowAddCategory] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState('');
  const [importing, setImporting] = useState(false);
  const [onboardingDismissed, setOnboardingDismissed] = useState(true);
  const [importReport, setImportReport] = useState<{
    total: number;
    results: Array<{
      name: string;
      title?: string;
      format?: string;
      status: 'success' | 'failed';
      reason?: string;
    }>;
    startedAt: number;
    finishedAt: number | null;
  } | null>(null);

  // 侧栏（分类/智能书架计数）走一次性聚合：不随分页变化
  const metaQuery = useQuery({
    queryKey: LIBRARY_META_QUERY_KEY,
    queryFn: loadLibraryMeta,
  });
  const categories = metaQuery.data?.categories ?? [];
  const dataMode = metaQuery.data?.mode ?? 'local';
  const migrationHint = metaQuery.data?.migrationHint ?? 0;
  const isApiMode = dataMode === 'api';

  // 质量摘要：仅 API 模式加载，用于 chips 计数
  const qualityQuery = useQuery({
    queryKey: ['quality', 'summary'],
    queryFn: getQualitySummary,
    enabled: isApiMode,
    staleTime: 60_000,
  });

  // 筛选变化滚回顶部；排序/视图变化保持当前位置
  const scrollKey = JSON.stringify([
    filters.q,
    filters.shelf,
    filters.category,
    filters.author,
    filters.quality,
    filters.format,
    filters.language,
    filters.tag,
  ]);
  const lastScrollKey = useRef(scrollKey);
  useEffect(() => {
    if (lastScrollKey.current === scrollKey) return;
    lastScrollKey.current = scrollKey;
    window.scrollTo({ top: 0, behavior: 'auto' });
  }, [scrollKey]);

  // 首次引导条件：非 API 模式 + 书架为空 + 无迁移提示 + 未点过「不再提示」
  const showOnboarding =
    !isApiMode &&
    metaQuery.data?.total === 0 &&
    migrationHint === 0 &&
    !onboardingDismissed;

  useEffect(() => {
    setOnboardingDismissed(localStorage.getItem('kb_onboarding_dismissed') === '1');
  }, []);

  const handleDismissOnboarding = () => {
    localStorage.setItem('kb_onboarding_dismissed', '1');
    setOnboardingDismissed(true);
  };

  // 书目列表按需分页：筛选条件全部下推到后端，翻页用 offset，书架不再受单页上限截断
  const pageSize = filters.shelf === 'recent' ? RECENT_SHELF_SIZE : PAGE_STEP;
  const filterParams = buildApiParams(filters);
  const querySignature = JSON.stringify([
    filterParams,
    filters.category,
    filters.shelf,
    pageSize,
    dataMode,
  ]);
  const booksQuery = useInfiniteQuery({
    queryKey: [...LIBRARY_QUERY_KEY, 'page', querySignature],
    queryFn: ({ pageParam }) =>
      loadLibraryPage({
        offset: pageParam,
        limit: pageSize,
        search: filters.q,
        category: filters.category,
        author: filters.author,
        shelf: filters.shelf,
        quality: filters.quality ?? undefined,
        sort: filters.sort,
        format: filters.format,
        language: filters.language,
        tag: filters.tag,
      }),
    initialPageParam: 0,
    getNextPageParam: (lastPage, allPages) =>
      lastPage.hasMore ? allPages.length * pageSize : undefined,
  });

  // 分面：每维由后端排除自身条件，因此请求要带上全部当前筛选
  const facetKey = JSON.stringify([
    filters.q,
    filters.shelf,
    filters.category,
    filters.author,
    filters.quality,
    filters.format,
    filters.language,
    filters.tag,
  ]);
  const facetsQuery = useQuery({
    queryKey: ['documents', 'facets', facetKey],
    queryFn: () =>
      loadLibraryFacets({
        search: filters.q,
        category: filters.category,
        author: filters.author,
        shelf: filters.shelf,
        quality: filters.quality ?? undefined,
        format: filters.format,
        language: filters.language,
        tag: filters.tag,
      }),
    enabled: isApiMode,
    staleTime: 30_000,
  });

  // offset 分页在数据变动时可能返回重复项，按 id 去重保证 key 唯一
  const books = useMemo(() => {
    const seen = new Set<string>();
    const merged: Book[] = [];
    for (const page of booksQuery.data?.pages ?? []) {
      for (const book of page.books) {
        if (seen.has(book.id)) continue;
        seen.add(book.id);
        merged.push(book);
      }
    }
    return merged;
  }, [booksQuery.data]);

  const totalBooks = booksQuery.data?.pages.at(-1)?.total ?? 0;
  const hasMore = booksQuery.hasNextPage;
  const isLoading = metaQuery.isLoading || booksQuery.isLoading;

  const shelfCounts = useMemo(() => {
    const base = metaQuery.data;
    const total = base?.total ?? 0;
    return {
      all: total,
      reading: base?.shelfCounts.reading ?? 0,
      unread: base?.shelfCounts.unread ?? 0,
      done: base?.shelfCounts.done ?? 0,
      recent: Math.min(RECENT_SHELF_SIZE, total),
    } as Record<keyof typeof SHELF_LABELS, number>;
  }, [metaQuery.data]);
  const categoryCounts = metaQuery.data?.categoryCounts;
  const activeChips = buildActiveChips(filters);
  const hasNonShelfFilters = Boolean(
    filters.q ||
      filters.category ||
      filters.author ||
      filters.quality ||
      filters.format ||
      filters.language ||
      filters.tag,
  );

  const removeFilter = (key: keyof LibraryFilters) => {
    patchFilters({ [key]: DEFAULT_FILTERS[key] } as Partial<LibraryFilters>);
  };

  const loadMoreBooks = () => {
    if (!booksQuery.isFetchingNextPage) void booksQuery.fetchNextPage();
  };

  const refetchLibrary = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: LIBRARY_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: ['documents', 'facets'] }),
    ]);
  };

  const runImport = async (files: File[], categoryOf: (file: File) => string) => {
    setImporting(true);
    setImportReport(null);

    const report = {
      total: files.length,
      results: [] as Array<{
        name: string;
        title?: string;
        format?: string;
        status: 'success' | 'failed';
        reason?: string;
      }>,
      startedAt: Date.now(),
      finishedAt: null as number | null,
    };

    try {
      for (const file of files) {
        const format = file.name.split('.').pop()?.toLowerCase() || '';
        if (!SUPPORTED_FORMATS.includes(format)) {
          report.results.push({ name: file.name, status: 'failed', reason: '不支持的文件格式' });
          continue;
        }

        try {
          const book = await importBookFile(file, categoryOf(file));
          report.results.push({ name: file.name, title: book.title, format: book.format, status: 'success' });
        } catch (error) {
          report.results.push({
            name: file.name,
            status: 'failed',
            reason: (error as Error).message || '导入失败',
          });
        }
      }

      report.finishedAt = Date.now();
      setImportReport(report);
      await refetchLibrary();
    } finally {
      setImporting(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    try {
      await runImport(Array.from(files), () => filters.category || '未分类');
    } finally {
      e.target.value = '';
    }
  };

  const handleFolderUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    try {
      await runImport(
        Array.from(files),
        // 父目录名作为分类（与 batch_import.py 一致）；无目录结构时用当前选中分类
        (file) => {
          const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || '';
          const parts = rel.split('/');
          const parentDir = parts.length >= 2 ? (parts[parts.length - 2] ?? '') : '';
          return parentDir || filters.category || '未分类';
        },
      );
    } finally {
      e.target.value = '';
    }
  };

  const handleDeleteBook = async (book: Book) => {
    const ok = await dialog.confirm({
      title: '删除书籍',
      message: `确定要删除《${book.title}》吗？`,
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;

    try {
      await deleteBookCompletely(book);
      await refetchLibrary();
      setActionError(null);
      toast.success('已删除《' + book.title + '》');
    } catch (error) {
      setActionError((error as Error).message || '删除书籍失败');
    }
  };

  const handleAddCategory = async () => {
    const name = newCategoryName.trim();
    if (!name) return;
    if (categories.some((c) => c.name === name)) {
      toast.error(`分类"${name}"已存在`);
      return;
    }

    try {
      await addCategoryByName(name);
      setNewCategoryName('');
      setShowAddCategory(false);
      await refetchLibrary();
      toast.success(`分类"${name}"已添加`);
    } catch (error) {
      toast.error((error as Error).message);
    }
  };

  const handleDeleteCategory = async (category: Category) => {
    const ok = await dialog.confirm({
      title: '删除分类',
      message: `确定要删除分类"${category.name}"吗？该分类下的书籍将变为未分类。`,
      confirmText: '删除',
      danger: true,
    });
    if (!ok) return;
    try {
      await deleteCategoryAndReset(category);
    } catch (error) {
      setActionError((error as Error).message || '删除分类失败');
      return;
    }
    if (filters.category === category.name) {
      setFilter('category', null);
    }
    await refetchLibrary();
  };

  const handleExport = async () => {
    const ok = await dialog.confirm({
      title: '备份数据',
      message:
        '备份包含书目、分类、书签、笔记、阅读记录和封面，但不包含书籍文件本身（文件保留在浏览器本地存储中）。继续导出？',
      confirmText: '导出备份',
    });
    if (!ok) return;

    try {
      const data = await exportData();
      const blob = new Blob([data], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `ebook_backup_${Date.now()}.json`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success('备份已下载');
    } catch (error) {
      // 此前无 catch：导出失败成为未处理 rejection，用户零反馈
      toast.error('备份导出失败：' + (error as Error).message);
    }
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onerror = () => toast.error('读取备份文件失败');
    reader.onload = async (event) => {
      try {
        const data = event.target?.result as string;
        await importData(data);
        await refetchLibrary();
        toast.success('数据恢复成功！已与现有数据合并（不会删除现有条目）。');
      } catch (error) {
        toast.error('数据恢复失败：' + (error as Error).message);
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const openReader = (book: Book) => {
    router.push(`/reader/${book.id}`);
  };

  return (
    <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <header className="bg-white shadow-sm border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
          <div className="flex items-center justify-between h-16">
            <div className="flex items-center gap-3">
              <Library className="w-8 h-8 text-primary-600" />
              <h1 className="text-xl font-bold text-gray-900">个人电子书管理系统</h1>
            </div>
            
            <div className="flex items-center gap-4">
              <div className="relative">
                <Search className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
                <input
                  type="text"
                  placeholder="搜索书名、作者、出版社..."
                  value={searchInput}
                  onChange={(e) => setSearchInput(e.target.value)}
                  className="pl-10 pr-4 py-2 w-80 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent outline-none"
                />
              </div>
              
              <button
                onClick={() => setShowStats(!showStats)}
                className="flex items-center gap-2 px-4 py-2 text-gray-700 hover:bg-gray-100 rounded-lg transition-colors"
              >
                <BarChart3 className="w-5 h-5" />
                <span className="hidden sm:inline">统计</span>
              </button>
              
              <span
                className={`hidden sm:inline-block px-2 py-1 rounded text-xs font-medium ${
                  dataMode === 'api' ? 'bg-emerald-50 text-emerald-700' : 'bg-gray-100 text-gray-500'
                }`}
                title={dataMode === 'api' ? '数据来自后端服务' : '后端不可用，使用浏览器本地数据'}
              >
                {dataMode === 'api' ? '云端' : '本地'}
              </span>

              <label className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 cursor-pointer transition-colors">
                <Upload className="w-5 h-5" />
                <span className="hidden sm:inline">导入书籍</span>
                <input
                  type="file"
                  data-import-trigger
                  accept=".epub,.pdf,.mobi,.azw3,.azw,.txt"
                  multiple
                  onChange={handleFileUpload}
                  className="hidden"
                />
              </label>

              <label
                className="flex items-center gap-2 px-4 py-2 bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200 cursor-pointer transition-colors"
                title="选择一个文件夹，文件夹名作为分类批量导入"
              >
                <FolderPlus className="w-5 h-5" />
                <span className="hidden sm:inline">批量导入文件夹</span>
                <input
                  type="file"
                  {...({ webkitdirectory: '', directory: '' } as React.InputHTMLAttributes<HTMLInputElement>)}
                  multiple
                  onChange={handleFolderUpload}
                  className="hidden"
                />
              </label>
            </div>
          </div>
        </div>
      </header>

      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        {importReport && (
          <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex items-start justify-between gap-4">
              <div>
                <h2 className="text-sm font-semibold text-slate-900">导入结果</h2>
                <p className="mt-1 text-xs text-slate-500">
                  共处理 {importReport.total} 个文件，成功 {importReport.results.filter(r => r.status === 'success').length} 个，失败 {importReport.results.filter(r => r.status === 'failed').length} 个
                </p>
                {importReport.finishedAt && (
                  <p className="mt-1 text-xs text-slate-400">
                    耗时 {Math.max(1, Math.round((importReport.finishedAt - importReport.startedAt) / 1000))} 秒
                  </p>
                )}
              </div>
              <button
                onClick={() => setImportReport(null)}
                className="rounded-lg px-3 py-1 text-xs text-slate-600 transition-colors hover:bg-slate-100"
              >
                关闭
              </button>
            </div>
            <div className="mt-4 grid gap-3">
              {importReport.results.map((item, index) => {
                const label = item.status === 'success'
                  ? `${item.title} (${(item.format || '').toUpperCase()})`
                  : item.reason
                    ? `${item.name}：${item.reason}`
                    : item.name;
                return (
                  <div
                    key={`${item.name}-${index}`}
                    className={`flex items-center justify-between rounded-lg border px-3 py-2 text-sm ${
                      item.status === 'success'
                        ? 'border-emerald-200 bg-emerald-50 text-emerald-700'
                        : 'border-red-200 bg-red-50 text-red-700'
                    }`}
                  >
                    <span className="truncate pr-3" title={label}>{label}</span>
                    <span className="shrink-0 text-xs font-medium">
                      {item.status === 'success' ? '成功' : '失败'}
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        )}
        {actionError && (
          <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {actionError}
          </div>
        )}
        {dataMode === 'api' && migrationHint > 0 && (
          <div className="mb-4 rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800 flex items-center justify-between">
            <span>检测到本地还有 {migrationHint} 本书未迁移到后端</span>
            <Link href="/settings" className="font-medium underline underline-offset-2 hover:text-amber-900">
              前往迁移
            </Link>
          </div>
        )}
        {droppedFilters.includes('quality') && (
          <div className="mb-4 flex items-center justify-between rounded-lg border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
            <span>质量筛选需要连接后端，当前已忽略该条件。</span>
            <button
              onClick={() => setFilter('quality', null)}
              className="font-medium underline underline-offset-2 hover:text-amber-900"
            >
              清除质量筛选
            </button>
          </div>
        )}
        <div className="flex gap-6">
          {/* Sidebar */}
          <aside className="w-64 flex-shrink-0">
            {/* 智能书架（规则视图，对标 BookLore Magic Shelves） */}
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mb-4">
              <h2 className="font-semibold text-gray-900 mb-3">书架</h2>
              <div className="space-y-1">
                {(Object.keys(SHELF_LABELS) as Array<keyof typeof SHELF_LABELS>).map((shelf) => {
                  const active = filters.shelf === shelf && !hasNonShelfFilters;
                  return (
                    <button
                      key={shelf}
                      onClick={() => setFilter('shelf', shelf)}
                      className={`w-full flex items-center justify-between px-3 py-2 rounded-lg transition-colors ${
                        active
                          ? 'bg-primary-50 text-primary-700'
                          : 'hover:bg-gray-50 text-gray-700'
                      }`}
                      title={shelf === 'reading' ? '进度 0% ~ 100% 之间' : shelf === 'unread' ? '从未打开' : shelf === 'done' ? '进度达 100%' : shelf === 'recent' ? `最近加入的 ${RECENT_SHELF_SIZE} 本` : ''}
                    >
                      <span>{SHELF_LABELS[shelf]}</span>
                      <span className="text-sm text-gray-500">{shelfCounts[shelf]}</span>
                    </button>
                  );
                })}
              </div>
            </div>
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4">
              <div className="flex items-center justify-between mb-4">
                <h2 className="font-semibold text-gray-900">分类</h2>
                <button
                  onClick={() => setShowAddCategory(true)}
                  className="p-1 hover:bg-gray-100 rounded transition-colors"
                >
                  <FolderPlus className="w-4 h-4 text-gray-600" />
                </button>
              </div>
              
              <button
                onClick={() => setFilter('category', null)}
                className={`w-full text-left px-3 py-2 rounded-lg mb-1 transition-colors ${
                  filters.category === null 
                    ? 'bg-primary-50 text-primary-700' 
                    : 'hover:bg-gray-50 text-gray-700'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span>全部书籍</span>
                  <span className="text-sm text-gray-500">{metaQuery.data?.total ?? 0}</span>
                </div>
              </button>
              
              {categories.map(category => (
                <div key={category.id} className="group relative">
                  <button
                    onClick={() => setFilter('category', category.name)}
                    className={`w-full text-left px-3 py-2 rounded-lg mb-1 transition-colors ${
                      filters.category === category.name 
                        ? 'bg-primary-50 text-primary-700' 
                        : 'hover:bg-gray-50 text-gray-700'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span>{category.name}</span>
                      <span className="text-sm text-gray-500">
                        {categoryCounts?.get(category.name) ?? 0}
                      </span>
                    </div>
                  </button>
                  <button
                    onClick={() => handleDeleteCategory(category)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 p-1 hover:bg-red-100 rounded transition-all"
                  >
                    <Trash2 className="w-3 h-3 text-red-500" />
                  </button>
                </div>
              ))}
              
              {showAddCategory && (
                <div className="mt-2 flex gap-2">
                  <input
                    type="text"
                    value={newCategoryName}
                    onChange={(e) => setNewCategoryName(e.target.value)}
                    placeholder="分类名称"
                    className="flex-1 px-2 py-1 text-sm border border-gray-300 rounded focus:ring-1 focus:ring-primary-500 outline-none"
                    onKeyDown={(e) => e.key === 'Enter' && handleAddCategory()}
                    autoFocus
                  />
                  <button
                    onClick={handleAddCategory}
                    className="px-2 py-1 bg-primary-600 text-white text-sm rounded hover:bg-primary-700"
                  >
                    添加
                  </button>
                </div>
              )}
            </div>

            {isApiMode && (
              <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mt-4">
                <div className="flex items-center justify-between mb-3">
                  <h2 className="font-semibold text-gray-900">筛选</h2>
                  {facetsQuery.isFetching && <span className="text-xs text-gray-400">统计中…</span>}
                </div>
                {facetsQuery.isError ? (
                  <div className="text-sm text-red-600">
                    分面统计加载失败
                    <button onClick={() => facetsQuery.refetch()} className="ml-2 underline underline-offset-2">
                      重试
                    </button>
                  </div>
                ) : !facetsQuery.data ? (
                  <div className="space-y-2">
                    {Array.from({ length: 3 }).map((_, i) => (
                      <div key={i} className="h-5 rounded bg-gray-100 animate-pulse" />
                    ))}
                  </div>
                ) : (
                  <div className="space-y-4">
                    <div>
                      <h3 className="text-xs font-medium text-gray-500 mb-1.5">格式</h3>
                      <div className="space-y-1">
                        {limitFacetItems(facetsQuery.data.formats, filters.format, 6).map((item) => {
                          const unset = item.value === '';
                          const active = filters.format === item.value;
                          return (
                            <button
                              key={item.value || '__unset__'}
                              type="button"
                              disabled={unset}
                              aria-pressed={unset ? undefined : active}
                              onClick={() => {
                                if (!unset) setFilter('format', active ? null : item.value);
                              }}
                              className={`w-full flex items-center justify-between rounded px-2 py-1 text-sm transition-colors ${
                                active
                                  ? 'bg-primary-50 text-primary-700'
                                  : unset
                                    ? 'text-gray-400 cursor-default'
                                    : 'text-gray-600 hover:bg-gray-50'
                              }`}
                            >
                              <span className="truncate">{unset ? '未标注' : item.value.toUpperCase()}</span>
                              <span className="text-xs text-gray-400">{item.count}</span>
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    <div>
                      <h3 className="text-xs font-medium text-gray-500 mb-1.5">语言</h3>
                      <div className="space-y-1">
                        {limitFacetItems(facetsQuery.data.languages, filters.language, 6).map((item) => {
                          const active = filters.language === item.value;
                          return (
                            <button
                              key={item.value}
                              type="button"
                              aria-pressed={active}
                              onClick={() => setFilter('language', active ? null : item.value)}
                              className={`w-full flex items-center justify-between rounded px-2 py-1 text-sm transition-colors ${
                                active
                                  ? 'bg-primary-50 text-primary-700'
                                  : 'text-gray-600 hover:bg-gray-50'
                              }`}
                            >
                              <span className="truncate">{languageLabel(item.value)}</span>
                              <span className="text-xs text-gray-400">{item.count}</span>
                            </button>
                          );
                        })}
                      </div>
                    </div>

                    <div>
                      <h3 className="text-xs font-medium text-gray-500 mb-1.5">标签</h3>
                      {limitFacetItems(facetsQuery.data.tags, filters.tag, 10).length === 0 ? (
                        <p className="px-2 text-xs text-gray-400">暂无标签</p>
                      ) : (
                        <div className="space-y-1">
                          {limitFacetItems(facetsQuery.data.tags, filters.tag, 10).map((item) => {
                            const active = filters.tag === item.value;
                            return (
                              <button
                                key={item.value}
                                type="button"
                                aria-pressed={active}
                                onClick={() => setFilter('tag', active ? null : item.value)}
                                className={`w-full flex items-center justify-between rounded px-2 py-1 text-sm transition-colors ${
                                  active
                                    ? 'bg-primary-50 text-primary-700'
                                    : 'text-gray-600 hover:bg-gray-50'
                                }`}
                              >
                                <span className="truncate">{item.value}</span>
                                <span className="text-xs text-gray-400">{item.count}</span>
                              </button>
                            );
                          })}
                        </div>
                      )}
                      {facetsQuery.data.truncated.tags && (
                        <p className="mt-1 px-2 text-xs text-gray-400">仅显示计数最高的 100 个标签</p>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )}
            
            <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-4 mt-4">
              <h2 className="font-semibold text-gray-900 mb-3">数据管理</h2>
              <div className="space-y-2">
                <button
                  onClick={handleExport}
                  className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 rounded-lg transition-colors"
                >
                  <Download className="w-4 h-4" />
                  备份数据
                </button>
                <label className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-700 hover:bg-gray-50 rounded-lg transition-colors cursor-pointer">
                  <RotateCcw className="w-4 h-4" />
                  恢复数据
                  <input
                    type="file"
                    accept=".json"
                    onChange={handleImport}
                    className="hidden"
                  />
                </label>
              </div>
            </div>
          </aside>

          {/* Main Content */}
          <main className="flex-1">
            {showStats && <StatisticsPanel onClose={() => setShowStats(false)} />}

            {showOnboarding && <OnboardingBanner onDismiss={handleDismissOnboarding} />}

            <div className="bg-white rounded-lg shadow-sm border border-gray-200">
              <div className="px-6 py-4 border-b border-gray-200">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-lg font-semibold text-gray-900">
                      {filters.category ||
                        (filters.shelf !== 'all' ? SHELF_LABELS[filters.shelf] : '全部书籍')}
                    </h2>
                    <p className="text-sm text-gray-500 mt-1" aria-live="polite">
                      共 {totalBooks} 本书
                      {hasMore && <span className="text-gray-400">（已加载 {books.length}）</span>}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <label className="flex items-center gap-1.5 text-sm text-gray-500">
                      <span className="hidden sm:inline">排序</span>
                      <select
                        aria-label="排序方式"
                        value={filters.sort}
                        disabled={filters.shelf === 'recent'}
                        onChange={(e) => setFilter('sort', e.target.value as LibrarySort)}
                        title={filters.shelf === 'recent' ? '最近添加固定按添加时间排序' : undefined}
                        className="rounded-lg border border-gray-300 bg-white px-2 py-1.5 text-sm text-gray-700 disabled:bg-gray-50 disabled:text-gray-400"
                      >
                        {(Object.keys(SORT_LABELS) as LibrarySort[]).map((sort) => (
                          <option key={sort} value={sort}>{SORT_LABELS[sort]}</option>
                        ))}
                      </select>
                    </label>
                    <button
                      onClick={() => setFilter('view', 'grid')}
                      aria-label="网格视图"
                      aria-pressed={filters.view === 'grid'}
                      className={`p-2 rounded-lg transition-colors ${
                        filters.view === 'grid' ? 'bg-primary-50 text-primary-600' : 'text-gray-500 hover:bg-gray-100'
                      }`}
                    >
                      <Grid3X3 className="w-5 h-5" />
                    </button>
                    <button
                      onClick={() => setFilter('view', 'list')}
                      aria-label="列表视图"
                      aria-pressed={filters.view === 'list'}
                      className={`p-2 rounded-lg transition-colors ${
                        filters.view === 'list' ? 'bg-primary-50 text-primary-600' : 'text-gray-500 hover:bg-gray-100'
                      }`}
                    >
                      <List className="w-5 h-5" />
                    </button>
                  </div>
                </div>
              </div>

              {activeChips.length > 0 && (
                <div className="px-6 py-2 border-b border-gray-100 flex items-center gap-2 overflow-x-auto">
                  {activeChips.map((chip) => (
                    <FilterChip
                      key={chip.key}
                      label={chip.label}
                      active
                      onClear={() => removeFilter(chip.key)}
                    />
                  ))}
                  <button
                    onClick={clearFilters}
                    className="ml-auto whitespace-nowrap text-sm text-primary-600 hover:text-primary-700"
                  >
                    清除全部
                  </button>
                </div>
              )}

              {/* 质量筛选 chips（B1-10） */}
              {isApiMode ? (
                <div className="px-6 py-2 border-b border-gray-100 flex items-center gap-2 overflow-x-auto">
                  <FilterChip
                    label="全部"
                    active={!filters.quality}
                    onClick={() => setFilter('quality', null)}
                  />
                  <FilterChip
                    label="有质量问题"
                    count={qualityQuery.data ? ((qualityQuery.data.counts.empty_content ?? 0) + (qualityQuery.data.counts.short_content ?? 0) + (qualityQuery.data.counts.no_cover ?? 0) + (qualityQuery.data.counts.unclassified ?? 0)) : undefined}
                    active={filters.quality === 'issues'}
                    onClick={() => setFilter('quality', filters.quality === 'issues' ? null : 'issues')}
                  />
                  <FilterChip
                    label="正文为空"
                    count={qualityQuery.data?.counts.empty_content}
                    active={filters.quality === 'empty_content'}
                    onClick={() => setFilter('quality', filters.quality === 'empty_content' ? null : 'empty_content')}
                  />
                  <FilterChip
                    label="无封面"
                    count={qualityQuery.data?.counts.no_cover}
                    active={filters.quality === 'no_cover'}
                    onClick={() => setFilter('quality', filters.quality === 'no_cover' ? null : 'no_cover')}
                  />
                  <FilterChip
                    label="需 OCR"
                    count={qualityQuery.data?.counts.needs_ocr}
                    active={filters.quality === 'needs_ocr'}
                    onClick={() => setFilter('quality', filters.quality === 'needs_ocr' ? null : 'needs_ocr')}
                  />
                  <FilterChip
                    label="未分类"
                    count={qualityQuery.data?.counts.unclassified}
                    active={filters.quality === 'unclassified'}
                    onClick={() => setFilter('quality', filters.quality === 'unclassified' ? null : 'unclassified')}
                  />
                  <Link href="/quality" className="ml-auto text-sm text-primary-600 hover:text-primary-700 whitespace-nowrap">
                    质量中心 →
                  </Link>
                </div>
              ) : (
                <div className="px-6 py-2 border-b border-gray-100 text-xs text-gray-400">
                  质量筛选需要连接后端 · <Link href="/settings" className="text-primary-500">去设置</Link>
                </div>
              )}

              {isLoading ? (
                <div className="p-6 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                  {Array.from({ length: 12 }).map((_, i) => (
                    <div key={i} className="bg-white border border-gray-200 rounded-lg overflow-hidden animate-pulse">
                      <div className="aspect-[3/4] bg-gray-200" />
                      <div className="p-3 space-y-2">
                        <div className="h-4 bg-gray-200 rounded w-3/4" />
                        <div className="h-3 bg-gray-200 rounded w-1/2" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : metaQuery.isError ? (
                <StatePanel
                  variant="error"
                  title="书架元数据加载失败"
                  description={metaQuery.error instanceof Error ? metaQuery.error.message : '未知错误'}
                  action={{ label: '重试', onClick: () => metaQuery.refetch() }}
                />
              ) : booksQuery.isError ? (
                <StatePanel
                  variant="error"
                  title="书目加载失败"
                  description={booksQuery.error instanceof Error ? booksQuery.error.message : '未知错误'}
                  action={{ label: '重试', onClick: () => booksQuery.refetch() }}
                />
              ) : importing ? (
                <StatePanel variant="loading" title="正在导入书籍..." />
              ) : books.length === 0 ? (
                isFiltered ? (
                  <StatePanel
                    variant="empty"
                    title="没有符合条件的书"
                    description="试试调整搜索关键词或筛选条件"
                    action={{ label: '清除筛选', onClick: clearFilters }}
                  />
                ) : isApiMode ? (
                  <StatePanel
                    variant="empty"
                    title="书架还是空的"
                    description="导入 EPUB/PDF，或到导入中心扫描目录"
                    action={{ label: '导入书籍', onClick: () => (document.querySelector('[data-import-trigger]') as HTMLInputElement)?.click() }}
                  />
                ) : (
                  <StatePanel
                    variant="empty"
                    title="本地书架没有书"
                    description="连接后端或先导入本地文件"
                    action={{ label: '去设置', onClick: () => router.push('/settings') }}
                  />
                )
              ) : filters.view === 'grid' ? (
                <div className="p-6 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                  {books.map(book => (
                    <div
                      key={book.id}
                      className="group bg-white border border-gray-200 rounded-lg overflow-hidden hover:shadow-lg transition-shadow"
                    >
                      <div 
                        className="aspect-[3/4] bg-gray-100 relative cursor-pointer overflow-hidden"
                        onClick={() => openReader(book)}
                      >
                        <BookCover book={book} variant="grid" />
                        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors" />
                        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent p-3 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
                          <button onClick={() => openReader(book)} className="w-full py-2 bg-white/90 text-gray-900 rounded text-sm font-medium hover:bg-white transition-colors">
                            开始阅读
                          </button>
                        </div>
                      </div>
                      <div className="p-3">
                        <h3 className="font-medium text-gray-900 truncate" title={book.title}>
                          {book.title}
                        </h3>
                        <p className="text-sm text-gray-500 truncate mt-1">{book.author}</p>
                        <div className="flex items-center justify-between mt-2">
                          <span className="text-xs text-gray-400">{book.format.toUpperCase()}</span>
                          <div className="flex items-center gap-1">
                            <button
                              onClick={() => setEditingBook(book)}
                              className="p-1 hover:bg-gray-100 rounded transition-colors"
                            >
                              <Edit3 className="w-3 h-3 text-gray-500" />
                            </button>
                            <button
                              onClick={() => handleDeleteBook(book)}
                              className="p-1 hover:bg-red-50 rounded transition-colors"
                            >
                              <Trash2 className="w-3 h-3 text-red-500" />
                            </button>
                          </div>
                        </div>
                        {book.readProgress > 0 && (
                          <div className="mt-2">
                            <div className="h-1 bg-gray-200 rounded-full overflow-hidden">
                              <div 
                                className="h-full bg-primary-500 rounded-full transition-all"
                                style={{ width: `${book.readProgress}%` }}
                              />
                            </div>
                            <p className="text-xs text-gray-400 mt-1">已读 {book.readProgress}%</p>
                          </div>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="divide-y divide-gray-200">
                  {books.map(book => (
                    <div
                      key={book.id}
                      className="px-6 py-4 flex items-center gap-4 hover:bg-gray-50 transition-colors group"
                    >
                      <div 
                        className="w-16 h-20 bg-gray-100 rounded flex-shrink-0 overflow-hidden cursor-pointer"
                        onClick={() => openReader(book)}
                      >
                        <BookCover book={book} variant="list" />
                      </div>
                      <div className="flex-1 min-w-0">
                        <h3 className="font-medium text-gray-900 truncate">{book.title}</h3>
                        <p className="text-sm text-gray-500">{book.author}</p>
                        <div className="flex items-center gap-4 mt-1 text-xs text-gray-400">
                          <span>{book.publisher || '未知出版社'}</span>
                          <span>{formatFileSize(book.fileSize)}</span>
                          <span>{book.format.toUpperCase()}</span>
                          {book.lastReadAt && (
                            <span>上次阅读: {formatDate(book.lastReadAt)}</span>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
                        <button
                          onClick={() => openReader(book)}
                          className="p-2 hover:bg-primary-50 text-primary-600 rounded-lg transition-colors"
                        >
                          <Eye className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => setEditingBook(book)}
                          className="p-2 hover:bg-gray-100 text-gray-600 rounded-lg transition-colors"
                        >
                          <Edit3 className="w-4 h-4" />
                        </button>
                        <button
                          onClick={() => handleDeleteBook(book)}
                          className="p-2 hover:bg-red-50 text-red-600 rounded-lg transition-colors"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {hasMore && (
                <div className="px-6 pb-8">
                  <button
                    onClick={loadMoreBooks}
                    disabled={booksQuery.isFetchingNextPage}
                    className="w-full py-3 text-sm text-gray-600 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-60"
                  >
                    {booksQuery.isFetchingNextPage
                      ? '加载中...'
                      : `加载更多（剩余 ${Math.max(0, totalBooks - books.length)} 本）`}
                  </button>
                </div>
              )}
            </div>
          </main>
        </div>
      </div>

      {editingBook && (
        <BookEditModal
          book={editingBook}
          categories={categories}
          onClose={() => setEditingBook(null)}
          onSave={() => void refetchLibrary()}
        />
      )}
    </div>
  );
}

