'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { BookOpen, Loader2, X } from 'lucide-react';
import { AnnotationHit, searchAnnotations } from '@/lib/api/client';

interface CitationDialogProps {
  onClose: () => void;
  onInsert: (hit: AnnotationHit) => void;
}

/** 书籍引用弹窗：搜索标注并插入引用，防抖 300ms 后发起请求 */
export default function CitationDialog({ onClose, onInsert }: CitationDialogProps) {
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [hits, setHits] = useState<AnnotationHit[]>([]);
  const [loading, setLoading] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 防抖搜索：输入停顿 300ms 后再请求（打开弹窗时同样走一次防抖）
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(async () => {
      setLoading(true);
      try {
        setHits(await searchAnnotations(query));
      } catch {
        setHits([]);
      } finally {
        setLoading(false);
      }
    }, 300);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [query]);

  return (
    <div className="fixed inset-0 z-[60] flex items-start justify-center pt-[12vh] p-4">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} />
      <div className="relative w-full max-w-xl bg-white rounded-xl shadow-2xl flex flex-col max-h-[70vh]">
        <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
          <h3 className="text-sm font-medium text-gray-900">插入书籍标注引用</h3>
          <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded">
            <X className="w-4 h-4 text-gray-500" />
          </button>
        </div>
        <div className="px-5 py-3 border-b border-gray-100">
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索划线或批注内容…"
            autoFocus
            className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none"
          />
        </div>
        <div className="flex-1 overflow-y-auto p-2">
          {loading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-gray-400">
              <Loader2 className="w-4 h-4 animate-spin" /> 搜索中...
            </div>
          ) : hits.length === 0 ? (
            <p className="text-center text-sm text-gray-400 py-8">没有找到匹配的标注</p>
          ) : (
            hits.map((hit) => {
              const anchorPos = hit.anchor
                ? (hit.anchor as { chapter?: number; page?: number }).chapter ??
                  (hit.anchor as { page?: number }).page
                : undefined;
              return (
                <div
                  key={hit.id}
                  className="flex items-start gap-2 px-3 py-2.5 rounded-lg hover:bg-primary-50 transition-colors"
                >
                  <button
                    onClick={() => onInsert(hit)}
                    className="block flex-1 min-w-0 text-left"
                  >
                    <p className="text-sm text-gray-800 line-clamp-2">
                      {hit.quote || hit.content || '（无内容）'}
                    </p>
                    <p className="text-xs text-gray-400 mt-1">
                      《{hit.document_title}》 · {hit.type === 'highlight' ? '划线' : hit.type === 'note' ? '批注' : '书签'}
                    </p>
                  </button>
                  {anchorPos !== undefined && hit.document_type !== 'note' && (
                    <button
                      onClick={() => router.push(`/reader/${hit.document_id}?position=${anchorPos}`)}
                      className="shrink-0 self-center flex items-center gap-1 px-2 py-1 text-xs text-primary-600 border border-primary-200 rounded-md hover:bg-primary-50 transition-colors"
                      title="跳转到书中标注位置"
                    >
                      <BookOpen className="w-3 h-3" />
                      跳转
                    </button>
                  )}
                </div>
              );
            })
          )}
        </div>
        <div className="px-5 py-2.5 border-t border-gray-100 text-xs text-gray-400">
          插入引用后将自动建立笔记与书籍的「引用」连接
        </div>
      </div>
    </div>
  );
}
