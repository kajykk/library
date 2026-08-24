'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Search as SearchIcon, FileText, BookOpen, Loader2, X, Clock3 } from 'lucide-react';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { searchApi, SearchHit, getSearchSummary, SearchSummary } from '@/lib/api/client';
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
  const router = useRouter();
  const [query, setQuery] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [summary, setSummary] = useState<SearchSummary | null>(null);
  const [recommendations, setRecommendations] = useState<string[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [mode, setMode] = useState<'api' | 'local' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [history, setHistory] = useState<string[]>([]);
  const [activeIndex, setActiveIndex] = useState(-1);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 键盘导航用的扁平结果（按分组顺序）
  const flatHits = useMemo(
    () => (hits ? TYPE_ORDER.flatMap((t) => hits.filter((h) => h.type === t)) : []),
    [hits],
  );

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
      setSummary(null);
      setRecommendations([]);
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
      const [result, stat] = await Promise.all([
        searchApi(trimmed, type || undefined),
        getSearchSummary(trimmed).catch(() => null),
      ]);
      setHits(result);
      setSummary(stat);
      setRecommendations(stat?.suggestions || []);
      if (result.length > 0) recordHistory(trimmed);
    } catch (err) {
      setError((err as Error).message);
      setHits(null);
      setSummary(null);
      setRecommendations([]);
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

  // 结果变化时重置选中
  useEffect(() => {
    setActiveIndex(-1);
  }, [hits, query]);

  // 选中项滚动进视口
  useEffect(() => {
    if (activeIndex < 0) return;
    document.getElementById(`search-hit-${activeIndex}`)?.scrollIntoView({ block: 'nearest' });
  }, [activeIndex]);

  const openHit = (index: number) => {
    const hit = flatHits[index];
    if (!hit) return;
    if (['note', 'article', 'webclip'].includes(hit.type)) {
      router.push(`/notes?open=${hit.id}`);
    } else {
      router.push('/library');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (flatHits.length > 0) {
        setActiveIndex((i) => Math.min(i + 1, flatHits.length - 1));
      }
      return;
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (flatHits.length > 0) {
        setActiveIndex((i) => Math.max(i - 1, 0));
      }
      return;
    }
    if (e.key === 'Enter') {
      const target = activeIndex >= 0 ? activeIndex : 0;
      if (flatHits[target]) {
        e.preventDefault();
        openHit(target);
      }
      return;
    }
    if (e.key === 'Escape') {
      setQuery('');
      setHits(null);
      setSummary(null);
      setRecommendations([]);
      setActiveIndex(-1);
    }
  };

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
            onKeyDown={handleKeyDown}
            placeholder="输入关键词即时搜索书名、作者、笔记内容...（↑↓ 选择、回车打开、Esc 清空）"
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
          <span>共 {summary?.total ?? hits.length} 条结果</span>
        )}
        {!isSearching && !hits && <span>支持空格分隔多个关键词，按相关度排序</span>}
      </div>

      {recommendations.length > 0 && (
        <div className="mb-4 rounded-lg border border-blue-100 bg-blue-50/60 px-4 py-3">
          <p className="text-xs text-blue-700 mb-2">相关搜索建议</p>
          <div className="flex flex-wrap gap-2">
            {recommendations.map((suggestion) => (
              <button
                key={suggestion}
                onClick={() => setQuery(suggestion)}
                className="rounded-full bg-white px-3 py-1 text-xs text-blue-700 border border-blue-100 hover:bg-blue-100 transition-colors"
              >
                {suggestion}
              </button>
            ))}
          </div>
        </div>
      )}

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
                  {group.map((hit) => {
                    const flatIndex = flatHits.indexOf(hit);
                    return (
                      <Link
                        key={hit.id}
                        id={`search-hit-${flatIndex}`}
                        href={['note', 'article', 'webclip'].includes(hit.type) ? `/notes?open=${hit.id}` : '/library'}
                        className={`block bg-white rounded-lg border p-4 transition-shadow ${
                          flatIndex === activeIndex
                            ? 'border-primary-500 ring-2 ring-primary-500'
                            : 'border-gray-200 hover:shadow-sm'
                        }`}
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
                    );
                  })}
                </div>
              </section>
            );
          })}
        </div>
      )}
    </main>
  );
}
