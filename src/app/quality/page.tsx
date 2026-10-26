'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import {
  ArrowLeft, RefreshCw, FileWarning, FileX, BookOpen, FolderOpen,
  ScanLine, Copy, Globe, Loader2, ChevronLeft, ChevronRight, ExternalLink,
} from 'lucide-react';
import {
  getQualitySummary, listQualityIssues, startQualityAudit,
  getQualityAuditStatus, getQualityOrphans,
  type QualitySummary, type QualityIssueItem, type QualityAuditStatus, type QualityOrphans,
  type QualityIssueCode,
} from '@/lib/api/client';
import { useDataSourceStatus } from '@/lib/useDataSourceStatus';
import StatePanel from '@/components/ui/StatePanel';

const ISSUE_CARDS: Array<{ key: string; label: string; icon: typeof FileWarning; color: string }> = [
  { key: 'empty_content', label: '正文为空', icon: FileX, color: 'text-red-500' },
  { key: 'short_content', label: '正文过短', icon: FileWarning, color: 'text-amber-500' },
  { key: 'no_cover', label: '无封面', icon: BookOpen, color: 'text-blue-500' },
  { key: 'unclassified', label: '未分类', icon: FolderOpen, color: 'text-gray-500' },
  { key: 'needs_ocr', label: '需 OCR', icon: ScanLine, color: 'text-purple-500' },
  { key: 'missing_file', label: '文件丢失', icon: FileX, color: 'text-red-600' },
  { key: 'duplicate', label: '重复', icon: Copy, color: 'text-orange-500' },
  { key: 'language_variant', label: '语言变体', icon: Globe, color: 'text-teal-500' },
];

export default function QualityPage() {
  const router = useRouter();
  const { status: dsStatus } = useDataSourceStatus();
  const isLocal = dsStatus && dsStatus.mode !== 'api';

  const [summary, setSummary] = useState<QualitySummary | null>(null);
  const [auditStatus, setAuditStatus] = useState<QualityAuditStatus | null>(null);
  const [orphans, setOrphans] = useState<QualityOrphans | null>(null);
  const [selectedIssue, setSelectedIssue] = useState<string>('all');
  const [issues, setIssues] = useState<{ total: number; items: QualityIssueItem[] } | null>(null);
  const [page, setPage] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const PAGE_SIZE = 20;

  const loadData = async () => {
    try {
      setLoading(true);
      setError(null);
      const [s, a, o] = await Promise.all([
        getQualitySummary(),
        getQualityAuditStatus(),
        getQualityOrphans(),
      ]);
      setSummary(s);
      setAuditStatus(a);
      setOrphans(o);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  };

  const loadIssues = async () => {
    try {
      const result = await listQualityIssues(
        selectedIssue as QualityIssueCode | 'all',
        PAGE_SIZE,
        page * PAGE_SIZE,
      );
      setIssues(result);
    } catch {
      /* 保持上一次结果 */
    }
  };

  useEffect(() => {
    if (!isLocal) void loadData();
  }, [isLocal]);

  useEffect(() => {
    if (!isLocal) void loadIssues();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIssue, page, isLocal]);

  // 审计运行中每 2 秒轮询
  useEffect(() => {
    if (!auditStatus?.running) return;
    const timer = setInterval(async () => {
      try {
        const a = await getQualityAuditStatus();
        setAuditStatus(a);
        if (!a.running) {
          const s = await getQualitySummary();
          setSummary(s);
        }
      } catch { /* 忽略轮询错误 */ }
    }, 2000);
    return () => clearInterval(timer);
  }, [auditStatus?.running]);

  const handleStartAudit = async () => {
    try {
      await startQualityAudit();
      const a = await getQualityAuditStatus();
      setAuditStatus(a);
    } catch (err) {
      setError((err as Error).message);
    }
  };

  if (isLocal) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <StatePanel
          variant="empty"
          title="质量中心需要连接后端"
          description="质量审计依赖后端数据库，本地模式暂不支持。"
          action={{ label: '去设置', onClick: () => router.push('/settings') }}
        />
      </div>
    );
  }

  if (loading && !summary) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <StatePanel variant="loading" title="加载质量数据..." />
      </div>
    );
  }

  if (error && !summary) {
    return (
      <div className="min-h-screen bg-gray-50 flex items-center justify-center">
        <StatePanel variant="error" title="质量数据加载失败" description={error} action={{ label: '重试', onClick: loadData }} />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <header className="bg-white shadow-sm border-b border-gray-200 sticky top-0 z-10">
        <div className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 flex items-center justify-between h-16">
          <div className="flex items-center gap-3">
            <Link href="/library" className="p-2 hover:bg-gray-100 rounded-lg transition-colors">
              <ArrowLeft className="w-5 h-5" />
            </Link>
            <h1 className="text-xl font-bold text-gray-900">质量中心</h1>
            {summary?.stale && (
              <span className="px-2 py-1 text-xs bg-amber-100 text-amber-700 rounded-full">数据已变更</span>
            )}
          </div>
          <button
            onClick={handleStartAudit}
            disabled={auditStatus?.running}
            className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 disabled:opacity-50 transition-colors"
          >
            <RefreshCw className={`w-4 h-4 ${auditStatus?.running ? 'animate-spin' : ''}`} />
            {auditStatus?.running ? '审计中...' : '重新审计'}
          </button>
        </div>
      </header>

      <main className="max-w-6xl mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">
        {/* 审计进度 */}
        {auditStatus?.running && (
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <div className="flex items-center justify-between text-sm text-gray-600 mb-2">
              <span className="flex items-center gap-1"><Loader2 className="w-4 h-4 animate-spin" />正在审计...</span>
              <span>{auditStatus.done} / {auditStatus.total || '?'}</span>
            </div>
            <div className="h-2 bg-gray-200 rounded-full overflow-hidden">
              <div
                className="h-full bg-primary-500 rounded-full transition-all"
                style={{ width: `${auditStatus.total ? Math.min(100, (auditStatus.done / auditStatus.total) * 100) : 0}%` }}
              />
            </div>
          </div>
        )}

        {/* 配置阈值说明 */}
        {summary && (
          <p className="text-sm text-gray-500">
            正文少于 <strong>{summary.min_content_chars}</strong> 字视为过短。
            {summary.last_audited_at && ` 最后审计：${new Date(summary.last_audited_at).toLocaleString('zh-CN')}`}
          </p>
        )}

        {/* 汇总卡 */}
        {summary && (
          <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
            {ISSUE_CARDS.map(({ key, label, icon: Icon, color }) => (
              <button
                key={key}
                type="button"
                onClick={() => { setSelectedIssue(key); setPage(0); }}
                className={`bg-white rounded-lg border p-4 text-left transition-colors hover:border-primary-300 ${
                  selectedIssue === key ? 'border-primary-500 ring-1 ring-primary-200' : 'border-gray-200'
                }`}
              >
                <div className="flex items-center gap-2 mb-1">
                  <Icon className={`w-4 h-4 ${color}`} />
                  <span className="text-xs text-gray-500">{label}</span>
                </div>
                <p className="text-2xl font-bold text-gray-900">{summary.counts[key] ?? 0}</p>
              </button>
            ))}
          </div>
        )}

        {/* Issue 表格 */}
        <div className="bg-white rounded-lg border border-gray-200 overflow-hidden">
          <div className="px-4 py-3 border-b border-gray-200 flex items-center justify-between">
            <h2 className="font-semibold text-gray-900">
              {ISSUE_CARDS.find(c => c.key === selectedIssue)?.label || '全部问题'}
              {issues && <span className="ml-2 text-sm font-normal text-gray-500">共 {issues.total} 条</span>}
            </h2>
            {selectedIssue !== 'all' && (
              <button
                type="button"
                onClick={() => { setSelectedIssue('all'); setPage(0); }}
                className="text-sm text-primary-600 hover:text-primary-700"
              >
                查看全部
              </button>
            )}
          </div>

          {issues && issues.items.length > 0 ? (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-2 text-left text-gray-500">书名</th>
                    <th className="px-4 py-2 text-left text-gray-500">类型</th>
                    <th className="px-4 py-2 text-left text-gray-500">字数</th>
                    <th className="px-4 py-2 text-left text-gray-500">问题</th>
                    <th className="px-4 py-2 text-left text-gray-500">操作</th>
                  </tr>
                </thead>
                <tbody>
                  {issues.items.map((item) => (
                    <tr key={item.document_id} className="border-t border-gray-100">
                      <td className="px-4 py-2 max-w-xs truncate">{item.title}</td>
                      <td className="px-4 py-2 text-gray-500">{item.format || item.type}</td>
                      <td className="px-4 py-2 text-gray-500">{item.content_chars}</td>
                      <td className="px-4 py-2">
                        <div className="flex flex-wrap gap-1">
                          {item.issues.map((iss) => (
                            <span key={iss} className="px-1.5 py-0.5 text-xs bg-red-50 text-red-600 rounded">{iss}</span>
                          ))}
                        </div>
                      </td>
                      <td className="px-4 py-2">
                        <div className="flex items-center gap-2">
                          <Link href={`/library?quality=${selectedIssue}`} className="text-xs text-primary-600 hover:text-primary-700">
                            在书架查看
                          </Link>
                          {(item.type === 'book' || item.type === 'pdf') && (
                            <Link href={`/reader/${item.document_id}`} className="text-xs text-gray-500 hover:text-gray-700 flex items-center gap-0.5">
                              <ExternalLink className="w-3 h-3" />打开
                            </Link>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="px-4 py-8 text-center text-gray-400 text-sm">
              {issues ? '没有符合条件的文档' : '加载中...'}
            </div>
          )}

          {/* 分页 */}
          {issues && issues.total > PAGE_SIZE && (
            <div className="px-4 py-3 border-t border-gray-200 flex items-center justify-between">
              <span className="text-sm text-gray-500">
                第 {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, issues.total)} 条，共 {issues.total} 条
              </span>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setPage(Math.max(0, page - 1))}
                  disabled={page === 0}
                  className="p-1.5 rounded hover:bg-gray-100 disabled:opacity-50"
                >
                  <ChevronLeft className="w-4 h-4" />
                </button>
                <button
                  type="button"
                  onClick={() => setPage(page + 1)}
                  disabled={(page + 1) * PAGE_SIZE >= issues.total}
                  className="p-1.5 rounded hover:bg-gray-100 disabled:opacity-50"
                >
                  <ChevronRight className="w-4 h-4" />
                </button>
              </div>
            </div>
          )}
        </div>

        {/* 孤儿文件区（只读） */}
        {orphans && (orphans.orphan_files.length > 0 || orphans.orphan_covers.length > 0) && (
          <div className="bg-white rounded-lg border border-gray-200 p-4">
            <h2 className="font-semibold text-gray-900 mb-3">孤儿文件</h2>
            <p className="text-sm text-gray-500 mb-3">
              共 {orphans.orphan_files.length + orphans.orphan_covers.length} 个文件，
              总计 {(orphans.total_bytes / 1024 / 1024).toFixed(1)} MB。
              隔离操作将在 B2 批次提供。
            </p>
            {orphans.orphan_files.length > 0 && (
              <div className="mb-2">
                <p className="text-xs text-gray-400 mb-1">孤儿书籍文件：</p>
                <div className="space-y-1 max-h-32 overflow-y-auto">
                  {orphans.orphan_files.slice(0, 10).map((f) => (
                    <div key={f.rel_path} className="text-xs font-mono text-gray-500">{f.rel_path} ({(f.size / 1024).toFixed(0)} KB)</div>
                  ))}
                </div>
              </div>
            )}
            {orphans.missing_files.length > 0 && (
              <div>
                <p className="text-xs text-gray-400 mb-1">缺失文件：</p>
                <div className="space-y-1 max-h-32 overflow-y-auto">
                  {orphans.missing_files.slice(0, 10).map((f) => (
                    <div key={f.document_id} className="text-xs text-gray-500">{f.title} → {f.rel_path}</div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </main>
    </div>
  );
}
