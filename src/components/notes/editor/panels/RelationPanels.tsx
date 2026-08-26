'use client';

/**
 * 笔记关系面板：反向链接 / 智能建议链接 / 未链接提及（纯展示）。
 * 数据来自 useNoteRelations；建立链接动作经 onLinkUnlinked 回调上抛。
 */

import { Link2 } from 'lucide-react';
import type { Backlink } from '@/lib/api/client';

interface SuggestedLink {
  id: string;
  title: string;
  type: string;
  score: number;
}

interface RelationPanelsProps {
  noteTitle: string;
  backlinks: Backlink[];
  suggestedLinks: SuggestedLink[];
  unlinked: Backlink[];
  onOpenNote: (id: string) => void;
  onLinkUnlinked: (id: string) => void;
}

/** 服务端 snippet 中 <mark> 高亮的安全拆分渲染（不使用 dangerouslySetInnerHTML） */
function highlightSnippet(snippet: string) {
  const parts = snippet.split(/<\/?mark>/);
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <mark key={i} className="bg-yellow-100 rounded px-0.5">{part}</mark>
    ) : (
      <span key={i}>{part}</span>
    ),
  );
}

export default function RelationPanels({
  noteTitle,
  backlinks,
  suggestedLinks,
  unlinked,
  onOpenNote,
  onLinkUnlinked,
}: RelationPanelsProps) {
  return (
    <>
      {/* 反向链接 */}
      <div className="mt-6">
        <h3 className="text-sm font-medium text-gray-700 mb-2">
          反向链接（{backlinks.length}）
        </h3>
        {backlinks.length === 0 ? (
          <p className="text-sm text-gray-400">暂无其他文档引用本笔记</p>
        ) : (
          <div className="space-y-1">
            {backlinks.map((b) => (
              <button
                key={b.id}
                onClick={() => onOpenNote(b.id)}
                className="block w-full text-left rounded-lg px-3 py-2 hover:bg-gray-50 transition-colors group"
                title="打开引用笔记"
              >
                <div className="flex items-center gap-2 text-sm">
                  <span className="w-1.5 h-1.5 rounded-full bg-amber-400" />
                  <span className="text-gray-700 group-hover:text-primary-600">{b.title}</span>
                  <span className="text-xs text-gray-400">{b.type === 'note' ? '笔记' : b.type}</span>
                </div>
                {b.snippet && (
                  <p className="text-xs text-gray-400 mt-1 line-clamp-2 pl-4">{highlightSnippet(b.snippet)}</p>
                )}
              </button>
            ))}
          </div>
        )}
      </div>

      {/* 未链接提及 */}
      {suggestedLinks.length > 0 && (
        <div className="mt-6">
          <h3 className="text-sm font-medium text-gray-700 mb-2">智能建议链接</h3>
          <p className="text-xs text-gray-400 mb-2">基于标题相似度与文档关系给出可进一步连接的候选项</p>
          <div className="space-y-1">
            {suggestedLinks.map((s) => (
              <button
                key={s.id}
                onClick={() => onOpenNote(s.id)}
                className="block w-full text-left rounded-lg border border-gray-200 px-3 py-2 hover:bg-gray-50 transition-colors"
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="text-sm text-gray-800 truncate">{s.title}</span>
                  <span className="text-xs text-gray-400">{Math.round(s.score * 100)}%</span>
                </div>
                <p className="text-xs text-gray-400 mt-0.5">{s.type}</p>
              </button>
            ))}
          </div>
        </div>
      )}

      {unlinked.length > 0 && (
        <div className="mt-6">
          <h3 className="text-sm font-medium text-gray-700 mb-2">
            未链接提及（{unlinked.length}）
          </h3>
          <p className="text-xs text-gray-400 mb-2">
            以下文档提到了「{noteTitle}」但尚未建立链接
          </p>
          <div className="space-y-1">
            {unlinked.map((u) => (
              <div
                key={u.id}
                className="flex items-start gap-3 rounded-lg border border-gray-200 bg-white px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 text-sm">
                    <span className="w-1.5 h-1.5 rounded-full bg-gray-300" />
                    <button
                      onClick={() => onOpenNote(u.id)}
                      className="text-gray-700 hover:text-primary-600 text-left truncate"
                    >
                      {u.title}
                    </button>
                    <span className="text-xs text-gray-400 shrink-0">{u.type === 'note' ? '笔记' : u.type}</span>
                  </div>
                  <p className="text-xs text-gray-400 mt-1 line-clamp-2 pl-4">{highlightSnippet(u.snippet)}</p>
                </div>
                <button
                  onClick={() => onLinkUnlinked(u.id)}
                  className="shrink-0 flex items-center gap-1 px-2.5 py-1 text-xs text-primary-600 border border-primary-200 rounded-md hover:bg-primary-50 transition-colors"
                >
                  <Link2 className="w-3 h-3" />
                  建立链接
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </>
  );
}
