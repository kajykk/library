'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import {
  getApiBaseUrl,
  getApiToken,
  saveApiConfig,
  checkHealth,
  migrateMeta,
  migrateFile,
  migrateCover,
  downloadBackup,
  restoreBackup,
  clipUrl,
  startReindex,
  getReindexStatus,
  ReindexStatus,
} from '@/lib/api/client';
import { getBooks, getCategories, exportData, getAllFileRecords, getAllCoverRecords, initDB } from '@/lib/db';
import { resetModeCache } from '@/lib/dataSource';
import { ArrowLeft, Settings2, Plug, DatabaseZap, CheckCircle2, XCircle, Loader2, Download, Upload, Globe, RefreshCw, Search } from 'lucide-react';

interface MigrationState {
  status: 'idle' | 'running' | 'done' | 'error';
  progress: { done: number; total: number };
  summary: string | null;
  errors: string[];
}

export default function SettingsPage() {
  const [apiBase, setApiBase] = useState('');
  const [apiToken, setApiToken] = useState('');
  const [connectionStatus, setConnectionStatus] = useState<'unknown' | 'ok' | 'fail' | 'testing'>('unknown');
  const [migration, setMigration] = useState<MigrationState>({
    status: 'idle',
    progress: { done: 0, total: 0 },
    summary: null,
    errors: [],
  });
  const [backupMsg, setBackupMsg] = useState<string | null>(null);
  const [clipUrlInput, setClipUrlInput] = useState('');
  const [clipMsg, setClipMsg] = useState<string | null>(null);
  const [reindex, setReindex] = useState<ReindexStatus | null>(null);

  useEffect(() => {
    setApiBase(getApiBaseUrl());
    setApiToken(getApiToken());
    const poll = async () => {
      try {
        setReindex(await getReindexStatus());
      } catch {
        /* 后端未连接时忽略 */
      }
    };
    poll();
    const timer = setInterval(() => {
      if (!reindex?.running) return;
      poll();
    }, 2000);
    return () => clearInterval(timer);
  }, [reindex?.running]);

  const handleReindex = async () => {
    try {
      const res = await startReindex();
      setReindex(
        'status' in res
          ? { running: true, total: 0, done: 0, updated: 0, skipped: 0, empty: 0, sha_backfilled: 0, started_at: new Date().toISOString(), finished_at: null, error: null }
          : null,
      );
    } catch (err) {
      setClipMsg('重建索引失败：' + (err as Error).message);
    }
  };

  const handleSaveAndTest = async () => {
    saveApiConfig(apiBase, apiToken);
    resetModeCache();
    setConnectionStatus('testing');
    const ok = await checkHealth();
    setConnectionStatus(ok ? 'ok' : 'fail');
  };

  const dataUrlToBlob = async (dataUrl: string): Promise<Blob> => {
    const resp = await fetch(dataUrl);
    return resp.blob();
  };

  const handleBackup = async () => {
    setBackupMsg('正在生成备份...');
    try {
      await downloadBackup();
      setBackupMsg('备份已下载（zip 含全部元数据与文件）');
    } catch (err) {
      setBackupMsg('备份失败：' + (err as Error).message);
    }
  };

  const handleRestore = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (!confirm('恢复将把备份内容合并进当前库（不会删除现有数据，同名条目会被覆盖）。继续？')) {
      e.target.value = '';
      return;
    }
    setBackupMsg('正在恢复...');
    try {
      const result = await restoreBackup(file);
      setBackupMsg(
        `恢复完成：文档 ${result.restored.documents || 0}、批注 ${result.restored.annotations || 0}、文件 ${result.files_restored} 个`
      );
    } catch (err) {
      setBackupMsg('恢复失败：' + (err as Error).message);
    }
    e.target.value = '';
  };

  const handleClip = async () => {
    const url = clipUrlInput.trim();
    if (!url) return;
    setClipMsg('正在抓取...');
    try {
      const result = await clipUrl(url);
      setClipMsg(`已剪藏《${result.title}》，可在笔记页的"剪藏"列表查看`);
      setClipUrlInput('');
    } catch (err) {
      setClipMsg('剪藏失败：' + (err as Error).message);
    }
  };

  const handleMigrate = async () => {
    setMigration({ status: 'running', progress: { done: 0, total: 0 }, summary: null, errors: [] });
    try {
      await initDB();

      // 1) 读取本地 IndexedDB 全量元数据（exportData 含 books/categories/bookmarks/notes/readingRecords）
      const [exportJson, files, covers] = await Promise.all([
        exportData(),
        getAllFileRecords(),
        getAllCoverRecords(),
      ]);
      const local = JSON.parse(exportJson);
      const books = await getBooks();
      const categories = await getCategories();

      const payload = {
        books,
        categories,
        bookmarks: local.bookmarks || [],
        notes: local.notes || [],
        readingRecords: local.readingRecords || [],
      };

      // 2) 提交元数据
      const result = await migrateMeta(payload);
      const mapping = new Map(result.documents.map((d) => [d.old_id, d.new_id]));
      const fileMap = new Map(files.map((f) => [f.id, f.data]));
      const coverMap = new Map(covers.map((c) => [c.id, c.data]));

      // 3) 逐本上传原始文件与封面
      const errors: string[] = [];
      const total = books.length;
      let done = 0;
      for (const book of books) {
        const newId = mapping.get(book.id);
        if (!newId) {
          errors.push(`${book.title}：元数据迁移失败`);
          continue;
        }
        try {
          const ab = fileMap.get(book.id);
          if (ab) {
            await migrateFile(newId, new Blob([ab]), `${book.title}.${book.format || 'bin'}`);
          }
          const coverData = coverMap.get(book.id);
          if (coverData) {
            const blob = coverData.startsWith('data:')
              ? await dataUrlToBlob(coverData)
              : await (await fetch(`data:image/jpeg;base64,${coverData}`)).blob();
            await migrateCover(newId, blob, `cover.${coverData.startsWith('data:image/png') ? 'png' : 'jpg'}`);
          }
        } catch (err) {
          errors.push(`${book.title}：${(err as Error).message}`);
        }
        done += 1;
        setMigration((prev) => ({ ...prev, progress: { done, total } }));
      }

      setMigration({
        status: 'done',
        progress: { done, total },
        summary:
          `迁移完成：分类 ${result.collections} 个，书籍 ${result.documents.length} 本，` +
          `批注 ${result.annotations} 条，阅读记录 ${result.reading_records} 条` +
          (errors.length ? `，失败 ${errors.length} 项` : ''),
        errors,
      });
    } catch (error) {
      setMigration({
        status: 'error',
        progress: { done: 0, total: 0 },
        summary: null,
        errors: [(error as Error).message],
      });
    }
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow-sm border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-16">
          <div className="flex items-center gap-3">
            <Link href="/library" className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
              <ArrowLeft className="w-5 h-5" />
            </Link>
            <Settings2 className="w-6 h-6 text-primary-600" />
            <h1 className="text-xl font-bold text-gray-900">设置</h1>
          </div>
        </div>
      </header>

      <main className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
        {/* 连接配置 */}
        <section className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="font-semibold text-gray-900 mb-1">后端连接</h2>
          <p className="text-sm text-gray-500 mb-4">个人知识库后端（FastAPI）的地址与访问令牌</p>
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API 地址</label>
              <input
                type="text"
                value={apiBase}
                onChange={(e) => setApiBase(e.target.value)}
                placeholder="http://localhost:8000"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 outline-none"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">API Token</label>
              <input
                type="password"
                value={apiToken}
                onChange={(e) => setApiToken(e.target.value)}
                placeholder="与服务端 KB_API_TOKEN 一致"
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 outline-none"
              />
            </div>
            <div className="flex items-center gap-3">
              <button
                onClick={handleSaveAndTest}
                className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
              >
                <Plug className="w-4 h-4" />
                保存并测试连接
              </button>
              {connectionStatus === 'testing' && <span className="text-sm text-gray-500 flex items-center gap-1"><Loader2 className="w-4 h-4 animate-spin" />测试中...</span>}
              {connectionStatus === 'ok' && (
                <span className="text-sm text-green-600 flex items-center gap-1"><CheckCircle2 className="w-4 h-4" />连接正常</span>
              )}
              {connectionStatus === 'fail' && (
                <span className="text-sm text-red-600 flex items-center gap-1"><XCircle className="w-4 h-4" />无法连接，请检查地址与令牌</span>
              )}
            </div>
          </div>
        </section>

        {/* 全文索引 */}
        <section className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="font-semibold text-gray-900 mb-1">全文索引维护</h2>
          <p className="text-sm text-gray-500 mb-4">
            为库中书籍提取正文并建立全文搜索索引。首次迁移后建议执行一次；
            任务在后台运行，可在本页随时查看进度。扫描版 PDF 会按单文件时间预算跳过。
          </p>
          {reindex?.running && (
            <div className="mb-4">
              <div className="flex items-center justify-between text-sm text-gray-600 mb-1">
                <span className="flex items-center gap-1">
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  正在重建索引...
                </span>
                <span>
                  {reindex.done} / {reindex.total || '?'}
                  （已更新 {reindex.updated}，跳过 {reindex.skipped}）
                </span>
              </div>
              <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary-500 rounded-full transition-all"
                  style={{ width: `${reindex.total ? Math.min(100, (reindex.done / reindex.total) * 100) : 0}%` }}
                />
              </div>
            </div>
          )}
          {reindex && !reindex.running && reindex.finished_at && (
            <div className="mb-4 rounded-lg bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
              上次索引完成：处理 {reindex.total} 个文档，更新 {reindex.updated}，跳过 {reindex.skipped}
              {reindex.error ? `；任务出错：${reindex.error}` : ''}
            </div>
          )}
          <button
            onClick={handleReindex}
            disabled={reindex?.running}
            className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${reindex?.running ? 'animate-spin' : ''}`} />
            {reindex?.running ? '索引运行中...' : '重建全文索引'}
          </button>
        </section>

        {/* 数据迁移 */}
        <section className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="font-semibold text-gray-900 mb-1">本地数据迁移到后端</h2>
          <p className="text-sm text-gray-500 mb-4">
            将浏览器本地 IndexedDB 中的全部书籍（含文件）、分类、书签、笔记与阅读记录上传到后端数据库。
            迁移为合并写入，不会删除本地数据；重复点击会产生重复条目。
          </p>

          {migration.status === 'running' && (
            <div className="mb-4">
              <div className="flex items-center justify-between text-sm text-gray-600 mb-1">
                <span>正在上传文件...</span>
                <span>{migration.progress.done} / {migration.progress.total}</span>
              </div>
              <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
                <div
                  className="h-full bg-primary-500 rounded-full transition-all"
                  style={{ width: `${migration.progress.total ? (migration.progress.done / migration.progress.total) * 100 : 0}%` }}
                />
              </div>
            </div>
          )}

          {migration.summary && (
            <div className={`mb-4 rounded-lg px-4 py-3 text-sm ${migration.status === 'error' ? 'bg-red-50 text-red-700' : 'bg-emerald-50 text-emerald-700'}`}>
              {migration.summary}
            </div>
          )}

          {migration.errors.length > 0 && (
            <div className="mb-4 space-y-1">
              {migration.errors.map((err, i) => (
                <div key={i} className="rounded-lg bg-red-50 px-3 py-2 text-sm text-red-700">{err}</div>
              ))}
            </div>
          )}

          <button
            onClick={handleMigrate}
            disabled={migration.status === 'running'}
            className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
          >
            <DatabaseZap className="w-4 h-4" />
            {migration.status === 'running' ? '迁移中...' : '开始迁移'}
          </button>
        </section>

        {/* 全量备份 */}
        <section className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="font-semibold text-gray-900 mb-1">全量备份与恢复</h2>
          <p className="text-sm text-gray-500 mb-4">
            生成 zip 备份（含全部元数据、书籍文件与封面）；恢复采用合并写入，不删除现有数据。
          </p>
          {backupMsg && (
            <div className="mb-4 rounded-lg bg-gray-50 px-4 py-3 text-sm text-gray-700">{backupMsg}</div>
          )}
          <div className="flex items-center gap-3">
            <button
              onClick={handleBackup}
              className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
            >
              <Download className="w-4 h-4" />
              下载备份 (zip)
            </button>
            <label className="flex items-center gap-2 px-4 py-2 border border-gray-300 text-gray-700 rounded-lg hover:bg-gray-50 cursor-pointer transition-colors">
              <Upload className="w-4 h-4" />
              从备份恢复
              <input type="file" accept=".zip" onChange={handleRestore} className="hidden" />
            </label>
          </div>
        </section>

        {/* 网页剪藏 */}
        <section className="bg-white rounded-lg shadow-sm border border-gray-200 p-6">
          <h2 className="font-semibold text-gray-900 mb-1">网页剪藏</h2>
          <p className="text-sm text-gray-500 mb-4">
            输入网页地址，后端抓取并自动提取正文存为「剪藏」文档（可检索、可双链引用）。
          </p>
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <Globe className="w-4 h-4 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
              <input
                type="url"
                value={clipUrlInput}
                onChange={(e) => setClipUrlInput(e.target.value)}
                placeholder="https://example.com/article"
                className="w-full pl-9 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 outline-none"
              />
            </div>
            <button
              onClick={handleClip}
              disabled={!clipUrlInput.trim()}
              className="px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
            >
              剪藏
            </button>
          </div>
          {clipMsg && (
            <div className="mt-3 rounded-lg bg-gray-50 px-4 py-3 text-sm text-gray-700">{clipMsg}</div>
          )}
        </section>
      </main>
    </div>
  );
}
