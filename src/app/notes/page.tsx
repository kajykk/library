'use client';

import { Suspense, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import {
  NotebookPen,
  Plus,
  Trash2,
  CalendarDays,
  ChevronDown,
  X,
  Search,
  Sparkles,
} from 'lucide-react';
import { ApiDocument, listDocuments, createDocument, patchDocument, deleteDocument, downloadMarkdownExport } from '@/lib/api/client';
import { resolveMode } from '@/lib/dataSource';
import NoteEditor from '@/components/notes/NoteEditor';

// ---------- 笔记模板 ----------

const NOTE_TEMPLATES: Array<{ name: string; icon: string; content: (date: string) => string }> = [
  {
    name: '空白笔记',
    icon: '📄',
    content: () => '',
  },
  {
    name: '读书笔记',
    icon: '📚',
    content: () => '# 读书笔记\n\n## 基本信息\n- 书名：\n- 作者：\n- 开始阅读：\n\n## 摘录与批注\n\n## 读后感\n',
  },
  {
    name: '每日记录',
    icon: '📅',
    content: (date) => `# ${date} 记录\n\n## 今天做了什么\n\n## 收获与反思\n`,
  },
  {
    name: '会议纪要',
    icon: '📋',
    content: () => '# 会议纪要\n\n- 时间：\n- 参与人：\n- 议题：\n\n## 结论与行动项\n',
  },
];

export default function NotesPage() {
  return (
    <Suspense fallback={<main className="max-w-4xl mx-auto px-4 py-12 text-center text-gray-400">加载中...</main>}>
      <NotesPageInner />
    </Suspense>
  );
}

function NotesPageInner() {
  const [listType, setListType] = useState<'note' | 'webclip'>('note');
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [mode, setMode] = useState<'api' | 'local' | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [showTemplateMenu, setShowTemplateMenu] = useState(false);
  const [quickTitle, setQuickTitle] = useState('');
  const [quickContent, setQuickContent] = useState('');
  const [workflowFilter, setWorkflowFilter] = useState<'all' | 'inbox' | 'review' | 'done'>('all');
  const [selectedNotes, setSelectedNotes] = useState<Set<string>>(new Set());
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const listParentRef = useRef<HTMLDivElement>(null);

  const notesQuery = useQuery({
    queryKey: ['documents', listType],
    queryFn: () => listDocuments({ type: listType, include_content: 'false' }),
    enabled: mode === 'api',
  });
  const notes = notesQuery.data ?? [];
  const isLoading = notesQuery.isLoading;

  // 列表本地过滤：标题/标签
  const [listSearch, setListSearch] = useState('');
  const visibleNotes = useMemo(() => {
    const q = listSearch.trim().toLowerCase();
    return notes.filter((n) => {
      const matchesWorkflow = workflowFilter === 'all' || ((n.meta as { workflow?: string } | undefined)?.workflow || 'inbox') === workflowFilter;
      if (!matchesWorkflow) return false;
      if (!q) return true;
      return (
        (n.title || '').toLowerCase().includes(q) ||
        (n.tags || []).some((t) => (t.name || '').toLowerCase().includes(q))
      );
    });
  }, [notes, listSearch, workflowFilter]);

  const workflowCounts = useMemo(() => {
    const counts = { all: notes.length, inbox: 0, review: 0, done: 0 };
    notes.forEach((n) => {
      const wf = ((n.meta as { workflow?: string } | undefined)?.workflow || 'inbox') as 'inbox' | 'review' | 'done';
      counts[wf] += 1;
    });
    return counts;
  }, [notes]);

  const inboxCandidates = useMemo(() => notes.filter((n) => ((n.meta as { workflow?: string } | undefined)?.workflow || 'inbox') === 'inbox'), [notes]);

  const rowVirtualizer = useVirtualizer({
    count: listType === 'note' ? visibleNotes.length : 0,
    getScrollElement: () => listParentRef.current,
    estimateSize: () => 96,
    overscan: 6,
  });

  useEffect(() => {
    resolveMode().then(setMode);
  }, []);

  const refetchNotes = () => queryClient.invalidateQueries({ queryKey: ['documents', listType] });

  const toggleSelectedNote = (id: string) => {
    setSelectedNotes((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const clearSelection = () => setSelectedNotes(new Set());

  // 支持 /notes?open=<id> 直达编辑
  useEffect(() => {
    const openId = searchParams.get('open');
    if (openId && mode === 'api') {
      listDocuments({ q: '', include_content: 'false' })
        .then((docs) => {
          const target = docs.find((d) => d.id === openId);
          if (!target) return;
          // 剪藏/文章类文档在“剪藏”列表下渲染
          if (target.type === 'webclip' || target.type === 'article') setListType('webclip');
          setEditingId(openId);
        })
        .catch((err) => console.warn('直达笔记定位失败:', err));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, mode]);

  const handleCreate = async (template?: typeof NOTE_TEMPLATES[number]) => {
    setShowTemplateMenu(false);
    const content = template
      ? template.content(new Date().toLocaleDateString('zh-CN'))
      : quickContent;
    const title = (template ? template.name : quickTitle).trim() || '无标题笔记';
    const doc = await createDocument({
      type: 'note',
      title,
      content,
    });
    setQuickTitle('');
    setQuickContent('');
    refetchNotes();
    setEditingId(doc.id);
  };

  const handleDailyNote = async () => {
    const now = new Date();
    const title = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    let existing = notes.find((n) => n.title === title);
    if (!existing) {
      const all = await listDocuments({ include_content: 'false' });
      existing = all.find((n) => n.title === title);
    }
    if (existing) {
      setEditingId(existing.id);
      return;
    }
    const doc = await createDocument({ type: 'note', title, content: '' });
    refetchNotes();
    setEditingId(doc.id);
  };

  const handleImportMarkdown = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (!files) return;
    const results: string[] = [];
    for (const file of Array.from(files)) {
      try {
        const text = await file.text();
        const title = file.name.replace(/\.(md|markdown|txt)$/i, '') || '导入笔记';
        const doc = await createDocument({ type: 'note', title, content: text });
        results.push(`已导入《${doc.title}》`);
      } catch (err) {
        results.push(`${file.name}：${(err as Error).message}`);
      }
    }
    alert(results.join('\n'));
    refetchNotes();
    e.target.value = '';
  };

  const handleDelete = async (id: string) => {
    if (!confirm('确定删除这篇笔记吗？')) return;
    await deleteDocument(id);
    refetchNotes();
    if (editingId === id) setEditingId(null);
    setSelectedNotes((prev) => {
      const next = new Set(prev);
      next.delete(id);
      return next;
    });
  };

  const handleBatchArchive = async (workflow: 'review' | 'done') => {
    if (selectedNotes.size === 0) return;
    const ids = [...selectedNotes];
    if (!confirm(`将选中的 ${ids.length} 篇笔记标记为${workflow === 'review' ? '复盘中' : '已归档'}？`)) return;
    await Promise.all(ids.map((id) => patchDocument(id, { meta: { ...(notes.find((n) => n.id === id)?.meta || {}), workflow } })));
    clearSelection();
    refetchNotes();
  };

  if (mode === 'local') {
    return (
      <main className="max-w-3xl mx-auto px-4 py-12 text-center">
        <NotebookPen className="w-12 h-12 text-gray-300 mx-auto mb-4" />
        <p className="text-gray-600">笔记功能需要连接后端服务</p>
        <p className="text-sm text-gray-400 mt-1">请在设置页配置 API 地址与 Token</p>
      </main>
    );
  }

  if (editingId) {
    const note = notes.find((n) => n.id === editingId);
    if (note) {
      return (
        <NoteEditor
          key={editingId}
          note={note}
          onBack={() => {
            setEditingId(null);
            refetchNotes();
            // 等卸载时补发的保存落库后再刷一次列表
            setTimeout(() => refetchNotes(), 1200);
          }}
          onDelete={() => handleDelete(editingId)}
          onOpenNote={(id) => setEditingId(id)}
        />
      );
    }
  }

  return (
    <main className="max-w-4xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
      <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
        <div className="flex items-center gap-1 bg-white border border-gray-200 rounded-lg p-1">
          {([['note', '笔记'], ['webclip', '剪藏']] as const).map(([type, label]) => (
            <button
              key={type}
              onClick={() => {
                setListType(type);
                setExpandedId(null);
              }}
              className={`px-4 py-1.5 rounded-md text-sm font-medium transition-colors ${
                listType === type ? 'bg-primary-600 text-white' : 'text-gray-600 hover:bg-gray-100'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {listType === 'note' && (
          <div className="flex items-center gap-2 flex-wrap justify-end">
            {selectedNotes.size > 0 && (
              <>
                <button
                  onClick={() => handleBatchArchive('review')}
                  className="px-4 py-2 border border-blue-200 bg-blue-50 text-blue-700 rounded-lg hover:bg-blue-100 transition-colors text-sm"
                >
                  标记复盘中
                </button>
                <button
                  onClick={() => handleBatchArchive('done')}
                  className="px-4 py-2 border border-emerald-200 bg-emerald-50 text-emerald-700 rounded-lg hover:bg-emerald-100 transition-colors text-sm"
                >
                  快速归档
                </button>
                <button
                  onClick={clearSelection}
                  className="px-4 py-2 border border-gray-200 bg-white text-gray-600 rounded-lg hover:bg-gray-50 transition-colors text-sm"
                >
                  取消选择
                </button>
              </>
            )}
            <button
              onClick={handleDailyNote}
              className="flex items-center gap-2 px-4 py-2 border border-gray-200 bg-white text-gray-700 rounded-lg hover:bg-gray-50 transition-colors"
              title="打开或创建今天的日记"
            >
              <CalendarDays className="w-4 h-4" />
              今日笔记
            </button>
            <button
              onClick={() => downloadMarkdownExport().catch((e) => alert('导出失败：' + (e as Error).message))}
              className="px-4 py-2 border border-gray-200 bg-white text-gray-700 rounded-lg hover:bg-gray-50 transition-colors text-sm"
              title="导出全部笔记为 Markdown（zip）"
            >
              导出 MD
            </button>
            <label className="flex items-center gap-2 px-4 py-2 border border-gray-200 bg-white text-gray-700 rounded-lg hover:bg-gray-50 transition-colors text-sm cursor-pointer">
              导入 MD
              <input type="file" accept=".md,.markdown,.txt" multiple onChange={handleImportMarkdown} className="hidden" />
            </label>
          </div>
        )}
      </div>

      {listType === 'note' && (
        <div className="mb-4 rounded-2xl border border-gray-200 bg-gradient-to-br from-white to-slate-50 p-4 shadow-sm">
          <div className="flex items-center gap-2 text-xs text-gray-500 mb-2">
            <Sparkles className="w-3.5 h-3.5 text-primary-500" />
            快速捕获、模板化创建、双链联想、版本历史、引用插入与待整理工作流已整合到同一页面。
          </div>
          <div className="flex items-center justify-between gap-3 flex-wrap mb-3">
            <div className="flex items-center gap-2 flex-wrap">
              {([['all', '全部'], ['inbox', '待整理'], ['review', '复盘中'], ['done', '已归档']] as const).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setWorkflowFilter(value)}
                  className={`px-3 py-1 rounded-full text-xs transition-colors ${
                    workflowFilter === value ? 'bg-primary-600 text-white' : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {label} · {workflowCounts[value]}
                </button>
              ))}
            </div>
            <div>
              <h2 className="text-sm font-semibold text-gray-900">快速新建</h2>
              <p className="text-xs text-gray-500 mt-1">先捕获，再整理。适合会议纪要、临时灵感与待办。</p>
            </div>
            <div className="relative">
              <button
                onClick={() => setShowTemplateMenu((v) => !v)}
                className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
              >
                <Plus className="w-4 h-4" />
                模板新建
                <ChevronDown className="w-3.5 h-3.5" />
              </button>
              {showTemplateMenu && (
                <div className="absolute right-0 top-full mt-1 w-44 rounded-lg border border-gray-200 bg-white shadow-lg z-30 overflow-hidden">
                  {NOTE_TEMPLATES.map((tpl) => (
                    <button
                      key={tpl.name}
                      onClick={() => handleCreate(tpl)}
                      className="block w-full text-left px-3 py-2 text-sm text-gray-700 hover:bg-primary-50 transition-colors"
                    >
                      <span className="mr-2">{tpl.icon}</span>
                      {tpl.name}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
          <div className="grid gap-3 md:grid-cols-[1fr_2fr_auto]">
            <input
              type="text"
              value={quickTitle}
              onChange={(e) => setQuickTitle(e.target.value)}
              placeholder="新笔记标题"
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-primary-100"
            />
            <input
              type="text"
              value={quickContent}
              onChange={(e) => setQuickContent(e.target.value)}
              placeholder="先写一句要点，稍后再展开"
              className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg bg-white focus:outline-none focus:ring-2 focus:ring-primary-100"
            />
            <button
              onClick={() => handleCreate()}
              className="px-4 py-2 text-sm font-medium bg-gray-900 text-white rounded-lg hover:bg-gray-800 transition-colors"
            >
              立即创建
            </button>
          </div>
        </div>
      )}

      {isLoading ? (
        <div className="p-12 text-center text-gray-500">加载中...</div>
      ) : notes.length === 0 ? (
        <div className="bg-white rounded-lg border border-gray-200 p-12 text-center">
          <NotebookPen className="w-12 h-12 text-gray-300 mx-auto mb-4" />
          <p className="text-gray-500">{listType === 'note' ? '还没有笔记' : '还没有剪藏'}</p>
          <p className="text-sm text-gray-400 mt-1">
            {listType === 'note' ? '点击右上角新建第一篇笔记' : '在设置页输入网址即可剪藏网页'}
          </p>
        </div>
      ) : listType === 'note' ? (
        <div>
          <div className="relative mb-3">
            <Search className="w-4 h-4 text-gray-400 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={listSearch}
              onChange={(e) => setListSearch(e.target.value)}
              placeholder="搜索笔记标题或标签…"
              className="w-full pl-9 pr-9 py-2 text-sm rounded-lg border border-gray-200 bg-white focus:outline-none focus:ring-2 focus:ring-primary-100 placeholder:text-gray-400"
            />
            {listSearch && (
              <button
                onClick={() => setListSearch('')}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-400 hover:text-gray-600"
                title="清空搜索"
              >
                <X className="w-4 h-4" />
              </button>
            )}
          </div>
          {visibleNotes.length === 0 ? (
            <div className="bg-white rounded-lg border border-gray-200 p-8 text-center text-sm text-gray-400">
              没有匹配“{listSearch.trim()}”的笔记
            </div>
          ) : (
          <div
            ref={listParentRef}
            className="h-[calc(100vh-260px)] overflow-y-auto"
          >
            <div
              className="relative w-full"
              style={{ height: `${rowVirtualizer.getTotalSize()}px` }}
            >
              {rowVirtualizer.getVirtualItems().map((virtualRow) => {
                const note = visibleNotes[virtualRow.index];
              return (
                <div
                  key={note.id}
                  data-index={virtualRow.index}
                  ref={rowVirtualizer.measureElement}
                  className="absolute top-0 left-0 w-full"
                  style={{ transform: `translateY(${virtualRow.start}px)` }}
                >
                  <div
                    className="group bg-white rounded-lg border border-gray-200 p-4 mb-2 hover:shadow-sm transition-shadow cursor-pointer"
                    onClick={() => setEditingId(note.id)}
                  >
                    <div className="flex items-center justify-between gap-4">
                      <div className="min-w-0 flex-1">
                        <h3 className="font-medium text-gray-900 truncate">{note.title || '无标题笔记'}</h3>
                        <p className="text-sm text-gray-400 mt-1 truncate">
                          {(note.content || '').slice(0, 100) || '（空）'}
                        </p>
                        <div className="flex items-center gap-2 mt-2 flex-wrap">
                          <span className="text-xs text-gray-400">
                            {new Date(note.updated_at).toLocaleString('zh-CN')}
                          </span>
                          <button
                            onClick={(e) => {
                              e.stopPropagation();
                              toggleSelectedNote(note.id);
                            }}
                            className={`text-xs px-2 py-0.5 rounded-full border transition-colors ${
                              selectedNotes.has(note.id)
                                ? 'border-primary-500 bg-primary-50 text-primary-700'
                                : 'border-gray-200 bg-white text-gray-500 hover:bg-gray-50'
                            }`}
                          >
                            {selectedNotes.has(note.id) ? '已选中' : '选择'}
                          </button>
                          {note.tags.map((t) => (
                            <span key={t.id} className="text-xs px-2 py-0.5 rounded-full bg-primary-50 text-primary-700">
                              {t.name}
                            </span>
                          ))}
                        </div>
                      </div>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDelete(note.id);
                        }}
                        className="p-2 opacity-0 group-hover:opacity-100 hover:bg-red-50 text-red-500 rounded-lg transition-all"
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
          )}
        </div>
      ) : (
        <div className="space-y-2">
          {notes.map((clip) => (
            <div
              key={clip.id}
              className="group bg-white rounded-lg border border-gray-200 p-4 hover:shadow-sm transition-shadow cursor-pointer"
              onClick={() => setExpandedId(expandedId === clip.id ? null : clip.id)}
            >
              <div className="flex items-center justify-between gap-4">
                <div className="min-w-0 flex-1">
                  <h3 className="font-medium text-gray-900 truncate">{clip.title}</h3>
                  <a
                    href={clip.source_url || '#'}
                    target="_blank"
                    rel="noreferrer"
                    onClick={(e) => e.stopPropagation()}
                    className="text-xs text-primary-600 hover:underline truncate block mt-0.5"
                  >
                    {clip.source_url}
                  </a>
                  <p className="text-sm text-gray-400 mt-1 truncate">
                    {(clip.meta as { excerpt?: string })?.excerpt || (clip.content || '').slice(0, 100)}
                  </p>
                  <span className="text-xs text-gray-400 mt-2 block">
                    {new Date(clip.created_at).toLocaleString('zh-CN')}
                  </span>
                </div>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleDelete(clip.id);
                  }}
                  className="p-2 opacity-0 group-hover:opacity-100 hover:bg-red-50 text-red-500 rounded-lg transition-all"
                >
                  <Trash2 className="w-4 h-4" />
                </button>
              </div>
              {expandedId === clip.id && (
                <div className="mt-3 pt-3 border-t border-gray-100 max-h-96 overflow-y-auto text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
                  {clip.content || '（无正文）'}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
