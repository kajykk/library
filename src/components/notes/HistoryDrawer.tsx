'use client';

import { useEffect, useState } from 'react';
import { RotateCcw, X } from 'lucide-react';
import {
  ApiDocument,
  DocumentVersionDetail,
  DocumentVersionSummary,
  getVersion,
  getVersions,
  restoreVersion,
} from '@/lib/api/client';

interface HistoryDrawerProps {
  docId: string;
  onClose: () => void;
  /** 恢复成功后回调：由父组件把标题/正文/编辑器内容切到该版本 */
  onRestored: (version: ApiDocument) => void;
}

/** 版本历史抽屉 + 版本预览/恢复弹窗 */
export default function HistoryDrawer({ docId, onClose, onRestored }: HistoryDrawerProps) {
  const [versions, setVersions] = useState<DocumentVersionSummary[]>([]);
  const [viewingVersion, setViewingVersion] = useState<DocumentVersionDetail | null>(null);

  useEffect(() => {
    let cancelled = false;
    getVersions(docId)
      .then((items) => {
        if (!cancelled) setVersions(items);
      })
      .catch((err) => console.error('加载版本历史失败:', err));
    return () => {
      cancelled = true;
    };
  }, [docId]);

  const openVersionDetail = async (versionId: string) => {
    try {
      setViewingVersion(await getVersion(docId, versionId));
    } catch (err) {
      alert('加载版本失败：' + (err as Error).message);
    }
  };

  const handleRestore = async () => {
    if (!viewingVersion) return;
    if (!confirm('将文档内容恢复到此版本？恢复前会自动保存当前版本。')) return;
    try {
      const restored = await restoreVersion(docId, viewingVersion.id);
      setViewingVersion(null);
      setVersions(await getVersions(docId));
      onRestored(restored);
    } catch (err) {
      alert('恢复失败：' + (err as Error).message);
    }
  };

  return (
    <>
      {/* 版本历史抽屉 */}
      <div className="fixed inset-0 z-50">
        <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} />
        <aside className="absolute right-0 top-0 h-full w-80 bg-white shadow-2xl flex flex-col">
          <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
            <h3 className="text-sm font-medium text-gray-900">版本历史（{versions.length}）</h3>
            <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded">
              <X className="w-4 h-4 text-gray-500" />
            </button>
          </div>
          <div className="flex-1 overflow-y-auto p-2 space-y-1">
            {versions.length === 0 ? (
              <p className="text-center text-sm text-gray-400 py-8">暂无历史版本</p>
            ) : (
              versions.map((v) => (
                <button
                  key={v.id}
                  onClick={() => openVersionDetail(v.id)}
                  className="block w-full text-left px-3 py-2 rounded-lg hover:bg-gray-50 transition-colors"
                >
                  <p className="text-xs text-gray-500">
                    {new Date(v.created_at).toLocaleString('zh-CN')}
                  </p>
                  <p className="text-sm text-gray-700 truncate">{v.title}</p>
                  <p className="text-xs text-gray-400 truncate">{v.preview || '（空）'}</p>
                </button>
              ))
            )}
          </div>
        </aside>
      </div>

      {/* 版本预览/恢复弹窗 */}
      {viewingVersion && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setViewingVersion(null)} />
          <div className="relative w-full max-w-2xl max-h-[80vh] bg-white rounded-xl shadow-2xl flex flex-col">
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
              <div>
                <h3 className="text-sm font-medium text-gray-900">{viewingVersion.title}</h3>
                <p className="text-xs text-gray-400">
                  {new Date(viewingVersion.created_at).toLocaleString('zh-CN')}
                </p>
              </div>
              <button onClick={() => setViewingVersion(null)} className="p-1 hover:bg-gray-100 rounded">
                <X className="w-4 h-4 text-gray-500" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-4">
              <pre className="whitespace-pre-wrap font-sans text-sm text-gray-700 leading-relaxed">
                {viewingVersion.content || '（空）'}
              </pre>
            </div>
            <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-200">
              <button
                onClick={() => setViewingVersion(null)}
                className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
              >
                关闭
              </button>
              <button
                onClick={handleRestore}
                className="flex items-center gap-1 px-3 py-1.5 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                恢复此版本
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
