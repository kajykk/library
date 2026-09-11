'use client';

/**
 * 阅读器批注/书签侧栏（纯展示）：状态与增删逻辑留在 BookReader，
 * 通过 props 交互。从 BookReader 抽离以缩小主编排文件。
 */

import { useRouter } from 'next/navigation';
import { Bookmark as BookmarkIcon, FilePlus2, Plus, Trash2 } from 'lucide-react';
import type { UnifiedAnnotation } from '@/lib/dataSource';

export type SidebarTab = 'bookmarks' | 'annotations';

interface AnnotationsSidebarProps {
  annotations: UnifiedAnnotation[];
  tab: SidebarTab;
  onTabChange: (tab: SidebarTab) => void;
  showNoteInput: boolean;
  onToggleNoteInput: () => void;
  newNoteContent: string;
  onNewNoteContentChange: (value: string) => void;
  onAddNote: () => void;
  /** 跳转到批注位置（章节索引或页码） */
  onJump: (position: number) => void;
  onDelete: (annotation: UnifiedAnnotation) => void;
  /** 当前格式是否支持划线（用于空态提示文案） */
  highlightSupported: boolean;
  /** 标注一键转笔记（API 模式才可用） */
  onToNote?: (annotation: UnifiedAnnotation) => void;
  /** 已转换映射：annotationId -> 新笔记 id（会话内） */
  convertedNotes?: Record<string, string>;
}

export default function AnnotationsSidebar({
  annotations,
  tab,
  onTabChange,
  showNoteInput,
  onToggleNoteInput,
  newNoteContent,
  onNewNoteContentChange,
  onAddNote,
  onJump,
  onDelete,
  highlightSupported,
  onToNote,
  convertedNotes = {},
}: AnnotationsSidebarProps) {
  const router = useRouter();
  const bookmarks = annotations.filter((a) => a.type === 'bookmark');
  const notes = annotations.filter((a) => a.type !== 'bookmark');

  return (
    <aside className="w-80 border-l border-gray-200 bg-gray-50 flex flex-col">
      <div className="flex border-b border-gray-200">
        <button
          onClick={() => onTabChange('bookmarks')}
          className={`flex-1 py-3 text-sm font-medium transition-colors ${
            tab === 'bookmarks'
              ? 'text-primary-600 border-b-2 border-primary-600 bg-white'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          书签 ({bookmarks.length})
        </button>
        <button
          onClick={() => onTabChange('annotations')}
          className={`flex-1 py-3 text-sm font-medium transition-colors ${
            tab === 'annotations'
              ? 'text-primary-600 border-b-2 border-primary-600 bg-white'
              : 'text-gray-500 hover:text-gray-700'
          }`}
        >
          批注 ({notes.length})
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {tab === 'bookmarks' ? (
          <div className="space-y-2">
            {bookmarks.length === 0 ? (
              <p className="text-center text-gray-400 py-8">暂无书签</p>
            ) : (
              bookmarks.map((bookmark) => (
                <div
                  key={bookmark.id}
                  className="flex items-center justify-between p-3 bg-white rounded-lg border border-gray-200 group"
                >
                  <button
                    onClick={() => onJump(bookmark.chapter)}
                    className="flex items-center gap-2 flex-1 text-left"
                  >
                    <BookmarkIcon className="w-4 h-4 text-primary-500" />
                    <div>
                      <p className="text-sm font-medium">{bookmark.content}</p>
                      <p className="text-xs text-gray-400">
                        {new Date(bookmark.createdAt).toLocaleDateString('zh-CN')}
                      </p>
                    </div>
                  </button>
                  <button
                    onClick={() => onDelete(bookmark)}
                    className="p-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 hover:bg-red-50 rounded transition-all"
                    aria-label="删除书签"
                  >
                    <Trash2 className="w-3 h-3 text-red-500" />
                  </button>
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="space-y-3">
            <button
              onClick={onToggleNoteInput}
              className="w-full flex items-center justify-center gap-2 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
            >
              <Plus className="w-4 h-4" />
              添加笔记
            </button>

            {showNoteInput && (
              <div className="bg-white p-3 rounded-lg border border-gray-200">
                <textarea
                  value={newNoteContent}
                  onChange={(e) => onNewNoteContentChange(e.target.value)}
                  placeholder="输入笔记内容..."
                  aria-label="笔记内容"
                  className="w-full h-24 px-3 py-2 border border-gray-300 rounded resize-none focus:ring-2 focus:ring-primary-500 outline-none text-sm"
                />
                <div className="flex justify-end gap-2 mt-2">
                  <button
                    onClick={onToggleNoteInput}
                    className="px-3 py-1 text-sm text-gray-600 hover:bg-gray-100 rounded transition-colors"
                  >
                    取消
                  </button>
                  <button
                    onClick={onAddNote}
                    className="px-3 py-1 text-sm bg-primary-600 text-white rounded hover:bg-primary-700 transition-colors"
                  >
                    保存
                  </button>
                </div>
              </div>
            )}

            {notes.length === 0 ? (
              <p className="text-center text-gray-400 py-8">
                暂无批注
                {highlightSupported ? '，选中正文即可划线' : ''}
              </p>
            ) : (
              notes.map((ann) => {
                const convertedNoteId = convertedNotes[ann.id];
                return (
                  <div key={ann.id} className="bg-white p-3 rounded-lg border border-gray-200 group">
                    {ann.type === 'highlight' ? (
                      <p className="text-sm text-gray-500 border-l-4 border-yellow-300 pl-2 italic">
                        {ann.quote}
                      </p>
                    ) : null}
                    {ann.content ? (
                      <p
                        className={`text-sm text-gray-700 ${ann.type === 'highlight' ? 'mt-1' : ''}`}
                      >
                        {ann.content}
                      </p>
                    ) : null}
                    <div className="flex items-center justify-between mt-2">
                      <span className="text-xs text-gray-400">
                        {ann.type === 'highlight' ? '划线' : `第 ${ann.page} 页`} ·{' '}
                        {new Date(ann.createdAt).toLocaleDateString('zh-CN')}
                      </span>
                      <div className="flex items-center gap-1">
                        <button
                          onClick={() => onJump(ann.chapter)}
                          className="text-xs text-primary-600 hover:underline opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-all"
                        >
                          跳转
                        </button>
                        {onToNote && convertedNoteId ? (
                          <button
                            onClick={() => router.push(`/notes?open=${convertedNoteId}`)}
                            className="text-xs text-emerald-600 hover:underline"
                            title="打开由该标注生成的笔记"
                          >
                            查看笔记
                          </button>
                        ) : onToNote ? (
                          <button
                            onClick={() => onToNote(ann)}
                            className="flex items-center gap-1 text-xs text-primary-600 hover:underline opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-all"
                            title="生成一条带原文引用和书名双链的笔记"
                          >
                            <FilePlus2 className="w-3 h-3" />
                            转为笔记
                          </button>
                        ) : null}
                        <button
                          onClick={() => onDelete(ann)}
                          className="p-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 hover:bg-red-50 rounded transition-all"
                          aria-label="删除批注"
                        >
                          <Trash2 className="w-3 h-3 text-red-500" />
                        </button>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        )}
      </div>
    </aside>
  );
}
