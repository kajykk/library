'use client';

import { useEffect, useMemo, useState } from 'react';
import { ApiDocument, Backlink, getBacklinks, getUnlinkedMentions, listDocuments } from '@/lib/api/client';

export interface SuggestedLink {
  id: string;
  title: string;
  type: string;
  score: number;
}

/**
 * 编辑器周边关系数据：
 * - 文档列表只拉一次，allTitles（[[双链联想候选）与 suggestedLinks（智能建议链接）
 *   都用 useMemo 从同一份数据派生；
 * - 反向链接 / 未链接提及各自独立加载。
 */
export function useNoteRelations(noteId: string, noteTitle: string): {
  backlinks: Backlink[];
  unlinked: Backlink[];
  allTitles: Array<{ id: string; title: string }>;
  suggestedLinks: SuggestedLink[];
  refreshBacklinks: () => void;
  /** 建立链接后把对应文档从未链接提及列表中移除 */
  removeUnlinked: (id: string) => void;
} {
  const [docs, setDocs] = useState<ApiDocument[]>([]);
  const [backlinks, setBacklinks] = useState<Backlink[]>([]);
  const [unlinked, setUnlinked] = useState<Backlink[]>([]);

  useEffect(() => {
    let cancelled = false;
    listDocuments({ include_content: 'false' })
      .then((items) => {
        if (!cancelled) setDocs(items);
      })
      .catch((err) => console.warn('加载文档列表失败:', err));
    getBacklinks(noteId)
      .then((items) => {
        if (!cancelled) setBacklinks(items);
      })
      .catch((err) => console.warn('加载反向链接失败:', err));
    getUnlinkedMentions(noteId)
      .then((items) => {
        if (!cancelled) setUnlinked(items);
      })
      .catch((err) => console.warn('加载未链接提及失败:', err));
    return () => {
      cancelled = true;
    };
  }, [noteId]);

  const allTitles = useMemo(
    () => docs.filter((d) => d.id !== noteId).map((d) => ({ id: d.id, title: d.title })),
    [docs, noteId],
  );

  const suggestedLinks = useMemo(() => {
    const current = (noteTitle || '').toLowerCase();
    return docs
      .filter((d) => d.id !== noteId)
      .map((d) => ({
        id: d.id,
        title: d.title,
        type: d.type,
        score:
          d.title.toLowerCase().includes(current) || current.includes(d.title.toLowerCase()) ? 0.9 : 0.4,
      }))
      .filter((d) => d.score >= 0.4)
      .sort((a, b) => b.score - a.score)
      .slice(0, 5);
  }, [docs, noteId, noteTitle]);

  const refreshBacklinks = () => {
    getBacklinks(noteId)
      .then(setBacklinks)
      .catch((err) => console.warn('刷新反向链接失败:', err));
  };

  const removeUnlinked = (id: string) => {
    setUnlinked((prev) => prev.filter((u) => u.id !== id));
  };

  return { backlinks, unlinked, allTitles, suggestedLinks, refreshBacklinks, removeUnlinked };
}
