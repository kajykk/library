'use client';

import { Suspense, useEffect, useMemo, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import {
  AlertCircle,
  ArrowDown,
  ArrowLeft,
  ArrowRight,
  ArrowUp,
  Check,
  ExternalLink,
  Pause,
  Play,
  RotateCcw,
  Trash2,
} from 'lucide-react';
import BookCover from '@/components/BookCover';
import StatePanel from '@/components/ui/StatePanel';
import { useDialog, useToast } from '@/components/ui';
import { useDataSourceStatus } from '@/lib/useDataSourceStatus';
import {
  getQueueNext,
  listQueue,
  removeQueueItem,
  reorderQueue,
  updateQueueItem,
  type QueueItem,
  type QueueStatus,
} from '@/lib/api/queue';
import {
  getDueReviews,
  getReviewStats,
  rateReview,
  snoozeReview,
  type ReviewCard,
  type ReviewRating,
} from '@/lib/api/reviews';

const QUEUE_QUERY_KEY = ['queue'] as const;
const REVIEWS_DUE_KEY = ['reviews', 'due'] as const;
const REVIEWS_STATS_KEY = ['reviews', 'stats'] as const;
const DONE_VISIBLE_LIMIT = 20;

type Tab = 'queue' | 'review';

const RATING_BUTTONS: Array<{ rating: ReviewRating; key: string; label: string; className: string }> = [
  { rating: 'again', key: '1', label: '忘记', className: 'bg-red-600 hover:bg-red-700' },
  { rating: 'hard', key: '2', label: '困难', className: 'bg-amber-600 hover:bg-amber-700' },
  { rating: 'good', key: '3', label: '良好', className: 'bg-emerald-600 hover:bg-emerald-700' },
  { rating: 'easy', key: '4', label: '简单', className: 'bg-sky-600 hover:bg-sky-700' },
];

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '未知错误';
}

export default function QueuePage() {
  return (
    <Suspense fallback={<div className="min-h-screen bg-gray-50 p-16 text-center text-gray-400">加载中...</div>}>
      <QueuePageInner />
    </Suspense>
  );
}

function QueuePageInner() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const { status: dsStatus } = useDataSourceStatus();
  const isApiMode = dsStatus?.mode === 'api';
  const tab: Tab = searchParams.get('tab') === 'review' ? 'review' : 'queue';

  const setTab = (next: Tab) => {
    router.replace(next === 'review' ? '/queue?tab=review' : '/queue', { scroll: false });
  };

  return (
    <div className="min-h-screen bg-gray-50">
      <div className="max-w-5xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
          <div>
            <h1 className="text-xl font-bold text-gray-900">阅读队列与复习</h1>
            <p className="text-sm text-gray-500 mt-1">队列按顺序推进阅读，间隔复习帮你把划线变成长期记忆。</p>
          </div>
          <div className="inline-flex rounded-lg border border-gray-200 bg-white p-1" role="tablist" aria-label="队列与复习">
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'queue'}
              onClick={() => setTab('queue')}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                tab === 'queue' ? 'bg-primary-50 text-primary-700' : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              阅读队列
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={tab === 'review'}
              onClick={() => setTab('review')}
              className={`px-3 py-1.5 text-sm rounded-md transition-colors ${
                tab === 'review' ? 'bg-primary-50 text-primary-700' : 'text-gray-600 hover:bg-gray-50'
              }`}
            >
              间隔复习
            </button>
          </div>
        </div>

        {!isApiMode ? (
          <StatePanel
            variant="empty"
            title="阅读队列需要连接后端"
            description="队列与间隔复习依赖服务端调度；本地模式下不会发送任何请求。"
            action={{ label: '去设置', onClick: () => router.push('/settings') }}
          />
        ) : tab === 'queue' ? (
          <QueueTab />
        ) : (
          <ReviewTab />
        )}
      </div>
    </div>
  );
}

function QueueTab() {
  const router = useRouter();
  const toast = useToast();
  const dialog = useDialog();
  const queryClient = useQueryClient();

  const queueQuery = useQuery({
    queryKey: [...QUEUE_QUERY_KEY, 'all'],
    queryFn: () => listQueue({ status: 'all', limit: 200 }),
    staleTime: 15_000,
  });
  const nextQuery = useQuery({
    queryKey: [...QUEUE_QUERY_KEY, 'next'],
    queryFn: getQueueNext,
    staleTime: 15_000,
  });

  const invalidateQueue = () => queryClient.invalidateQueries({ queryKey: QUEUE_QUERY_KEY });

  const statusMutation = useMutation({
    mutationFn: ({ id, status }: { id: string; status: QueueStatus }) =>
      updateQueueItem(id, { status }),
    onSuccess: () => void invalidateQueue(),
    onError: (error) => toast.error(`更新队列失败：${errorMessage(error)}`),
  });

  const removeMutation = useMutation({
    mutationFn: (id: string) => removeQueueItem(id),
    onSuccess: () => void invalidateQueue(),
    onError: (error) => toast.error(`移出队列失败：${errorMessage(error)}`),
  });

  const reorderMutation = useMutation({
    mutationFn: (ids: string[]) => reorderQueue(ids),
    onSuccess: () => void invalidateQueue(),
    onError: (error) => {
      toast.error(`调整顺序失败：${errorMessage(error)}`);
      void invalidateQueue();
    },
  });

  const items = queueQuery.data?.items ?? [];
  const active = items.filter((item) => item.status === 'active');
  const paused = items.filter((item) => item.status === 'paused');
  const done = items.filter((item) => item.status === 'done').slice(0, DONE_VISIBLE_LIMIT);
  const hasMixedPriority = new Set(active.map((item) => item.priority)).size > 1;

  // 服务端按 priority、sort_order 升序：跨 priority 交换 sort_order 不会改变可见顺序
  const canMove = (item: QueueItem, direction: -1 | 1) => {
    const index = active.findIndex((candidate) => candidate.id === item.id);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= active.length) return false;
    const current = active[index];
    const adjacent = active[target];
    if (!current || !adjacent) return false;
    return current.priority === adjacent.priority;
  };

  const move = (itemId: string, direction: -1 | 1) => {
    const ids = active.map((item) => item.id);
    const index = ids.indexOf(itemId);
    const target = index + direction;
    if (index < 0 || target < 0 || target >= ids.length) return;
    const current = active[index];
    if (!current || !canMove(current, direction)) return;
    const moved = ids[index];
    const swapped = ids[target];
    if (moved === undefined || swapped === undefined) return;
    ids[index] = swapped;
    ids[target] = moved;
    reorderMutation.mutate(ids);
  };

  const confirmRemove = async (item: QueueItem) => {
    const ok = await dialog.confirm({
      title: '移出阅读队列',
      message: `确定把《${item.document.title}》移出队列吗？阅读进度不会受影响。`,
      confirmText: '移出',
      danger: true,
    });
    if (ok) removeMutation.mutate(item.id);
  };

  if (queueQuery.isLoading) {
    return <StatePanel variant="loading" title="正在加载阅读队列..." />;
  }
  if (queueQuery.isError) {
    return (
      <StatePanel
        variant="error"
        title="阅读队列加载失败"
        description={errorMessage(queueQuery.error)}
        action={{ label: '重试', onClick: () => queueQuery.refetch() }}
      />
    );
  }

  const next = nextQuery.data;
  const busy = statusMutation.isPending || removeMutation.isPending || reorderMutation.isPending;

  return (
    <div className="space-y-4">
      {next && (
        <div className="rounded-xl border border-primary-200 bg-primary-50 p-4 flex items-center gap-4">
          <div className="w-12 h-16 flex-shrink-0 overflow-hidden rounded bg-white">
            <BookCover
              book={{ id: next.document.id, title: next.document.title, coverPath: next.document.cover_path }}
              variant="list"
            />
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-xs font-medium text-primary-700">下一本</p>
            <p className="truncate font-medium text-gray-900">{next.document.title}</p>
            <p className="truncate text-sm text-gray-500">{next.document.author || '未知作者'}</p>
          </div>
          <button
            type="button"
            onClick={() => router.push(`/reader/${next.document.id}`)}
            className="flex-shrink-0 rounded-lg bg-primary-600 px-4 py-2 text-sm font-medium text-white hover:bg-primary-700"
          >
            继续阅读
          </button>
        </div>
      )}

      <QueueSection
        title="在读队列"
        empty="队列里还没有书。开始阅读任意书籍后会自动加入。"
        items={active}
        busy={busy}
        onMove={move}
        canMove={canMove}
        priorityHint={
          hasMixedPriority ? '不同优先级的书按优先级排序，上移/下移仅在相同优先级内生效。' : undefined
        }
        onStatus={(item, status) => statusMutation.mutate({ id: item.id, status })}
        onRemove={confirmRemove}
      />

      {paused.length > 0 && (
        <QueueSection
          title="已暂停"
          items={paused}
          busy={busy}
          onStatus={(item, status) => statusMutation.mutate({ id: item.id, status })}
          onRemove={confirmRemove}
        />
      )}

      {done.length > 0 && (
        <QueueSection
          title="最近完成"
          items={done}
          busy={busy}
          onStatus={(item, status) => statusMutation.mutate({ id: item.id, status })}
          onRemove={confirmRemove}
        />
      )}
    </div>
  );
}

function QueueSection({
  title,
  empty,
  items,
  busy,
  onMove,
  canMove,
  priorityHint,
  onStatus,
  onRemove,
}: {
  title: string;
  empty?: string;
  items: QueueItem[];
  busy: boolean;
  onMove?: (itemId: string, direction: -1 | 1) => void;
  canMove?: (item: QueueItem, direction: -1 | 1) => boolean;
  priorityHint?: string;
  onStatus: (item: QueueItem, status: QueueStatus) => void;
  onRemove: (item: QueueItem) => void;
}) {
  return (
    <section className="rounded-xl border border-gray-200 bg-white shadow-sm">
      <div className="flex items-center justify-between border-b border-gray-100 px-4 py-3">
        <h2 className="text-sm font-semibold text-gray-900">{title}</h2>
        <span className="text-xs text-gray-400">{items.length} 本</span>
      </div>
      {priorityHint && <p className="px-4 pt-2 text-xs text-gray-400">{priorityHint}</p>}
      {items.length === 0 ? (
        <p className="px-4 py-6 text-sm text-gray-500">{empty || '暂无'}</p>
      ) : (
        <div className="divide-y divide-gray-100">
          {items.map((item) => (
            <QueueRow
              key={item.id}
              item={item}
              busy={busy}
              onMove={onMove}
              canMove={canMove}
              onStatus={onStatus}
              onRemove={onRemove}
            />
          ))}
        </div>
      )}
    </section>
  );
}

function QueueRow({
  item,
  busy,
  onMove,
  canMove,
  onStatus,
  onRemove,
}: {
  item: QueueItem;
  busy: boolean;
  onMove?: (itemId: string, direction: -1 | 1) => void;
  canMove?: (item: QueueItem, direction: -1 | 1) => boolean;
  onStatus: (item: QueueItem, status: QueueStatus) => void;
  onRemove: (item: QueueItem) => void;
}) {
  const progress = Math.round((item.document.read_progress || 0) * 100);
  return (
    <div className="flex items-center gap-3 px-4 py-3">
      <div className="h-16 w-12 flex-shrink-0 overflow-hidden rounded bg-gray-100">
        <BookCover
          book={{
            id: item.document.id,
            title: item.document.title,
            coverPath: item.document.cover_path,
          }}
          variant="list"
        />
      </div>
      <div className="min-w-0 flex-1">
        <Link
          href={`/reader/${item.document.id}`}
          className="truncate font-medium text-gray-900 hover:text-primary-700"
        >
          {item.document.title}
        </Link>
        <p className="truncate text-sm text-gray-500">
          {item.document.author || '未知作者'}
          {item.document.format ? ` · ${item.document.format.toUpperCase()}` : ''}
        </p>
        {item.note && <p className="truncate text-xs text-gray-400 mt-1">{item.note}</p>}
        {progress > 0 && (
          <div className="mt-1 flex items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-gray-200">
              <div className="h-full rounded-full bg-primary-500" style={{ width: `${progress}%` }} />
            </div>
            <span className="text-xs text-gray-400">{progress}%</span>
          </div>
        )}
      </div>
      <div className="flex flex-shrink-0 items-center gap-1">
        {onMove && (
          <>
            <button
              type="button"
              disabled={busy || !canMove?.(item, -1)}
              onClick={() => onMove(item.id, -1)}
              aria-label={`上移《${item.document.title}》`}
              className="rounded p-1.5 text-gray-500 hover:bg-gray-100 disabled:opacity-30"
            >
              <ArrowUp className="h-4 w-4" />
            </button>
            <button
              type="button"
              disabled={busy || !canMove?.(item, 1)}
              onClick={() => onMove(item.id, 1)}
              aria-label={`下移《${item.document.title}》`}
              className="rounded p-1.5 text-gray-500 hover:bg-gray-100 disabled:opacity-30"
            >
              <ArrowDown className="h-4 w-4" />
            </button>
          </>
        )}
        {item.status === 'active' && (
          <>
            <button
              type="button"
              disabled={busy}
              onClick={() => onStatus(item, 'paused')}
              className="rounded p-1.5 text-amber-600 hover:bg-amber-50 disabled:opacity-40"
              aria-label={`暂停《${item.document.title}》`}
              title="暂停"
            >
              <Pause className="h-4 w-4" />
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => onStatus(item, 'done')}
              className="rounded p-1.5 text-emerald-600 hover:bg-emerald-50 disabled:opacity-40"
              aria-label={`完成《${item.document.title}》`}
              title="完成"
            >
              <Check className="h-4 w-4" />
            </button>
          </>
        )}
        {item.status !== 'active' && (
          <button
            type="button"
            disabled={busy}
            onClick={() => onStatus(item, 'active')}
            className="rounded p-1.5 text-primary-600 hover:bg-primary-50 disabled:opacity-40"
            aria-label={`恢复《${item.document.title}》到队列`}
            title="恢复"
          >
            <Play className="h-4 w-4" />
          </button>
        )}
        <button
          type="button"
          disabled={busy}
          onClick={() => onRemove(item)}
          className="rounded p-1.5 text-gray-400 hover:bg-red-50 hover:text-red-600 disabled:opacity-40"
          aria-label={`移出《${item.document.title}》`}
          title="移出队列"
        >
          <Trash2 className="h-4 w-4" />
        </button>
      </div>
    </div>
  );
}

function ReviewTab() {
  const toast = useToast();
  const queryClient = useQueryClient();
  const tzOffset = -new Date().getTimezoneOffset();

  const statsQuery = useQuery({
    queryKey: [...REVIEWS_STATS_KEY, tzOffset],
    queryFn: () => getReviewStats(tzOffset),
    staleTime: 30_000,
  });
  const dueQuery = useQuery({
    queryKey: REVIEWS_DUE_KEY,
    queryFn: () => getDueReviews({ limit: 20, newLimit: 20 }),
    staleTime: 30_000,
    refetchOnWindowFocus: false,
  });

  const [index, setIndex] = useState(0);
  const [revealed, setRevealed] = useState(false);
  const [dismissed, setDismissed] = useState<Set<string>>(new Set());

  // 服务端刷新后清掉已不在到期列表里的本地隐藏标记，避免计数被重复扣减
  useEffect(() => {
    if (!dueQuery.data) return;
    const ids = new Set(dueQuery.data.cards.map((card) => card.annotation_id));
    setDismissed((prev) => {
      const next = new Set([...prev].filter((id) => ids.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [dueQuery.data]);

  const visibleCards = useMemo(
    () => (dueQuery.data?.cards ?? []).filter((card) => !dismissed.has(card.annotation_id)),
    [dueQuery.data, dismissed],
  );
  const safeIndex = Math.min(index, Math.max(0, visibleCards.length - 1));
  const currentCard = visibleCards[safeIndex];

  useEffect(() => {
    setRevealed(false);
  }, [currentCard?.annotation_id]);

  const invalidate = () => {
    void queryClient.invalidateQueries({ queryKey: REVIEWS_DUE_KEY });
    void queryClient.invalidateQueries({ queryKey: REVIEWS_STATS_KEY });
  };

  const rateMutation = useMutation({
    mutationFn: ({ id, rating }: { id: string; rating: ReviewRating }) => rateReview(id, rating),
    onMutate: ({ id }) => {
      setDismissed((prev) => new Set(prev).add(id));
    },
    onError: (error, vars) => {
      setDismissed((prev) => {
        const next = new Set(prev);
        next.delete(vars.id);
        return next;
      });
      toast.error(`评分失败：${errorMessage(error)}`);
    },
    onSuccess: invalidate,
  });

  const snoozeMutation = useMutation({
    mutationFn: (id: string) => snoozeReview(id, 1),
    onMutate: (id) => {
      setDismissed((prev) => new Set(prev).add(id));
    },
    onError: (error, id) => {
      setDismissed((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
      toast.error(`推迟失败：${errorMessage(error)}`);
    },
    onSuccess: invalidate,
  });

  const busy = rateMutation.isPending || snoozeMutation.isPending;

  const rateCurrent = (rating: ReviewRating) => {
    if (!currentCard || busy) return;
    rateMutation.mutate({ id: currentCard.annotation_id, rating });
  };

  const snoozeCurrent = () => {
    if (!currentCard || busy) return;
    snoozeMutation.mutate(currentCard.annotation_id);
  };

  useEffect(() => {
    if (!currentCard) return;
    const handler = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target &&
        (target.tagName === 'INPUT' ||
          target.tagName === 'TEXTAREA' ||
          target.tagName === 'SELECT' ||
          target.isContentEditable)
      ) {
        return;
      }
      if (!revealed) {
        if (event.key === ' ' || event.key === 'Enter') {
          event.preventDefault();
          setRevealed(true);
        }
        return;
      }
      const rating = RATING_BUTTONS.find((button) => button.key === event.key)?.rating;
      if (rating) {
        event.preventDefault();
        rateCurrent(rating);
        return;
      }
      if (event.key.toLowerCase() === 's') {
        event.preventDefault();
        snoozeCurrent();
        return;
      }
      if (event.key === 'ArrowLeft') {
        event.preventDefault();
        setIndex(Math.max(0, safeIndex - 1));
      } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        setIndex(Math.min(visibleCards.length - 1, safeIndex + 1));
      } else if (event.key === 'Escape') {
        event.preventDefault();
        setRevealed(false);
      }
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  });

  if (statsQuery.isError || dueQuery.isError) {
    return (
      <StatePanel
        variant="error"
        title="复习数据加载失败"
        description={errorMessage(statsQuery.error || dueQuery.error)}
        action={{
          label: '重试',
          onClick: () => {
            if (statsQuery.isError) void statsQuery.refetch();
            if (dueQuery.isError) void dueQuery.refetch();
          },
        }}
      />
    );
  }
  if (dueQuery.isLoading) {
    return <StatePanel variant="loading" title="正在加载到期卡片..." />;
  }

  const stats = statsQuery.data;
  const remaining = Math.max(0, (dueQuery.data?.total ?? 0) - dismissed.size);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm">
        <span className="font-medium text-gray-900">到期 {stats?.due_now ?? remaining}</span>
        <span className="text-gray-500">今天稍后 {stats?.due_today ?? 0}</span>
        <span className="text-gray-500">今日已复习 {stats?.reviewed_today ?? 0}</span>
        <span className="text-gray-500">今日新增 {stats?.new_today ?? 0}</span>
        <button
          type="button"
          onClick={invalidate}
          className="ml-auto inline-flex items-center gap-1 rounded-lg border border-gray-200 px-2.5 py-1 text-xs text-gray-600 hover:bg-gray-50"
        >
          <RotateCcw className="h-3 w-3" /> 刷新
        </button>
      </div>

      {!currentCard ? (
        <StatePanel
          variant="empty"
          title="今天的复习做完了"
          description={
            (stats?.upcoming_7d ?? 0) > 0
              ? `未来 7 天还有 ${stats?.upcoming_7d} 张到期卡片，明天再来。`
              : '暂时没有到期的卡片。新建划线或笔记后会自动进入复习。'
          }
        />
      ) : (
        <div className="rounded-xl border border-gray-200 bg-white shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-gray-100 px-5 py-3">
            <div className="min-w-0">
              <Link
                href={`/reader/${currentCard.document_id}`}
                className="truncate font-medium text-gray-900 hover:text-primary-700"
              >
                《{currentCard.document_title}》
              </Link>
              <p className="text-xs text-gray-400">{anchorLabel(currentCard)}</p>
            </div>
            <Link
              href={`/reader/${currentCard.document_id}`}
              className="inline-flex items-center gap-1 text-xs text-primary-600 hover:text-primary-700"
            >
              跳回原文 <ExternalLink className="h-3 w-3" />
            </Link>
          </div>

          <div className="px-5 py-6">
            <blockquote className="border-l-4 border-primary-200 pl-4 font-serif text-lg leading-relaxed text-gray-900">
              {currentCard.quote || '（这条笔记没有摘录原文）'}
            </blockquote>

            {revealed ? (
              <>
                {currentCard.content && (
                  <div className="mt-4 rounded-lg bg-gray-50 p-3 text-sm text-gray-700">
                    <p className="mb-1 text-xs font-medium text-gray-400">我的批注</p>
                    {currentCard.content}
                  </div>
                )}
                <div className="mt-6 grid grid-cols-4 gap-2">
                  {RATING_BUTTONS.map((button) => (
                    <button
                      key={button.rating}
                      type="button"
                      disabled={busy}
                      onClick={() => rateCurrent(button.rating)}
                      className={`flex flex-col items-center rounded-lg px-2 py-2 text-sm font-medium text-white transition-colors disabled:opacity-50 ${button.className}`}
                    >
                      <span>{button.label} {button.key}</span>
                      <span className="text-[11px] font-normal opacity-80">
                        {currentCard.preview?.[button.rating] ?? ''}
                      </span>
                    </button>
                  ))}
                </div>
                <div className="mt-3 flex items-center justify-between text-xs text-gray-400">
                  <span>快捷键 1–4 评分 · S 推迟 1 天 · ←/→ 翻页 · Esc 收起答案</span>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={snoozeCurrent}
                    className="text-primary-600 hover:text-primary-700 disabled:opacity-50"
                  >
                    推迟 1 天（S）
                  </button>
                </div>
              </>
            ) : (
              <div className="mt-6 text-center">
                <button
                  type="button"
                  onClick={() => setRevealed(true)}
                  className="rounded-lg bg-slate-900 px-5 py-2 text-sm font-medium text-white hover:bg-slate-800"
                >
                  显示答案（空格 / Enter）
                </button>
              </div>
            )}
          </div>

          <div className="flex items-center justify-between border-t border-gray-100 px-5 py-3 text-xs text-gray-500">
            <span>
              剩余 {remaining} 张 · 第 {safeIndex + 1}/{visibleCards.length} 张
            </span>
            <div className="flex items-center gap-1">
              <button
                type="button"
                disabled={safeIndex === 0}
                onClick={() => setIndex(Math.max(0, safeIndex - 1))}
                aria-label="上一张"
                className="rounded p-1.5 hover:bg-gray-100 disabled:opacity-30"
              >
                <ArrowLeft className="h-4 w-4" />
              </button>
              <button
                type="button"
                disabled={safeIndex >= visibleCards.length - 1}
                onClick={() => setIndex(Math.min(visibleCards.length - 1, safeIndex + 1))}
                aria-label="下一张"
                className="rounded p-1.5 hover:bg-gray-100 disabled:opacity-30"
              >
                <ArrowRight className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>
      )}

      {dueQuery.isFetching && !dueQuery.isLoading && (
        <p className="flex items-center gap-1 text-xs text-gray-400">
          <AlertCircle className="h-3 w-3" /> 正在同步最新到期列表…
        </p>
      )}
    </div>
  );
}

function anchorLabel(card: ReviewCard): string {
  const anchor = card.anchor || {};
  const chapter = typeof anchor.chapter === 'number' ? anchor.chapter : null;
  const page = typeof anchor.page === 'number' ? anchor.page : null;
  if (chapter != null && page != null) return `章节 ${chapter} / 第 ${page} 页`;
  if (page != null) return `第 ${page} 页`;
  if (chapter != null) return `章节 ${chapter}`;
  return card.type === 'note' ? '笔记' : '划线';
}
