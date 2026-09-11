'use client';

import { useState } from 'react';
import { Book, Category } from '@/lib/db';
import { saveBookEdit } from '@/lib/dataSource';
import { useDataSourceMode } from '@/lib/useDataSourceMode';
import { MetadataCandidate, applyMetadata, searchMetadataOnline } from '@/lib/api/client';
import { Modal as UiModal, useToast } from '@/components/ui';
import { Globe, Loader2 } from 'lucide-react';

interface BookEditModalProps {
  book: Book;
  categories: Category[];
  onClose: () => void;
  onSave: () => void;
}

const SOURCE_LABELS: Record<string, string> = {
  google_books: 'Google Books',
  open_library: 'Open Library',
};

/** 联网搜索元数据区块：Google Books / Open Library 候选一键填入（需后端） */
function MetaSearchSection({
  book,
  onApply,
}: {
  book: Book;
  onApply: (candidate: MetadataCandidate) => void;
}) {
  const toast = useToast();
  const [query, setQuery] = useState(
    [book.title, book.author === '未知作者' ? '' : book.author].filter(Boolean).join(' '),
  );
  const [searching, setSearching] = useState(false);
  const [candidates, setCandidates] = useState<MetadataCandidate[] | null>(null);

  const handleSearch = async () => {
    const q = query.trim();
    if (!q || searching) return;
    setSearching(true);
    try {
      const results = await searchMetadataOnline(q);
      setCandidates(results);
      if (results.length === 0) toast.info('没有找到匹配的在线元数据');
    } catch (err) {
      toast.error('联网搜索失败：' + (err as Error).message);
    } finally {
      setSearching(false);
    }
  };

  return (
    <div className="rounded-xl border border-primary-100 bg-primary-50/40 p-4">
      <div className="flex items-center gap-2 mb-2">
        <Globe className="w-4 h-4 text-primary-600" />
        <span className="text-sm font-medium text-gray-700">联网搜索元数据</span>
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              void handleSearch();
            }
          }}
          placeholder="书名 / 书名 + 作者 / ISBN"
          className="flex-1 rounded-lg border border-slate-200 px-3 py-1.5 text-sm outline-none transition focus:border-primary-500 focus:ring-2 focus:ring-primary-500"
        />
        <button
          type="button"
          onClick={() => void handleSearch()}
          disabled={searching || !query.trim()}
          className="flex items-center gap-1.5 rounded-lg bg-primary-600 px-3 py-1.5 text-sm text-white transition-colors hover:bg-primary-700 disabled:opacity-60"
        >
          {searching && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          搜索
        </button>
      </div>
      {candidates && candidates.length > 0 && (
        <div className="mt-3 space-y-2 max-h-56 overflow-y-auto">
          {candidates.map((c, i) => (
            <button
              key={`${c.source}-${i}`}
              type="button"
              onClick={() => onApply(c)}
              className="w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-left transition-colors hover:border-primary-300 hover:bg-primary-50"
            >
              <div className="flex items-center gap-2">
                <span className="text-[10px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 shrink-0">
                  {SOURCE_LABELS[c.source] ?? c.source}
                </span>
                <span className="text-sm font-medium text-gray-800 truncate">{c.title}</span>
                {c.year && <span className="text-xs text-gray-400 shrink-0">{c.year}</span>}
              </div>
              <p className="text-xs text-gray-500 mt-0.5 truncate">
                {[c.author, c.publisher].filter(Boolean).join(' · ') || '（无作者/出版社信息）'}
              </p>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function BookEditModal({ book, categories, onClose, onSave }: BookEditModalProps) {
  const [formData, setFormData] = useState<Book>({ ...book });
  const [newTag, setNewTag] = useState('');
  const [saving, setSaving] = useState(false);
  const [pendingCover, setPendingCover] = useState<string | null>(null);
  const [appliedSource, setAppliedSource] = useState<string | null>(null);
  const toast = useToast();
  const { mode } = useDataSourceMode();

  const handleApplyCandidate = (candidate: MetadataCandidate) => {
    setFormData((prev) => ({
      ...prev,
      title: candidate.title || prev.title,
      author: candidate.author || prev.author,
      publisher: candidate.publisher || prev.publisher,
      isbn: candidate.isbn || prev.isbn,
      language: candidate.language || prev.language,
      description: candidate.description || prev.description,
    }));
    setPendingCover(candidate.cover_url ?? null);
    setAppliedSource(SOURCE_LABELS[candidate.source] ?? candidate.source);
    toast.success('已填入候选元数据，保存时生效');
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (saving) return; // 防双击重复提交
    setSaving(true);
    try {
      // 选中的候选带封面 URL：先经后端下载落盘（API 模式）
      if (pendingCover && mode === 'api') {
        try {
          await applyMetadata(book.id, { cover_url: pendingCover });
        } catch (err) {
          toast.error('封面下载失败：' + (err as Error).message);
        }
      }
      await saveBookEdit(formData);
      onSave();
      onClose();
    } catch (error) {
      console.warn('保存书籍信息失败:', error);
      toast.error('保存书籍信息失败：' + ((error as Error).message || '未知错误'));
    } finally {
      setSaving(false);
    }
  };

  const handleAddTag = () => {
    if (!newTag.trim()) return;
    if (formData.tags.includes(newTag.trim())) return;
    setFormData(prev => ({
      ...prev,
      tags: [...prev.tags, newTag.trim()]
    }));
    setNewTag('');
  };

  const handleRemoveTag = (tag: string) => {
    setFormData(prev => ({
      ...prev,
      tags: prev.tags.filter(t => t !== tag)
    }));
  };

  return (
    <UiModal open onClose={onClose} ariaLabel="编辑书籍信息" panelClassName="max-h-[90vh] w-full max-w-lg overflow-y-auto">
      <div className="flex items-center justify-between border-b border-slate-200 px-6 py-4">
        <h2 className="text-lg font-semibold text-slate-900">编辑书籍信息</h2>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4 p-6">
          {mode === 'api' && (
            <MetaSearchSection book={book} onApply={handleApplyCandidate} />
          )}
          {appliedSource && (
            <p className="text-xs text-emerald-600 -mt-2">已应用 {appliedSource} 候选，封面将在保存时下载</p>
          )}

          <div>
            <label htmlFor="book-edit-title" className="block text-sm font-medium text-gray-700 mb-1">书名</label>
            <input
              id="book-edit-title"
              type="text"
              value={formData.title}
              onChange={(e) => setFormData(prev => ({ ...prev, title: e.target.value }))}
              className="w-full rounded-xl border border-slate-200 px-3 py-2 outline-none transition focus:border-primary-500 focus:ring-2 focus:ring-primary-500"
            />
          </div>

          <div>
            <label htmlFor="book-edit-author" className="block text-sm font-medium text-gray-700 mb-1">作者</label>
            <input
              id="book-edit-author"
              type="text"
              value={formData.author}
              onChange={(e) => setFormData(prev => ({ ...prev, author: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 outline-none"
            />
          </div>

          <div>
            <label htmlFor="book-edit-publisher" className="block text-sm font-medium text-gray-700 mb-1">出版社</label>
            <input
              id="book-edit-publisher"
              type="text"
              value={formData.publisher}
              onChange={(e) => setFormData(prev => ({ ...prev, publisher: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 outline-none"
            />
          </div>

          <div>
            <label htmlFor="book-edit-category" className="block text-sm font-medium text-gray-700 mb-1">分类</label>
            <select
              id="book-edit-category"
              value={formData.category}
              onChange={(e) => setFormData(prev => ({ ...prev, category: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 outline-none"
            >
              {categories.map(cat => (
                <option key={cat.id} value={cat.name}>{cat.name}</option>
              ))}
            </select>
          </div>

          <div>
            <label htmlFor="book-edit-isbn" className="block text-sm font-medium text-gray-700 mb-1">ISBN</label>
            <input
              id="book-edit-isbn"
              type="text"
              value={formData.isbn}
              onChange={(e) => setFormData(prev => ({ ...prev, isbn: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 outline-none"
            />
          </div>

          <div>
            <label htmlFor="book-edit-language" className="block text-sm font-medium text-gray-700 mb-1">语言</label>
            <input
              id="book-edit-language"
              type="text"
              value={formData.language}
              onChange={(e) => setFormData(prev => ({ ...prev, language: e.target.value }))}
              className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:ring-2 focus:ring-primary-500 outline-none"
            />
          </div>

          <div>
            <label htmlFor="book-edit-description" className="block text-sm font-medium text-gray-700 mb-1">描述</label>
            <textarea
              id="book-edit-description"
              value={formData.description}
              onChange={(e) => setFormData(prev => ({ ...prev, description: e.target.value }))}
              rows={3}
              className="w-full resize-none rounded-xl border border-slate-200 px-3 py-2 outline-none transition focus:border-primary-500 focus:ring-2 focus:ring-primary-500"
            />
          </div>

          <div>
            <label htmlFor="book-edit-new-tag" className="block text-sm font-medium text-gray-700 mb-1">标签</label>
            <div className="flex gap-2 mb-2">
              <input
                id="book-edit-new-tag"
                type="text"
                value={newTag}
                onChange={(e) => setNewTag(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault();
                    handleAddTag();
                  }
                }}
                placeholder="添加标签"
                className="flex-1 rounded-xl border border-slate-200 px-3 py-2 outline-none transition focus:border-primary-500 focus:ring-2 focus:ring-primary-500"
              />
              <button
                type="button"
                onClick={handleAddTag}
                className="rounded-xl bg-slate-100 px-4 py-2 text-slate-700 transition-colors hover:bg-slate-200"
              >
                添加
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {formData.tags.map(tag => (
                <span
                  key={tag}
                  className="inline-flex items-center gap-1 rounded-full bg-primary-50 px-2 py-1 text-sm text-primary-700"
                >
                  {tag}
                  <button
                    type="button"
                    onClick={() => handleRemoveTag(tag)}
                    className="hover:text-primary-900"
                  >
                    ×
                  </button>
                </span>
              ))}
            </div>
          </div>
          
          <div className="flex justify-end gap-3 pt-4">
            <button
              type="button"
              onClick={onClose}
              className="rounded-xl px-4 py-2 text-slate-700 transition-colors hover:bg-slate-100"
            >
              取消
            </button>
            <button
              type="submit"
              disabled={saving}
              className="rounded-xl bg-primary-600 px-4 py-2 text-white transition-colors hover:bg-primary-700 disabled:opacity-60"
            >
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </form>
    </UiModal>
  );
}
