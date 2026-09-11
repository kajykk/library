'use client';

/**
 * 每日回顾（对标 Readwise 高亮回顾）：
 * 卡片轮换展示全部标注，支持跳回原文与一键存入笔记。
 * 数据走 /api/annotations/review（跨书籍标注流）。
 */

import { Suspense, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  BookOpen,
  Bookmark as BookmarkIcon,
  ChevronLeft,
  ChevronRight,
  FilePlus2,
  Highlighter,
  Loader2,
  RefreshCw,
  StickyNote,
} from 'lucide-react';
import {
  AnnotationHit,
  downloadAnnotationsExport,
  downloadAnnotationsTsv,
  reviewAnnotations,
} from '@/lib/api/client';
import { convertAnnotationToNote } from '@/lib/dataSource';
import { useToast } from '@/components/ui';

type ReviewFilter = 'all' | 'highlight' | 'note' | 'bookmark';

const TYPE_META: Record<string, { label: string; icon: typeof Highlighter; color: string }> = {
  highlight: { label: '划线', icon: Highlighter, color: 'text-yellow-500' },
  note: { label: '批注', icon: StickyNote, color: 'text-blue-500' },
  bookmark: { label: '书签', icon: BookmarkIcon, color: 'text-primary-500' },
};

function typeMeta(type: string): { label: string; icon: typeof Highlighter; color: string } {
  return TYPE_META[type] ?? { label: type, icon: StickyNote, color: 'text-gray-400' };
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

export default function ReviewPage() {
  return (
    <Suspense fallback={<main className="max-w-3xl mx-auto px-4 py-16 text-center text-gray-400">加载中...</main>}>
      <ReviewPageInner />
    </Suspense>
  );
}

function ReviewPageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const toast = useToast();

  const [filter, setFilter] = useState<ReviewFilter>('all');
  const [onlyToday, setOnlyToday] = useState(false);
  const [cursor, setCursor] = useState(0);
  const [convertedNotes, setConvertedNotes] = useState<Record<string, string>>({});
  const [converting, setConverting] = useState<string | null>(null);

  const reviewQuery = useQuery({
    queryKey: ['annotations', 'review'],
    queryFn: () => reviewAnnotations({ limit: 200 }),
  });
  const allHits = useMemo(() => reviewQuery.data ?? [], [reviewQuery.data]);
  const isLoading = reviewQuery.isLoading;

  const hits = useMemo(() => {
    if (filter === 'all') return allHits;
    return allHits.filter((h) => h.type === filter);
  }, [allHits, filter]);

  const todayCount = useMemo(() => {
    const todayPrefix = todayIso();
    return allHits.filter((h) => h.created_at.slice(0, 10) === todayPrefix).length;
  }, [allHits]);

  // 过滤条件变化时游标越界重置
  useEffect(() => {
    setCursor(0);
  }, [filter, onlyToday]);

  const visible = useMemo(() => {
    if (!onlyToday) return hits;
    const todayPrefix = todayIso();
    return hits.filter((h) => h.created_at.slice(0, 10) === todayPrefix);
  }, [hits, onlyToday]);

  const current: AnnotationHit | null =
    visible.length > 0 ? visible[Math.min(cursor, visible.length - 1)] ?? null : null;

  const anchorPos = current?.anchor
    ? (current.anchor as { chapter?: number }).chapter ?? (current.anchor as { page?: number }).page
    : undefined;

  const handleConvert = async (hit: AnnotationHit) => {
    setConverting(hit.id);
    try {
      const { noteId } = await convertAnnotationToNote(hit.id);
      setConvertedNotes((prev) => ({ ...prev, [hit.id]: noteId }));
      toast.success('已存入笔记，可在笔记页查看');
    } catch (err) {
      toast.error('存入笔记失败：' + (err as Error).message);
    } finally {
      setConverting(null);
    }
  };

  const jumpToSource = (hit: AnnotationHit) => {
    const pos = hit.anchor
      ? (hit.anchor as { chapter?: number }).chapter ?? (hit.anchor as { page?: number }).page
      : undefined;
    router.push(pos !== undefined ? `/reader/${hit.document_id}?position=${pos}` : `/reader/${hit.document_id}`);
  };

  // 左右方向键翻卡
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'ArrowLeft') setCursor((c) => Math.max(0, c - 1));
      else if (e.key === 'ArrowRight') setCursor((c) => Math.min(visible.length - 1, c + 1));
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [visible.length]);

  const openId = searchParams.get('open');

  // 支持 /review?open=<annotationId>：命令面板直达某条标注时定位该卡
  useEffect(() => {
    if (!openId) return;
    const idx = allHits.findIndex((h) => h.id === openId);
    if (idx >= 0) setCursor(idx);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [openId, allHits.length]);

  return (
    <main className="max-w-3xl mx-auto px-4 sm:px-6 py-6">
      <div className="flex items-center justify-between gap-3 flex-wrap mb-4">
        <div>
          <h1 className="text-lg font-semibold text-gray-900">每日回顾</h1>
          <p className="text-sm text-gray-500 mt-0.5">
            重温划线与批注，让读过的书持续产生价值
            {todayCount > 0 ? ` · 今天新增 ${todayCount} 条` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => {
              reviewQuery.refetch();
              queryClient.invalidateQueries({ queryKey: ['annotations'] });
              toast.success('已刷新');
            }}
            className="p-2 border border-gray-200 bg-white rounded-lg hover:bg-gray-50 transition-colors"
            title="刷新"
          >
            <RefreshCw className="w-4 h-4 text-gray-500" />
          </button>
          <button
            onClick={() =>
              downloadAnnotationsExport()
                .then(() => toast.success('标注 Markdown 已下载'))
                .catch((e) => toast.error('导出失败：' + (e as Error).message))
            }
            className="px-3 py-2 text-sm border border-gray-200 bg-white rounded-lg hover:bg-gray-50 transition-colors"
            title="按书分组导出全部标注（zip）"
          >
            导出 MD
          </button>
          <button
            onClick={() =>
              downloadAnnotationsTsv()
                .then(() => toast.success('标注 TSV 已下载'))
                .catch((e) => toast.error('导出失败：' + (e as Error).message))
            }
            className="px-3 py-2 text-sm border border-gray-200 bg-white rounded-lg hover:bg-gray-50 transition-colors"
            title="导出 TSV（可导入 Anki/表格）"
          >
            导出 TSV
          </button>
        </div>
      </div>

      <div className="flex items-center gap-2 flex-wrap mb-4">
        {([['all', '全部'], ['highlight', '划线'], ['note', '批注'], ['bookmark', '书签']] as const).map(
          ([value, label]) => (
            <button
              key={value}
              onClick={() => setFilter(value)}
              className={`px-3 py-1 rounded-full text-xs transition-colors ${
                filter === value
                  ? 'bg-primary-600 text-white'
                  : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
              }`}
            >
              {label}
            </button>
          ),
        )}
        <button
          onClick={() => setOnlyToday((v) => !v)}
          className={`px-3 py-1 rounded-full text-xs transition-colors ${
            onlyToday
              ? 'bg-emerald-600 text-white'
              : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
          }`}
        >
          仅今天
        </button>
      </div>

      {isLoading ? (
        <div className="flex items-center justify-center gap-2 py-16 text-gray-400">
          <Loader2 className="w-5 h-5 animate-spin" /> 加载中...
        </div>
      ) : !current ? (
        <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
          <Highlighter className="w-12 h-12 text-gray-300 mx-auto mb-4" />
          <p className="text-gray-500">{onlyToday ? '今天还没有新的标注' : '还没有任何标注'}</p>
          <p className="text-sm text-gray-400 mt-1">打开一本书，选中正文即可划线与批注</p>
          <Link
            href="/library"
            className="inline-flex items-center gap-2 mt-6 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors text-sm"
          >
            <BookOpen className="w-4 h-4" />
            去书架
          </Link>
        </div>
      ) : (
        <>
          <div className="relative bg-white rounded-2xl border border-gray-200 shadow-sm p-8 min-h-[280px] flex flex-col">
            <div className="flex items-center gap-2 mb-4">
              {(() => {
                const meta = typeMeta(current.type);
                const Icon = meta.icon;
                return (
                  <>
                    <Icon className={`w-4 h-4 ${meta.color}`} />
                    <span className="text-xs text-gray-400">{meta.label}</span>
                  </>
                );
              })()}
              <span className="text-xs text-gray-400 ml-auto">
                {new Date(current.created_at).toLocaleString('zh-CN')}
              </span>
            </div>

            {current.quote ? (
              <blockquote className="border-l-4 border-yellow-300 pl-4 text-lg leading-relaxed text-gray-800 italic">
                {current.quote}
              </blockquote>
            ) : null}
            {current.content ? (
              <p className={`text-base text-gray-700 leading-relaxed ${current.quote ? 'mt-4' : ''}`}>
                {current.content}
              </p>
            ) : null}

            <div className="mt-auto pt-6 flex items-center justify-between gap-3 flex-wrap">
              <button
                onClick={() => jumpToSource(current)}
                className="flex items-center gap-1.5 text-sm text-primary-600 hover:underline"
                title="回到书中标注位置"
              >
                <BookOpen className="w-4 h-4" />
                {`《${current.document_title}》`}
              </button>
              <div className="flex items-center gap-2">
                {convertedNotes[current.id] ? (
                  <button
                    onClick={() => router.push(`/notes?open=${convertedNotes[current.id]}`)}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-emerald-700 border border-emerald-200 bg-emerald-50 rounded-lg hover:bg-emerald-100 transition-colors"
                  >
                    <FilePlus2 className="w-3.5 h-3.5" />
                    查看笔记
                  </button>
                ) : (
                  <button
                    onClick={() => void handleConvert(current)}
                    disabled={converting === current.id}
                    className="flex items-center gap-1.5 px-3 py-1.5 text-sm text-gray-700 border border-gray-200 rounded-lg hover:bg-gray-50 transition-colors disabled:opacity-60"
                  >
                    {converting === current.id ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <FilePlus2 className="w-3.5 h-3.5" />
                    )}
                    存入笔记
                  </button>
                )}
                {anchorPos !== undefined && (
                  <button
                    onClick={() => jumpToSource(current)}
                    className="px-3 py-1.5 text-sm text-primary-600 border border-primary-200 rounded-lg hover:bg-primary-50 transition-colors"
                  >
                    跳回原文
                  </button>
                )}
              </div>
            </div>
          </div>

          <div className="flex items-center justify-center gap-4 mt-4">
            <button
              onClick={() => setCursor((c) => Math.max(0, c - 1))}
              disabled={cursor === 0}
              className="p-2 rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              aria-label="上一条"
            >
              <ChevronLeft className="w-5 h-5 text-gray-500" />
            </button>
            <span className="text-sm text-gray-400">
              {Math.min(cursor, visible.length - 1) + 1} / {visible.length}
            </span>
            <button
              onClick={() => setCursor((c) => Math.min(visible.length - 1, c + 1))}
              disabled={cursor >= visible.length - 1}
              className="p-2 rounded-lg hover:bg-gray-100 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              aria-label="下一条"
            >
              <ChevronRight className="w-5 h-5 text-gray-500" />
            </button>
          </div>
          <p className="mt-2 text-center text-xs text-gray-300 select-none">← → 切换卡片</p>
        </>
      )}
    </main>
  );
}
