'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Search as SearchIcon, FileText, BookOpen, Globe, CornerDownLeft, Loader2, Sparkles } from 'lucide-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ApiDocument, listDocuments, clipUrl, createDocument, patchDocument, startReindex, downloadBackup } from '@/lib/api/client';
import { resolveMode } from '@/lib/dataSource';
import { fuzzyScore } from '@/lib/fuzzy';

interface PaletteItem {
  id: string;
  title: string;
  type: string;
  description?: string;
  action?: () => Promise<void> | void;
}

const INBOX_ITEM_ID = '__inbox__';
const REVIEW_ITEM_ID = '__review__';
const COMPLETE_ITEM_ID = '__complete__';

const TYPE_LABELS: Record<string, string> = {
  book: '书籍',
  pdf: 'PDF',
  note: '笔记',
  article: '文章',
  webclip: '剪藏',
};

const CLIP_ITEM_ID = '__clip__';
const DAILY_NOTE_ITEM_ID = '__daily_note__';
const QUICK_NOTE_ITEM_ID = '__quick_note__';

/** 输入 "剪藏 <URL>"（或 clip <URL>）时返回置顶的剪藏动作项 */
function parseClipQuery(query: string): PaletteItem | null {
  const m = query.trim().match(/^(剪藏|clip)\s+(.+)$/i);
  if (!m) return null;
  const url = m[2].trim();
  const valid = /^https?:\/\//i.test(url);
  return {
    id: CLIP_ITEM_ID,
    title: valid ? `剪藏网页：${url}` : '剪藏网页（输入 http(s):// 开头的 URL）',
    type: 'webclip',
  };
}

export default function CommandPalette() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const [mode, setMode] = useState<'api' | 'local' | null>(null);
  const [clipBusy, setClipBusy] = useState(false);
  const [captureBusy, setCaptureBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const itemsQuery = useQuery({
    queryKey: ['palette-docs'],
    queryFn: async () => {
      const m = await resolveMode();
      setMode(m);
      if (m !== 'api') return [];
      const docs: ApiDocument[] = await listDocuments({ include_content: 'false' });
      return docs
        .filter((d) => !d.deleted_at)
        .map<PaletteItem>((d) => ({ id: d.id, title: d.title || '无标题', type: d.type }))
        .sort((a, b) => b.type.localeCompare(a.type));
    },
    enabled: open,
    staleTime: 60_000,
  });
  const items = useMemo(() => itemsQuery.data ?? [], [itemsQuery.data]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setOpen((v) => !v);
        setQuery('');
        setSelected(0);
      } else if (e.key === 'Escape' && open) {
        setOpen(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  useEffect(() => {
    if (open) inputRef.current?.focus();
  }, [open]);

  const results = useMemo(() => {
    const trimmed = query.trim();
    const clip = parseClipQuery(query);
    const quickActions: PaletteItem[] = !trimmed
      ? [
          {
            id: INBOX_ITEM_ID,
            title: '收集到待整理',
            type: 'note',
            description: '把当前捕获写入“待整理”工作流，后续再归档',
            action: async () => {
              const doc = await createDocument({ type: 'note', title: '待整理', content: '# 待整理\n\n- ' });
              await patchDocument(doc.id, { meta: { workflow: 'inbox', stage: 'capture' } });
              queryClient.invalidateQueries({ queryKey: ['documents', 'note'] });
              router.push(`/notes?open=${doc.id}`);
            },
          },
          {
            id: REVIEW_ITEM_ID,
            title: '打开待复盘清单',
            type: 'note',
            description: '快速进入需要归档、关联和补充标签的内容',
            action: async () => {
              const docs = await listDocuments({ type: 'note', include_content: 'false' });
              const target = docs.find((d) => (d.meta as { workflow?: string })?.workflow === 'inbox') || docs[0];
              if (target) router.push(`/notes?open=${target.id}`);
            },
          },
          {
            id: COMPLETE_ITEM_ID,
            title: '创建今日笔记',
            type: 'note',
            description: '快速打开或创建今天的日记/工作记录',
            action: async () => {
              const now = new Date();
              const title = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
              const doc = await createDocument({ type: 'note', title, content: '' });
              queryClient.invalidateQueries({ queryKey: ['documents', 'note'] });
              router.push(`/notes?open=${doc.id}`);
            },
          },
          {
            id: QUICK_NOTE_ITEM_ID,
            title: '快速新建空白笔记',
            type: 'note',
            description: '创建一个可立即编辑的空白笔记',
            action: async () => {
              const doc = await createDocument({ type: 'note', title: '无标题笔记', content: '' });
              queryClient.invalidateQueries({ queryKey: ['documents', 'note'] });
              router.push(`/notes?open=${doc.id}`);
            },
          },
          {
            id: '__reindex__',
            title: '重建全文索引',
            type: 'note',
            description: '启动后台索引任务以恢复全文检索质量',
            action: async () => {
              await startReindex();
            },
          },
          {
            id: '__backup__',
            title: '下载全量备份',
            type: 'note',
            description: '导出包含元数据、文件和封面的 zip 备份',
            action: async () => {
              await downloadBackup();
            },
          },
        ]
      : [];
    const normal = (() => {
      if (!trimmed) return items.slice(0, 12);
      const scored: Array<{ item: PaletteItem; score: number }> = [];
      for (const item of items) {
        const s = fuzzyScore(trimmed, item.title);
        if (s !== null) scored.push({ item, score: s });
      }
      return scored
        .sort((a, b) => b.score - a.score)
        .slice(0, 12)
        .map((s) => s.item);
    })();
    return clip ? [clip, ...quickActions, ...normal] : [...quickActions, ...normal];
  }, [items, query, queryClient, router]);

  useEffect(() => {
    setSelected(0);
  }, [query]);

  const go = useCallback(
    (item: PaletteItem) => {
      if (item.id === CLIP_ITEM_ID) {
        const m = query.trim().match(/^(剪藏|clip)\s+(.+)$/i);
        const url = m?.[2]?.trim();
        if (!url || !/^https?:\/\//i.test(url) || clipBusy) return;
        setClipBusy(true);
        clipUrl(url)
          .then((res) => {
            setOpen(false);
            router.push(`/notes?open=${res.id}`);
          })
          .catch((err) => {
            alert(`剪藏失败：${(err as Error).message}`);
          })
          .finally(() => setClipBusy(false));
        return;
      }
      if (item.action) {
        setCaptureBusy(true);
        Promise.resolve(item.action())
          .then(() => setOpen(false))
          .catch((err) => alert((err as Error).message))
          .finally(() => setCaptureBusy(false));
        return;
      }
      setOpen(false);
      if (['note', 'article', 'webclip'].includes(item.type)) {
        router.push(`/notes?open=${item.id}`);
      } else {
        router.push('/library');
      }
    },
    [router, query, clipBusy],
  );

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setSelected((s) => Math.min(s + 1, results.length - 1));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setSelected((s) => Math.max(s - 1, 0));
    } else if (e.key === 'Enter') {
      e.preventDefault();
      if (results[selected]) go(results[selected]);
    }
  };

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-start justify-center pt-[15vh]">
      <div className="fixed inset-0 bg-slate-900/40 backdrop-blur-sm" onClick={() => setOpen(false)} />
      <div className="relative w-full max-w-xl mx-4 rounded-xl bg-white shadow-2xl border border-gray-200 overflow-hidden">
        <div className="flex items-center gap-3 px-4 border-b border-gray-100">
          <SearchIcon className="w-4 h-4 text-gray-400" />
          <input
            ref={inputRef}
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder="搜索文档；输入“剪藏 <URL>”抓取网页…"
            className="flex-1 py-3.5 text-sm outline-none placeholder:text-gray-400"
          />
          {(clipBusy || captureBusy) && <Loader2 className="w-4 h-4 text-primary-500 animate-spin" />}
          <kbd className="text-[10px] px-1.5 py-0.5 rounded border border-gray-200 bg-gray-50 text-gray-400">ESC</kbd>
        </div>

        <div className="flex items-center gap-2 px-4 py-2 border-b border-gray-100 text-[11px] text-gray-400 bg-gray-50/60">
          <Sparkles className="w-3.5 h-3.5 text-primary-500" />
          支持快速捕获、文档跳转和网页剪藏，直接把动作变成入口。
        </div>
        <div className="max-h-80 overflow-y-auto py-1">
          {mode === 'local' ? (
            <div className="px-4 py-8 text-center text-sm text-gray-400">
              命令面板需要连接后端服务，请先在设置页配置
            </div>
          ) : results.length === 0 ? (
            <div className="px-4 py-8 text-center text-sm text-gray-400">
              {itemsQuery.isLoading ? '加载中…' : '没有匹配的文档'}
            </div>
          ) : (
            results.map((item, i) => (
              <button
                key={item.id}
                onClick={() => go(item)}
                onMouseEnter={() => setSelected(i)}
                className={`w-full flex items-center gap-3 px-4 py-2.5 text-left text-sm ${
                  i === selected ? 'bg-primary-50' : ''
                }`}
              >
                {item.type === 'webclip' ? (
                  <Globe className="w-4 h-4 text-pink-500" />
                ) : ['note', 'article'].includes(item.type) ? (
                  <FileText className="w-4 h-4 text-amber-500" />
                ) : (
                  <BookOpen className="w-4 h-4 text-blue-500" />
                )}
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="flex-1 truncate text-gray-800">{item.title}</span>
                    <span className="text-xs text-gray-400 shrink-0">{TYPE_LABELS[item.type] || item.type}</span>
                  </div>
                  {item.description && <p className="text-[11px] text-gray-400 mt-0.5 truncate">{item.description}</p>}
                </div>
                {i === selected && <CornerDownLeft className="w-3.5 h-3.5 text-gray-300" />}
              </button>
            ))
          )}
        </div>

        <div className="flex items-center gap-3 px-4 py-2 border-t border-gray-100 text-[11px] text-gray-400">
          <span>↑↓ 选择</span>
          <span>Enter 跳转 / 剪藏</span>
          <span className="ml-auto">Ctrl+K 打开/关闭</span>
        </div>
      </div>
    </div>
  );
}
