'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery } from '@tanstack/react-query';
import { Book, ReadingRecord, getBooks, getAllReadingRecords } from '@/lib/db';
import { formatDuration } from '@/lib/utils';
import { getStats, StatsOut, getAuthors, AuthorStat, getHealthStats, HealthOut } from '@/lib/api/client';
import { resolveMode } from '@/lib/dataSource';
import { X, BookOpen, Clock, TrendingUp, Calendar, User, HeartPulse } from 'lucide-react';

interface StatisticsPanelProps {
  onClose: () => void;
}

export default function StatisticsPanel({ onClose }: StatisticsPanelProps) {
  const router = useRouter();
  const [books, setBooks] = useState<Book[]>([]);
  const [records, setRecords] = useState<ReadingRecord[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isApiMode, setIsApiMode] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setIsLoading(true);
      const mode = await resolveMode();
      if (cancelled) return;
      if (mode !== 'api') {
        const [loadedBooks, loadedRecords] = await Promise.all([
          getBooks(),
          getAllReadingRecords(),
        ]);
        if (cancelled) return;
        setBooks(loadedBooks);
        setRecords(loadedRecords);
        setIsApiMode(false);
      } else {
        setIsApiMode(true);
      }
      setIsLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const apiStats = useQuery({ queryKey: ['stats'], queryFn: getStats, enabled: isApiMode }).data ?? null;
  const authors = useQuery({ queryKey: ['authors'], queryFn: getAuthors, enabled: isApiMode }).data ?? [];
  const health = useQuery({ queryKey: ['health'], queryFn: getHealthStats, enabled: isApiMode }).data ?? null;

  // 统一口径：本地模式由 books/records 计算，API 模式用后端聚合结果
  const totalBooks = apiStats ? (apiStats.by_type.book || 0) + (apiStats.by_type.pdf || 0) : books.length;
  const readBooks = apiStats ? apiStats.read_started : books.filter(b => b.readProgress > 0).length;
  const completedBooks = apiStats ? apiStats.completed : books.filter(b => b.readProgress >= 100).length;

  const totalReadingMinutes = apiStats
    ? apiStats.total_reading_minutes
    : Math.floor(records.reduce((sum, r) => sum + (r.endTime - r.startTime), 0) / 60000);

  const booksByFormat = apiStats
    ? (() => {
        const acc: Record<string, number> = {};
        if (apiStats.by_type.book) acc['epub/mobi/txt'] = apiStats.by_type.book;
        if (apiStats.by_type.pdf) acc['pdf'] = apiStats.by_type.pdf;
        return acc;
      })()
    : books.reduce((acc, book) => {
        acc[book.format] = (acc[book.format] || 0) + 1;
        return acc;
      }, {} as Record<string, number>);

  const readingByDay = apiStats
    ? apiStats.recent_days.map((d) => [d.date, d.minutes] as const)
    : Object.entries(
        records.reduce((acc, record) => {
          const date = new Date(record.startTime).toLocaleDateString('zh-CN');
          const duration = (record.endTime - record.startTime) / 60000;
          acc[date] = (acc[date] || 0) + duration;
          return acc;
        }, {} as Record<string, number>)
      );

  const topTags = apiStats ? apiStats.top_tags : [];

  if (isLoading) {
    return (
      <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 mb-6">
        <div className="text-center text-gray-500">加载统计中...</div>
      </div>
    );
  }

  return (
    <div className="bg-white rounded-lg shadow-sm border border-gray-200 p-6 mb-6">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-lg font-semibold text-gray-900">阅读统计</h2>
        <button
          onClick={onClose}
          className="p-1 hover:bg-gray-100 rounded transition-colors"
        >
          <X className="w-5 h-5" />
        </button>
      </div>
      
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
        <div className="bg-primary-50 rounded-lg p-4">
          <div className="flex items-center gap-2 text-primary-600 mb-2">
            <BookOpen className="w-5 h-5" />
            <span className="text-sm font-medium">总书籍</span>
          </div>
          <p className="text-2xl font-bold text-primary-700">{totalBooks}</p>
          <p className="text-xs text-primary-500 mt-1">本</p>
        </div>
        
        <div className="bg-green-50 rounded-lg p-4">
          <div className="flex items-center gap-2 text-green-600 mb-2">
            <TrendingUp className="w-5 h-5" />
            <span className="text-sm font-medium">已阅读</span>
          </div>
          <p className="text-2xl font-bold text-green-700">{readBooks}</p>
          <p className="text-xs text-green-500 mt-1">本 ({totalBooks > 0 ? Math.round((readBooks / totalBooks) * 100) : 0}%)</p>
        </div>
        
        <div className="bg-purple-50 rounded-lg p-4">
          <div className="flex items-center gap-2 text-purple-600 mb-2">
            <Calendar className="w-5 h-5" />
            <span className="text-sm font-medium">已读完</span>
          </div>
          <p className="text-2xl font-bold text-purple-700">{completedBooks}</p>
          <p className="text-xs text-purple-500 mt-1">本</p>
        </div>
        
        <div className="bg-orange-50 rounded-lg p-4">
          <div className="flex items-center gap-2 text-orange-600 mb-2">
            <Clock className="w-5 h-5" />
            <span className="text-sm font-medium">阅读时长</span>
          </div>
          <p className="text-2xl font-bold text-orange-700">{formatDuration(totalReadingMinutes)}</p>
          <p className="text-xs text-orange-500 mt-1">累计</p>
        </div>
      </div>
      
      <div className="grid md:grid-cols-2 gap-6">
        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-3">类型分布</h3>
          <div className="space-y-2">
            {Object.entries(booksByFormat).map(([format, count]) => (
              <div key={format} className="flex items-center gap-3">
                <span className="text-sm text-gray-600 w-24 truncate">{apiStats ? format : format.toUpperCase()}</span>
                <div className="flex-1 h-6 bg-gray-100 rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary-500 rounded-full transition-all"
                    style={{ width: `${totalBooks > 0 ? (count / totalBooks) * 100 : 0}%` }}
                  />
                </div>
                <span className="text-sm text-gray-500 w-8 text-right">{count}</span>
              </div>
            ))}
          </div>
        </div>

        <div>
          <h3 className="text-sm font-medium text-gray-700 mb-3">{apiStats ? '热门标签' : '分类分布'}</h3>
          <div className="space-y-2">
            {apiStats ? (
              topTags.length === 0 ? (
                <p className="text-sm text-gray-400 py-2">暂无标签</p>
              ) : (
                topTags.map((tag) => (
                  <div key={tag.name} className="flex items-center gap-3">
                    <span className="text-sm text-gray-600 w-20 truncate">{tag.name}</span>
                    <div className="flex-1 h-6 bg-gray-100 rounded-full overflow-hidden">
                      <div
                        className="h-full bg-green-500 rounded-full transition-all"
                        style={{ width: `${totalBooks > 0 ? (tag.count / totalBooks) * 100 : 0}%` }}
                      />
                    </div>
                    <span className="text-sm text-gray-500 w-8 text-right">{tag.count}</span>
                  </div>
                ))
              )
            ) : (
              Object.entries(
                books.reduce((acc, book) => {
                  acc[book.category] = (acc[book.category] || 0) + 1;
                  return acc;
                }, {} as Record<string, number>)
              ).map(([category, count]) => (
                <div key={category} className="flex items-center gap-3">
                  <span className="text-sm text-gray-600 w-20 truncate">{category}</span>
                  <div className="flex-1 h-6 bg-gray-100 rounded-full overflow-hidden">
                    <div
                      className="h-full bg-green-500 rounded-full transition-all"
                      style={{ width: `${totalBooks > 0 ? (count / totalBooks) * 100 : 0}%` }}
                    />
                  </div>
                  <span className="text-sm text-gray-500 w-8 text-right">{count}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>

      {readingByDay.length > 0 && (
        <div className="mt-6">
          <h3 className="text-sm font-medium text-gray-700 mb-3">最近阅读记录</h3>
          <div className="space-y-2">
            {readingByDay.slice(0, 7).map(([date, minutes]) => (
              <div key={date} className="flex items-center justify-between py-2 px-3 bg-gray-50 rounded-lg">
                <span className="text-sm text-gray-600">{date}</span>
                <span className="text-sm text-gray-500">{formatDuration(minutes)}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {authors.length > 0 && (
        <div className="mt-6">
          <div className="flex items-center gap-2 mb-3">
            <User className="w-4 h-4 text-gray-500" />
            <h3 className="text-sm font-medium text-gray-700">作者聚合（点击按作者筛选书籍）</h3>
          </div>
          <div className="flex flex-wrap gap-2">
            {authors.map((a) => (
              <button
                key={a.name}
                onClick={() => router.push(`/library?author=${encodeURIComponent(a.name)}`)}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-full border border-gray-200 bg-gray-50 text-sm text-gray-700 hover:border-primary-300 hover:bg-primary-50 transition-colors"
              >
                {a.name}
                <span className="text-xs px-1.5 py-0.5 rounded-full bg-primary-100 text-primary-700">{a.count}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      {health && (
        <div className="mt-6">
          <div className="flex items-center gap-2 mb-3">
            <HeartPulse className="w-4 h-4 text-gray-500" />
            <h3 className="text-sm font-medium text-gray-700">知识库健康</h3>
          </div>
          <div className="grid md:grid-cols-3 gap-4">
            <div className="border border-gray-200 rounded-lg p-3">
              <h4 className="text-xs font-medium text-amber-600 mb-2">孤立文档（{health.orphans.length}）</h4>
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {health.orphans.length === 0 ? (
                  <p className="text-xs text-gray-400">全部文档都有连接</p>
                ) : (
                  health.orphans.map((o) => (
                    <button
                      key={o.id}
                      onClick={() =>
                        router.push(['note', 'article', 'webclip'].includes(o.type) ? `/notes?open=${o.id}` : '/library')
                      }
                      className="block w-full text-left text-xs text-gray-600 hover:text-primary-600 truncate"
                      title={o.title}
                    >
                      {o.title}
                    </button>
                  ))
                )}
              </div>
            </div>
            <div className="border border-gray-200 rounded-lg p-3">
              <h4 className="text-xs font-medium text-rose-600 mb-2">占位提及（{health.placeholders.length}）</h4>
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {health.placeholders.length === 0 ? (
                  <p className="text-xs text-gray-400">没有悬空的 [[提及]]</p>
                ) : (
                  health.placeholders.map((p) => (
                    <p key={p.mention} className="text-xs text-gray-600 truncate" title={`[[${p.mention}]]`}>
                      [[{p.mention}]]<span className="text-gray-400"> ×{p.count}</span>
                    </p>
                  ))
                )}
              </div>
            </div>
            <div className="border border-gray-200 rounded-lg p-3">
              <h4 className="text-xs font-medium text-blue-600 mb-2">未开始阅读（{health.unread.length}）</h4>
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {health.unread.length === 0 ? (
                  <p className="text-xs text-gray-400">所有书籍都已开始阅读</p>
                ) : (
                  health.unread.map((u) => (
                    <button
                      key={u.id}
                      onClick={() => router.push('/library')}
                      className="block w-full text-left text-xs text-gray-600 hover:text-primary-600 truncate"
                      title={u.title}
                    >
                      {u.title}
                    </button>
                  ))
                )}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
