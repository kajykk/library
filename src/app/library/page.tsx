'use client';

import { useState, useEffect, useMemo, Suspense } from 'react';
import { Book, Category, exportData, importData } from '@/lib/db';
import {
  loadLibrary,
  importBookFile,
  deleteBookCompletely,
  addCategoryByName,
  deleteCategoryAndReset,
  getBookCoverUrl,
  countLocalBooks,
  DataSourceMode,
} from '@/lib/dataSource';
import { formatFileSize, formatDate } from '@/lib/utils';
import {
  BookOpen,
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
import { useRouter, useSearchParams } from 'next/navigation';
import BookEditModal from '@/components/BookEditModal';
import StatisticsPanel from '@/components/StatisticsPanel';

export default function LibraryPage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-gray-50 p-16 text-center text-gray-400">加载中...</div>}>
      <LibraryPageInner />
    </Suspense>
  );
}

function LibraryPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const authorFilter = searchParams.get('author') || '';
  const [books, setBooks] = useState<Book[]>([]);
  const [categories, setCategories] = useState<Category[]>([]);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<'grid' | 'list'>('grid');
  const [isLoading, setIsLoading] = useState(true);
  const [editingBook, setEditingBook] = useState<Book | null>(null);
  const [showStats, setShowStats] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [showAddCategory, setShowAddCategory] = useState(false);
  const [newCategoryName, setNewCategoryName] = useState('');
  const [importing, setImporting] = useState(false);
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
  const [coverUrls, setCoverUrls] = useState<Record<string, string>>({});
  const [dataMode, setDataMode] = useState<DataSourceMode>('local');
  const [migrationHint, setMigrationHint] = useState(0);

  useEffect(() => {
    loadData();
  }, []);

  const loadData = async () => {
    setIsLoading(true);
    try {
      const { books, categories, mode } = await loadLibrary();
      setBooks(books);
      setCategories(categories);
      setDataMode(mode);

      // API 模式下若云端为空而本地有数据，提示迁移
      if (mode === 'api' && books.length === 0) {
        setMigrationHint(await countLocalBooks());
      } else {
        setMigrationHint(0);
      }

      const coverEntries = await Promise.all(
        books.map(async (book) => {
          const url = await getBookCoverUrl(book.id);
          return [book.id, url] as const;
        })
      );
      setCoverUrls(Object.fromEntries(coverEntries.filter(([, url]) => Boolean(url))) as Record<string, string>);
    } finally {
      setIsLoading(false);
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;

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
      for (const file of Array.from(files)) {
        const format = file.name.split('.').pop()?.toLowerCase() || '';
        if (!['epub', 'pdf', 'mobi', 'azw3', 'azw', 'txt'].includes(format)) {
          report.results.push({ name: file.name, status: 'failed', reason: '不支持的文件格式' });
          continue;
        }

        try {
          const book = await importBookFile(file, selectedCategory || '未分类');
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
      await loadData();
    } finally {
      setImporting(false);
      e.target.value = '';
    }
  };

  const handleFolderUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;

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
      for (const file of Array.from(files)) {
        const format = file.name.split('.').pop()?.toLowerCase() || '';
        if (!['epub', 'pdf', 'mobi', 'azw3', 'azw', 'txt'].includes(format)) {
          report.results.push({ name: file.name, status: 'failed', reason: '不支持的文件格式' });
          continue;
        }

        // 父目录名作为分类（与 batch_import.py 一致）；无目录结构时用当前选中分类
        const rel = (file as File & { webkitRelativePath?: string }).webkitRelativePath || '';
        const parts = rel.split('/');
        const category =
          parts.length >= 2 ? parts[parts.length - 2] : selectedCategory || '未分类';

        try {
          const book = await importBookFile(file, category);
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
      await loadData();
    } finally {
      setImporting(false);
      e.target.value = '';
    }
  };

  const handleDeleteBook = async (book: Book) => {
    if (!confirm(`确定要删除《${book.title}》吗？`)) return;

    try {
      await deleteBookCompletely(book);
      await loadData();
      setActionError(null);
    } catch (error) {
      setActionError((error as Error).message || '删除书籍失败');
    }
  };

  const handleAddCategory = async () => {
    const name = newCategoryName.trim();
    if (!name) return;
    if (categories.some((c) => c.name === name)) {
      alert(`分类"${name}"已存在`);
      return;
    }

    try {
      const category = await addCategoryByName(name);
      setCategories((prev) => [...prev, category]);
      setNewCategoryName('');
      setShowAddCategory(false);
    } catch (error) {
      alert((error as Error).message);
    }
  };

  const handleDeleteCategory = async (category: Category) => {
    if (!confirm(`确定要删除分类"${category.name}"吗？该分类下的书籍将变为未分类。`)) return;
    try {
      await deleteCategoryAndReset(category);
    } catch (error) {
      setActionError((error as Error).message || '删除分类失败');
      return;
    }
    if (selectedCategory === category.name) {
      setSelectedCategory(null);
    }
    await loadData();
  };

  const handleExport = async () => {
    if (!confirm('备份包含书目、分类、书签、笔记、阅读记录和封面，但不包含书籍文件本身（文件保留在浏览器本地存储中）。继续导出？')) return;

    const data = await exportData();
    const blob = new Blob([data], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `ebook_backup_${Date.now()}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleImport = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const reader = new FileReader();
    reader.onload = async (event) => {
      try {
        const data = event.target?.result as string;
        await importData(data);
        await loadData();
        alert('数据恢复成功！已与现有数据合并（不会删除现有条目）。');
      } catch (error) {
        alert('数据恢复失败：' + (error as Error).message);
      }
    };
    reader.readAsText(file);
    e.target.value = '';
  };

  const filteredBooks = useMemo(() => {
    const normalizedQuery = searchQuery.trim().toLowerCase();

    return books.filter((book) => {
      const matchesCategory = !selectedCategory || book.category === selectedCategory;
      const searchableText = [book.title, book.author, book.publisher, book.description]
        .join(' ')
        .toLowerCase();
      const matchesQuery = !normalizedQuery || searchableText.includes(normalizedQuery);
      const matchesAuthor = !authorFilter || (book.author || '').includes(authorFilter);
      return matchesCategory && matchesQuery && matchesAuthor;
    });
  }, [books, searchQuery, selectedCategory, authorFilter]);

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
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
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
        {authorFilter && (
          <div className="mb-4 flex items-center gap-2 text-sm">
            <span className="text-gray-500">作者筛选：</span>
            <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-primary-200 bg-primary-50 text-primary-700">
              {authorFilter}
              <button
                onClick={() => router.push('/library')}
                className="hover:text-primary-900"
                title="清除作者筛选"
              >
                ×
              </button>
            </span>
            <span className="text-xs text-gray-400">共 {filteredBooks.length} 本</span>
          </div>
        )}
        <div className="flex gap-6">
          {/* Sidebar */}
          <aside className="w-64 flex-shrink-0">
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
                onClick={() => setSelectedCategory(null)}
                className={`w-full text-left px-3 py-2 rounded-lg mb-1 transition-colors ${
                  selectedCategory === null 
                    ? 'bg-primary-50 text-primary-700' 
                    : 'hover:bg-gray-50 text-gray-700'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span>全部书籍</span>
                  <span className="text-sm text-gray-500">{books.length}</span>
                </div>
              </button>
              
              {categories.map(category => (
                <div key={category.id} className="group relative">
                  <button
                    onClick={() => setSelectedCategory(category.name)}
                    className={`w-full text-left px-3 py-2 rounded-lg mb-1 transition-colors ${
                      selectedCategory === category.name 
                        ? 'bg-primary-50 text-primary-700' 
                        : 'hover:bg-gray-50 text-gray-700'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span>{category.name}</span>
                      <span className="text-sm text-gray-500">
                        {books.filter(b => b.category === category.name).length}
                      </span>
                    </div>
                  </button>
                  <button
                    onClick={() => handleDeleteCategory(category)}
                    className="absolute right-2 top-1/2 -translate-y-1/2 opacity-0 group-hover:opacity-100 p-1 hover:bg-red-100 rounded transition-all"
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
            
            <div className="bg-white rounded-lg shadow-sm border border-gray-200">
              <div className="px-6 py-4 border-b border-gray-200 flex items-center justify-between">
                <div>
                  <h2 className="text-lg font-semibold text-gray-900">
                    {selectedCategory || '全部书籍'}
                  </h2>
                  <p className="text-sm text-gray-500 mt-1">
                    共 {filteredBooks.length} 本书
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={() => setViewMode('grid')}
                    className={`p-2 rounded-lg transition-colors ${
                      viewMode === 'grid' ? 'bg-primary-50 text-primary-600' : 'text-gray-500 hover:bg-gray-100'
                    }`}
                  >
                    <Grid3X3 className="w-5 h-5" />
                  </button>
                  <button
                    onClick={() => setViewMode('list')}
                    className={`p-2 rounded-lg transition-colors ${
                      viewMode === 'list' ? 'bg-primary-50 text-primary-600' : 'text-gray-500 hover:bg-gray-100'
                    }`}
                  >
                    <List className="w-5 h-5" />
                  </button>
                </div>
              </div>
              
              {isLoading ? (
                <div className="p-12 text-center text-gray-500">加载中...</div>
              ) : importing ? (
                <div className="p-12 text-center text-gray-500">正在导入书籍...</div>
              ) : filteredBooks.length === 0 ? (
                <div className="p-12 text-center">
                  <BookOpen className="w-16 h-16 text-gray-300 mx-auto mb-4" />
                  <p className="text-gray-500">暂无书籍</p>
                  <p className="text-sm text-gray-400 mt-1">点击右上角导入书籍开始添加</p>
                </div>
              ) : viewMode === 'grid' ? (
                <div className="p-6 grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 gap-6">
                  {filteredBooks.map(book => (
                    <div
                      key={book.id}
                      className="group bg-white border border-gray-200 rounded-lg overflow-hidden hover:shadow-lg transition-shadow"
                    >
                      <div 
                        className="aspect-[3/4] bg-gray-100 relative cursor-pointer overflow-hidden"
                        onClick={() => openReader(book)}
                      >
                        {coverUrls[book.id] ? (
                          <img
                            src={coverUrls[book.id]}
                            alt={book.title}
                            loading="lazy"
                            className="w-full h-full object-cover"
                          />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center">
                            <BookOpen className="w-12 h-12 text-gray-300" />
                          </div>
                        )}
                        <div className="absolute inset-0 bg-black/0 group-hover:bg-black/20 transition-colors" />
                        <div className="absolute bottom-0 left-0 right-0 bg-gradient-to-t from-black/60 to-transparent p-3 opacity-0 group-hover:opacity-100 transition-opacity">
                          <button className="w-full py-2 bg-white/90 text-gray-900 rounded text-sm font-medium hover:bg-white transition-colors">
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
                  {filteredBooks.map(book => (
                    <div
                      key={book.id}
                      className="px-6 py-4 flex items-center gap-4 hover:bg-gray-50 transition-colors group"
                    >
                      <div 
                        className="w-16 h-20 bg-gray-100 rounded flex-shrink-0 overflow-hidden cursor-pointer"
                        onClick={() => openReader(book)}
                      >
                        {coverUrls[book.id] ? (
                          <img
                            src={coverUrls[book.id]}
                            alt={book.title}
                            loading="lazy"
                            className="w-full h-full object-cover"
                          />
                        ) : (
                          <div className="w-full h-full flex items-center justify-center">
                            <BookOpen className="w-6 h-6 text-gray-300" />
                          </div>
                        )}
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
                      <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
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
            </div>
          </main>
        </div>
      </div>

      {editingBook && (
        <BookEditModal
          book={editingBook}
          categories={categories}
          onClose={() => setEditingBook(null)}
          onSave={loadData}
        />
      )}
    </div>
  );
}

