'use client';

import { useState } from 'react';
import { Book, Category } from '@/lib/db';
import { saveBookEdit } from '@/lib/dataSource';
import { Modal as UiModal, useToast } from '@/components/ui';

interface BookEditModalProps {
  book: Book;
  categories: Category[];
  onClose: () => void;
  onSave: () => void;
}

export default function BookEditModal({ book, categories, onClose, onSave }: BookEditModalProps) {
  const [formData, setFormData] = useState<Book>({ ...book });
  const [newTag, setNewTag] = useState('');
  const [saving, setSaving] = useState(false);
  const toast = useToast();

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (saving) return; // 防双击重复提交
    setSaving(true);
    try {
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
