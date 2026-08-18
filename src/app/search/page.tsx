'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Search as SearchIcon, FileText, BookOpen, Loader2, X, Clock3 } from 'lucide-react';
import Link from 'next/link';
import { searchApi, SearchHit } from '@/lib/api/client';
import { resolveMode } from '@/lib/dataSource';

const TYPE_LABELS: Record<string, string> = {
  book: '书籍',
  pdf: 'PDF',
  note: '笔记',
  article: '文章',
  webclip: '剪藏',
};

const TYPE_ORDER = ['book', 'pdf', 'note', 'article', 'webclip'];

const HISTORY_KEY = 'kb_search_history';
const HISTORY_MAX = 10;

function loadHistory(): string[] {
  try {
    const raw = JSON.parse(window.localStorage.getItem(HISTORY_KEY) || '[]');
    return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string') : [];
  } catch {
    return [];
  }
}

function highlightSnippet(snippet: string) {
  const parts = snippet.split(/<\/?mark>/);
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="bg-yellow-200 rounded px-0.5">{part}</mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

export default function SearchPage() {
  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [mode, setMode] = useState<'api' | 'local' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    if (typeof window !== 'undefined') setHistory(loadHistory());
  }, []);

  const recordHistory = useCallback((q: string) => {
    const trimmed = q.trim();
    if (!trimmed) return;
    setHistory((prev) => {
      const next = [trimmed, ...prev.filter((x) => x !== trimmed)].slice(0, HISTORY_MAX);
      window.localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
      return next;
    });
  }, []);

  const runSearch = useCallback(async (q: string, type: string) => {
    const trimmed = q.trim();
    if (!trimmed) {
      setHits(null);
      setError(null);
      return;
    }
    setIsSearching(true);
    setError(null);
    const m = mode ?? (await resolveMode());
    setMode(m);
    if (m !== 'api') {
      setIsSearching(false);
      return;
    }
    try {
      const result = await searchApi(trimmed, type || undefined);
      setHits(result);
      if (result.length > 0) recordHistory(trimmed);
    } catch (err) {
      setError((err as Error).message);
      setHits(null);
    } finally {
      setIsSearching(false);
    }
  }, [mode, recordHistory]);

  useEffect(() => {
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      runSearch(query, typeFilter);
    }, 300);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [query, typeFilter, runSearch]);

  return (
    <main className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
      <h1 className="text-xl font-bold text-gray-900 mb-4">全文检索</h1>

      <div className="flex gap-2 mb-4">
        <div className="relative flex-1">
          <SearchIcon className="w-5 h-5 absolute left-3 top-1/2 -translate-y-1/2 text-gray-400" />
          <input
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="输入关键词即时搜索书名、作者、笔记内容..."
            className="w-full pl-10 pr-4 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 focus:border-transparent outline-none"
            autoFocus
          />
        </div>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="px-3 py-2 border border-gray-300 rounded-lg bg-white text-sm focus:ring-2 focus:ring-primary-500 outline-none"
        >
          <option value="">全部类型</option>
          <option value="book">书籍</option>
          <option value="pdf">PDF</option>
          <option value="note">笔记</option>
          <option value="article">文章</option>
          <option value="webclip">剪藏</option>
        </select>
      </div>

      {!query.trim() && history.length > 0 && (
        <div className="mb-4">
          <div className="flex items-center gap-2 mb-2 text-xs text-gray-400">
            <Clock3 className="w-3.5 h-3.5" />
            最近搜索
            <button
              onClick={() => {
                window.localStorage.removeItem(HISTORY_KEY);
                setHistory([]);
              }}
              className="ml-auto flex items-center gap-0.5 hover:text-gray-600"
            >
              <X className="w-3 h-3" />
              清空
            </button>
          </div>
          <div className="flex flex-wrap gap-2">
            {history.map((h) => (
              <button
                key={h}
                onClick={() => setQuery(h)}
                className="px-3 py-1.5 text-sm bg-white border border-gray-200 rounded-full hover:bg-primary-50 hover:text-primary-600 transition-colors"
              >
                {h}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="flex items-center gap-2 mb-4 text-xs text-gray-400">
        {isSearching && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
        {!isSearching && hits && (
          <span>共 {hits.length} 条结果</span>
        )}
        {!isSearching && !hits && <span>支持空格分隔多个关键词，按相关度排序</span>}
      </div>

      {mode === 'local' && (
        <div className="bg-white rounded-lg border border-gray-200 p-8 text-center text-gray-500">
          全文检索需要连接后端服务，请先在设置页配置
        </div>
      )}

      {error && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div>
      )}

      {hits && hits.length === 0 && (
        <div className="bg-white rounded-lg border border-gray-200 p-8 text-center text-gray-500">
          没有找到与「{query}」相关的内容
        </div>
      )}

      {hits && hits.length > 0 && (
        <div className="space-y-6">
          {TYPE_ORDER.map((type) => {
            const group = hits.filter((h) => h.type === type);
            if (group.length === 0) return null;
            return (
              <section key={type}>
                <h2 className="text-sm font-medium text-gray-400 mb-2">
                  {TYPE_LABELS[type] || type}（{group.length}）
                </h2>
                <div className="space-y-2">
                  {group.map((hit) => (
                    <Link
                      key={hit.id}
                      href={['note', 'article', 'webclip'].includes(hit.type) ? `/notes?open=${hit.id}` : '/library'}
                      className="block bg-white rounded-lg border border-gray-200 p-4 hover:shadow-sm transition-shadow"
                    >
                      <div className="flex items-center gap-2 mb-1">
                        {hit.type === 'book' || hit.type === 'pdf' ? (
                          <BookOpen className="w-4 h-4 text-primary-500" />
                        ) : (
                          <FileText className="w-4 h-4 text-primary-500" />
                        )}
                        <h3 className="font-medium text-gray-900 truncate">{hit.title}</h3>
                        <span className="ml-auto shrink-0 text-xs px-2 py-0.5 rounded-full bg-gray-100 text-gray-500">
                          {TYPE_LABELS[hit.type] || hit.type}
                        </span>
                      </div>
                      <p className="text-sm text-gray-500 line-clamp-2">{highlightSnippet(hit.snippet)}</p>
                    </Link>
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </main>
  );
}
