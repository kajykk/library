'use client';

import { useEffect, useState } from 'react';
import { RotateCcw } from 'lucide-react';
import {
  ApiDocument,
  DocumentVersionDetail,
  DocumentVersionSummary,
  getVersion,
  getVersions,
  restoreVersion,
} from '@/lib/api/client';
import { Drawer, DrawerHeader, Modal as UiModal, useDialog, useToast } from '@/components/ui';

interface HistoryDrawerProps {
  docId: string;
  onClose: () => void;
  /** 恢复成功后回调：由父组件把标题/正文/编辑器内容切到该版本 */
  onRestored: (version: ApiDocument) => void;
  /** 恢复前回调（父组件应先 flush 在途自动保存）：
   * 否则在途的旧内容 PATCH 可能晚于恢复落库，把刚恢复的版本再次覆盖。 */
  beforeRestore?: () => Promise<void>;
}

/** 版本历史抽屉 + 版本预览/恢复弹窗 */
export default function HistoryDrawer({ docId, onClose, onRestored, beforeRestore }: HistoryDrawerProps) {
  const [versions, setVersions] = useState<DocumentVersionSummary[]>([]);
  const [viewingVersion, setViewingVersion] = useState<DocumentVersionDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const dialog = useDialog();
  const toast = useToast();

  useEffect(() => {
    let cancelled = false;
    setLoadError(null);
    getVersions(docId)
      .then((items) => {
        if (!cancelled) setVersions(items);
      })
      .catch((err) => {
        console.error('加载版本历史失败:', err);
        if (!cancelled) setLoadError((err as Error).message);
      });
    return () => {
      cancelled = true;
    };
  }, [docId]);

  const openVersionDetail = async (versionId: string) => {
    try {
      setViewingVersion(await getVersion(docId, versionId));
    } catch (err) {
      toast.error('加载版本失败：' + (err as Error).message);
    }
  };

  const handleRestore = async () => {
    if (!viewingVersion) return;
    const ok = await dialog.confirm({
      title: '恢复此版本？',
      message: '将文档内容恢复到此版本，恢复前会自动保存当前版本。',
      confirmText: '恢复',
    });
    if (!ok) return;
    try {
      // 先等在途/待触发的自动保存落地，再执行恢复，
      // 防止旧内容的保存请求后到、覆盖刚恢复的版本
      await beforeRestore?.();
      const restored = await restoreVersion(docId, viewingVersion.id);
      setViewingVersion(null);
      setVersions(await getVersions(docId));
      onRestored(restored);
      toast.success('已恢复到所选版本');
    } catch (err) {
      toast.error('恢复失败：' + (err as Error).message);
    }
  };

  return (
    <>
      {/* 版本历史抽屉 */}
      <Drawer open onClose={onClose} ariaLabel="版本历史">
        <DrawerHeader title={`版本历史（${versions.length}）`} onClose={onClose} />
        <div className="flex-1 overflow-y-auto p-2 space-y-1">
          {loadError ? (
            <p className="text-center text-sm text-red-400 py-8">加载失败：{loadError}</p>
          ) : versions.length === 0 ? (
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
      </Drawer>

      {/* 版本预览/恢复弹窗（层级高于抽屉） */}
      <UiModal         open={viewingVersion !== null}
        onClose={() => setViewingVersion(null)}
        ariaLabel="版本预览"
        panelClassName="max-h-[80vh] w-full max-w-2xl"
      >
        {viewingVersion && (
          <>
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
              <div>
                <h3 className="text-sm font-medium text-gray-900">{viewingVersion.title}</h3>
                <p className="text-xs text-gray-400">
                  {new Date(viewingVersion.created_at).toLocaleString('zh-CN')}
                </p>
              </div>
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
          </>
        )}
      </UiModal>
    </>
  );
}
