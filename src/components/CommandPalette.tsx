'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { Search as SearchIcon, FileText, BookOpen, Globe, CornerDownLeft, Loader2 } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { ApiDocument, listDocuments, clipUrl } from '@/lib/api/client';
import { resolveMode } from '@/lib/dataSource';

interface PaletteItem {
  id: string;
  title: string;
  type: string;
}

const TYPE_LABELS: Record<string, string> = {
  book: '书籍',
  pdf: 'PDF',
  note: '笔记',
  article: '文章',
  webclip: '剪藏',
};

const CLIP_ITEM_ID = '__clip__';

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

function fuzzyScore(query: string, target: string): number | null {
  const q = query.toLowerCase();
  const t = target.toLowerCase();
  if (!q) return 1;
  let score = 0;
  let ti = 0;
  let streak = 0;
  for (const ch of q) {
    const idx = t.indexOf(ch, ti);
    if (idx < 0) return null;
    if (idx === ti) {
      streak += 1;
      score += streak >= 2 ? 4 : 2;
    } else {
      streak = 0;
      score += 1;
    }
    ti = idx + 1;
  }
  return score - t.length * 0.01;
}

export default function CommandPalette() {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState(0);
  const [mode, setMode] = useState<'api' | 'local' | null>(null);
  const [clipBusy, setClipBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const itemsQuery = useQuery({
    queryKey: ['palette-docs'],
    queryFn: async () => {
      const m = await resolveMode();
      setMode(m);
      if (m !== 'api') return [];
      const docs: ApiDocument[] = await listDocuments();
      return docs
        .filter((d) => !d.deleted_at)
        .map((d) => ({ id: d.id, title: d.title || '无标题', type: d.type }))
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
    const clip = parseClipQuery(query);
    const normal = (() => {
      if (!query.trim()) return items.slice(0, 12);
      const scored: Array<{ item: PaletteItem; score: number }> = [];
      for (const item of items) {
        const s = fuzzyScore(query.trim(), item.title);
        if (s !== null) scored.push({ item, score: s });
      }
      return scored
        .sort((a, b) => b.score - a.score)
        .slice(0, 12)
        .map((s) => s.item);
    })();
    return clip ? [clip, ...normal] : normal;
  }, [items, query]);

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
          {clipBusy && <Loader2 className="w-4 h-4 text-primary-500 animate-spin" />}
          <kbd className="text-[10px] px-1.5 py-0.5 rounded border border-gray-200 bg-gray-50 text-gray-400">ESC</kbd>
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
                <span className="flex-1 truncate text-gray-800">{item.title}</span>
                <span className="text-xs text-gray-400">{TYPE_LABELS[item.type] || item.type}</span>
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
