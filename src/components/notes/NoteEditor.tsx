'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { useEditor, EditorContent } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from 'tiptap-markdown';
import Collaboration from '@tiptap/extension-collaboration';
import type { WebsocketProvider } from 'y-websocket';
import {
  ArrowLeft,
  Bold,
  Italic,
  Heading2,
  List,
  ListOrdered,
  Code,
  Quote,
  History,
  BookMarked,
  Trash2,
  Wifi,
  WifiOff,
  Users,
  Sparkles,
} from 'lucide-react';
import * as Y from 'yjs';
import {
  ApiDocument,
  AnnotationHit,
  createLink,
  getDocument,
  getDocumentInsight,
  listDocuments,
  patchDocument,
} from '@/lib/api/client';
import { useYjsCollab } from './useYjsCollab';
import { useDebouncedSave } from './useDebouncedSave';
import { useNoteRelations } from './useNoteRelations';
import CitationDialog from './CitationDialog';
import HistoryDrawer from './HistoryDrawer';
import { useToast } from '@/components/ui';
import { WikiLinkExtension } from './editor/wikiLink';
import SlashMenu, { SLASH_ITEMS } from './editor/menus/SlashMenu';
import MentionMenu from './editor/menus/MentionMenu';
import EditorToolbar from './editor/Toolbar';
import RelationPanels from './editor/panels/RelationPanels';

export interface NoteEditorProps {
  note: ApiDocument;
  onBack: () => void;
  onDelete: () => void;
  onOpenNote: (id: string) => void;
}

export default function NoteEditor(props: NoteEditorProps) {
  const { ydoc, provider, status, onlineUsers } = useYjsCollab(props.note.id);

  // Provider 在 useEffect 中创建（StrictMode 双载安全）；就绪前先渲染占位，
  // 保证编辑器挂载时协作文档已可用。
  if (!provider) {
    return (
      <main className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
        <div className="py-24 text-center text-sm text-gray-400">正在建立协作连接...</div>
      </main>
    );
  }

  return (
    <NoteEditorView
      {...props}
      ydoc={ydoc}
      provider={provider}
      collabStatus={status}
      onlineUsers={onlineUsers}
    />
  );
}

function NoteEditorView({
  note,
  onBack,
  onDelete,
  onOpenNote,
  ydoc,
  provider,
  collabStatus,
  onlineUsers,
}: NoteEditorProps & {
  ydoc: Y.Doc;
  provider: WebsocketProvider;
  collabStatus: 'connecting' | 'connected' | 'offline';
  onlineUsers: { clientId: number; name: string }[];
}) {
  const [title, setTitle] = useState(note.title);
  const [tags, setTags] = useState(note.tags.map((t) => t.name));
  const toast = useToast();
  const [newTag, setNewTag] = useState('');
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'dirty'>('saved');
  const [mention, setMention] = useState<{ query: string; top: number; left: number } | null>(null);
  const [slash, setSlash] = useState<{ query: string; top: number; left: number; index: number } | null>(null);
  const [insight, setInsight] = useState<{ summary: string; suggested_tags: string[] } | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [showCite, setShowCite] = useState(false);
  const contentRef = useRef(note.content || '');
  // 列表接口为轻量摘要模式：编辑器必须用 detail 接口拉全文，防止截断内容被回写
  const fullContentRef = useRef<string | null>(null);
  const titleRef = useRef(note.title);
  const tagsRef = useRef(tags);
  // 最近一次已落库的标签集合：增删都按与它的差异决定是否提交（避免删回初始值被跳过）
  const savedTagsRef = useRef<string[]>(note.tags.map((t) => t.name));
  const editorContainerRef = useRef<HTMLDivElement>(null);
  // 协作 sync 后只播种一次编辑器内容
  const seededRef = useRef(false);
  // 惰性序列化：保存时刻才从 TipTap 取 Markdown（返回 null 表示编辑器不可用，回退 contentRef）
  const getMarkdownRef = useRef<() => string | null>(() => null);

  const { backlinks, unlinked, allTitles, suggestedLinks, refreshBacklinks, removeUnlinked } = useNoteRelations(note.id, note.title);

  const queryClient = useQueryClient();

  const { schedule: scheduleSave, flush: flushSave } = useDebouncedSave({
    docId: note.id,
    titleRef,
    contentRef,
    getContent: getMarkdownRef,
    tagsRef,
    savedTagsRef,
    onStateChange: setSaveState,
  });

  // 智能摘要与推荐标签
  useEffect(() => {
    let cancelled = false;
    getDocumentInsight(note.id)
      .then((value) => {
        if (!cancelled) setInsight(value);
      })
      .catch((err) => {
        console.warn('加载智能摘要失败:', err);
        if (!cancelled) setInsight(null);
      });
    return () => {
      cancelled = true;
    };
  }, [note.id]);

  const handleLinkUnlinked = async (sourceId: string) => {
    try {
      await createLink(sourceId, note.id, 'mention');
      removeUnlinked(sourceId);
      refreshBacklinks();
    } catch (err) {
      toast.error('创建链接失败：' + (err as Error).message);
    }
  };

  const [wikilinkPreview, setWikilinkPreview] = useState<{
    title: string;
    x: number;
    y: number;
    doc: { id: string; title: string; type: string; snippet: string } | null;
  } | null>(null);

  // 点击卡片外任意位置关闭（此前只能靠 onMouseLeave，触屏/键盘用户无法关闭）
  useEffect(() => {
    if (!wikilinkPreview) return;
    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null;
      if (target?.closest('[data-wikilink-preview]')) return;
      if (target?.closest('.kb-wikilink')) return; // 点击其他双链时由 handleClick 重建预览
      setWikilinkPreview(null);
    };
    document.addEventListener('mousedown', onPointerDown);
    return () => document.removeEventListener('mousedown', onPointerDown);
  }, [wikilinkPreview]);

  const editor = useEditor({
    extensions: [
      StarterKit,
      Markdown.configure({ html: false, transformPastedText: true }),
      WikiLinkExtension,
      Collaboration.configure({ document: ydoc }),
    ],
    content: '',
    onUpdate: () => {
      // 序列化延迟到保存时刻执行（getMarkdownRef）：此前每个按键都对全文档
      // getMarkdown()（O(文档大小)），长文打字可感卡顿
      scheduleSave();
    },
    editorProps: {
      attributes: {
        class: 'prose max-w-none min-h-[50vh] focus:outline-none',
      },
      handleClick: (_, __, event) => {
        const target = (event.target as HTMLElement).closest('.kb-wikilink') as HTMLElement | null;
        if (!target) return false;
        const title = target.getAttribute('data-title') || '';
        if (!title) return false;
        const rect = target.getBoundingClientRect();
        setWikilinkPreview({ title, x: rect.left, y: rect.bottom + 6, doc: null });
        // fetchQuery：并发同标题点击去重 + 60s 缓存（此前每次点击都打一次搜索接口）
        queryClient
          .fetchQuery({
            queryKey: ['doc-by-title', title],
            queryFn: () => listDocuments({ q: title, include_content: 'false' }),
            staleTime: 60_000,
          })
          .then((docs) => {
            const found = docs.find((d) => d.title === title);
            setWikilinkPreview((prev) =>
              prev && prev.title === title
                ? {
                    ...prev,
                    doc: found
                      ? {
                          id: found.id,
                          title: found.title,
                          type: found.type,
                          snippet: (found.content || found.description || '').slice(0, 120),
                        }
                      : null,
                  }
                : prev,
            );
          })
          .catch((err) => console.warn('查询双链目标失败:', err));
        return true;
      },
    },
  });

  // 保存时刻的正文取值器：编辑器就绪后从 TipTap 惰性序列化 Markdown
  useEffect(() => {
    if (!editor) return;
    getMarkdownRef.current = () => {
      try {
        if (editor.isDestroyed) return null;
        return (
          (editor.storage as { markdown?: { getMarkdown: () => string } }).markdown?.getMarkdown() ??
          null
        );
      } catch {
        return null;
      }
    };
    return () => {
      getMarkdownRef.current = () => null;
    };
  }, [editor]);

  // 播种调度器（合并自原 sync 回调 + 全文加载两个 effect）：
  // 等"协作已同步"与"全文已到达"两个条件都满足后，仅当编辑器仍为空时注入全文。
  // 修复了旧实现中"sync 先到会置位单旗、导致后到的全文永不注入"的时序漏洞。
  useEffect(() => {
    if (!editor) return;
    let cancelled = false;
    let synced = false;
    let gotFull = false;
    let fullText: string | null = null;

    const trySeed = () => {
      if (cancelled || seededRef.current) return;
      if (!synced || !gotFull) return;
      seededRef.current = true;
      if (editor.isEmpty && fullText) {
        editor.commands.setContent(fullText);
      }
    };

    const onSync = (isSynced: boolean) => {
      if (!isSynced) return;
      synced = true;
      trySeed();
    };
    provider.on('sync', onSync);

    getDocument(note.id)
      .then((doc) => {
        if (cancelled) return;
        fullText = doc.content || '';
        fullContentRef.current = fullText;
        contentRef.current = fullText;
        gotFull = true;
        trySeed();
      })
      .catch((err) => console.warn('加载笔记全文失败:', err));

    return () => {
      cancelled = true;
      provider.off('sync', onSync);
    };
  }, [provider, editor, note.id]);

  // 失焦强制落盘：仅在确有未保存修改时发请求
  useEffect(() => {
    if (!editor) return;
    const onBlur = () => {
      void flushSave();
    };
    editor.on('blur', onBlur);
    return () => {
      editor.off('blur', onBlur);
    };
  }, [editor, flushSave]);

  // [[ 双链联想：检测光标前是否有未闭合的 [[
  useEffect(() => {
    if (!editor) return;
    const checkMention = () => {
      const { state, view } = editor;
      const pos = state.selection.from;
      const textBefore = state.doc.textBetween(Math.max(0, pos - 40), pos, '\n', '\0');
      const m = textBefore.match(/\[\[([^\[\]\n]*)$/);
      const query = m?.[1];
      if (!m || query === undefined) {
        setMention(null);
        return;
      }
      let top = 80;
      let left = 24;
      try {
        const coords = view.coordsAtPos(pos - query.length);
        const container = editorContainerRef.current?.getBoundingClientRect();
        if (container) {
          top = coords.top - container.top;
          left = coords.left - container.left;
        }
      } catch {
        /* 回退默认位置 */
      }
      setMention({ query, top, left });
    };
    editor.on('update', checkMention);
    return () => {
      editor.off('update', checkMention);
    };
  }, [editor]);

  // 斜杠命令：检测光标前是否为「空格+ /词」
  useEffect(() => {
    if (!editor) return;
    const checkSlash = () => {
      const { state, view } = editor;
      const pos = state.selection.from;
      if (pos === 0) {
        setSlash(null);
        return;
      }
      const textBefore = state.doc.textBetween(Math.max(0, pos - 30), pos, '\n', '\0');
      const m = textBefore.match(/(?:^|\s)\/([^\s/]*)$/);
      const slashQuery = m?.[1];
      if (!m || slashQuery === undefined || slashQuery.length > 20) {
        setSlash(null);
        return;
      }
      let top = 80;
      let left = 24;
      try {
        const coords = view.coordsAtPos(pos - slashQuery.length - 1);
        const container = editorContainerRef.current?.getBoundingClientRect();
        if (container) {
          top = coords.top - container.top;
          left = coords.left - container.left;
        }
      } catch {
        /* 回退默认位置 */
      }
      setSlash((prev) =>
        prev && prev.query === slashQuery && prev.top === top && prev.left === left
          ? prev
          : { query: slashQuery, top, left, index: 0 },
      );
    };
    editor.on('update', checkSlash);
    editor.on('selectionUpdate', checkSlash);
    return () => {
      editor.off('update', checkSlash);
      editor.off('selectionUpdate', checkSlash);
    };
  }, [editor]);

  const slashItems = useMemo(() => {
    if (!slash) return [];
    const q = slash.query.toLowerCase();
    return SLASH_ITEMS.filter(
      (i) => !q || i.label.toLowerCase().includes(q) || i.hint.toLowerCase().includes(q),
    );
  }, [slash]);

  const applySlash = (index?: number) => {
    if (!editor || !slash) return;
    const item = slashItems[Math.min(index ?? slash.index, slashItems.length - 1)];
    if (!item) return;
    const pos = editor.state.selection.from;
    const start = pos - slash.query.length - 1;
    if (start >= 0) {
      editor.chain().focus().deleteRange({ from: start, to: pos }).run();
    }
    item.apply(editor);
    setSlash(null);
  };

  useEffect(() => {
    if (!slash) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.isComposing) return;
      if (e.key === 'Escape') {
        e.preventDefault();
        // 仅关闭菜单，保留用户已输入的文本（此前会删掉「/词」，违反直觉）
        setSlash(null);
        return;
      }
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSlash((s) => (s ? { ...s, index: Math.min(s.index + 1, slashItems.length - 1) } : s));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSlash((s) => (s ? { ...s, index: Math.max(s.index - 1, 0) } : s));
        return;
      }
      if (e.key === 'Enter' && slashItems.length > 0) {
        e.preventDefault();
        applySlash();
        return;
      }
      if (e.key === 'Backspace' && slash.query.length === 0) {
        setSlash(null);
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editor, slash, slashItems]);

  const mentionCandidates = useMemo(() => {
    if (!mention) return [];
    const q = mention.query.trim().toLowerCase();
    return allTitles
      .filter((t) => !q || t.title.toLowerCase().includes(q))
      .slice(0, 8);
  }, [mention, allTitles]);

  const insertMention = (title: string) => {
    if (!editor || !mention) return;
    editor.chain().focus().insertContent(`${title}]]`).run();
    setMention(null);
  };

  const updateTitle = (value: string) => {
    setTitle(value);
    titleRef.current = value;
    scheduleSave();
  };

  const [workflowState, setWorkflowState] = useState<string>(
    (note.meta?.workflow || 'inbox'),
  );

  const updateWorkflow = async (workflow: 'inbox' | 'review' | 'done') => {
    const prev = workflowState;
    setWorkflowState(workflow); // 乐观更新：徽章立即反映选择
    try {
      await patchDocument(note.id, { meta: { ...(note.meta || {}), workflow } });
    } catch (err) {
      setWorkflowState(prev); // 失败回滚
      toast.error('更新工作流失败：' + (err as Error).message);
    }
  };

  const addSuggestedTag = (tag: string) => {
    if (!tag || tags.includes(tag)) return;
    const next = [...tags, tag];
    setTags(next);
    tagsRef.current = next;
    scheduleSave();
  };

  const addTag = () => {
    const t = newTag.trim();
    if (!t || tags.includes(t)) return;
    const next = [...tags, t];
    setTags(next);
    tagsRef.current = next;
    setNewTag('');
    scheduleSave();
  };

  const removeTag = (t: string) => {
    const next = tags.filter((x) => x !== t);
    setTags(next);
    tagsRef.current = next;
    scheduleSave();
  };

  const insertCitation = useCallback(async (hit: AnnotationHit) => {
    const quoteText = hit.quote || hit.content || '未命名标注';
    const html = `<blockquote><p>「${quoteText.replace(/</g, '&lt;').replace(/>/g, '&gt;')}」—— 《${hit.document_title}》</p></blockquote><p></p>`;
    editor?.chain().focus().insertContent(html).run();
    try {
      await createLink(note.id, hit.document_id, 'cite');
    } catch (err) {
      console.warn('创建 cite 链接失败:', err);
    }
    setShowCite(false);
  }, [editor, note.id]);

  const handleRestored = useCallback(
    (restored: ApiDocument) => {
      setTitle(restored.title);
      titleRef.current = restored.title;
      contentRef.current = restored.content || '';
      editor?.commands.setContent(restored.content || '');
      setSaveState('saved');
    },
    [editor],
  );

  // 返回列表前先等在途/待触发的保存落地：父级刷新列表时能读到最新内容
  const handleBack = useCallback(async () => {
    try {
      await flushSave();
    } catch {
      // flush 失败不阻塞返回（离线场景由队列兜底）
    }
    onBack();
  }, [flushSave, onBack]);

  const workflow = workflowState;
  const setWorkflow = (next: 'inbox' | 'review' | 'done') => {
    void updateWorkflow(next);
  };

  return (
    <main className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
      <div className="flex items-center justify-between mb-4 gap-3 flex-wrap">
        <button onClick={() => void handleBack()} className="flex items-center gap-2 text-gray-600 hover:text-gray-900 transition-colors">
          <ArrowLeft className="w-4 h-4" />
          返回列表
        </button>
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs px-2 py-1 rounded-full bg-gray-100 text-gray-600">
            工作流：{workflow === 'inbox' ? '待整理' : workflow === 'review' ? '复盘中' : '已归档'}
          </span>
          <button onClick={() => setWorkflow('inbox')} className="text-xs px-2 py-1 rounded-full bg-amber-50 text-amber-700 hover:bg-amber-100 transition-colors">待整理</button>
          <button onClick={() => setWorkflow('review')} className="text-xs px-2 py-1 rounded-full bg-blue-50 text-blue-700 hover:bg-blue-100 transition-colors">复盘中</button>
          <button onClick={() => setWorkflow('done')} className="text-xs px-2 py-1 rounded-full bg-emerald-50 text-emerald-700 hover:bg-emerald-100 transition-colors">已归档</button>
        </div>
        <div className="flex items-center gap-3">
          <span className="text-xs text-gray-400">
            {saveState === 'saving' ? '保存中...' : saveState === 'dirty' ? '未保存' : '已保存'}
          </span>
          <span
            className={`flex items-center gap-1 text-xs ${collabStatus === 'connected' ? 'text-emerald-600' : collabStatus === 'connecting' ? 'text-amber-500' : 'text-gray-400'}`}
            title={collabStatus === 'connected' ? '实时协作已连接' : collabStatus === 'connecting' ? '协作连接中...' : '协作离线（本地编辑仍可保存）'}
          >
            {collabStatus === 'offline' ? <WifiOff className="w-3.5 h-3.5" /> : <Wifi className="w-3.5 h-3.5" />}
            {collabStatus === 'connected' ? '已连接' : collabStatus === 'connecting' ? '连接中' : '离线'}
          </span>
          {collabStatus === 'connected' && onlineUsers.length > 0 && (
            <span
              className="flex items-center gap-1 text-xs text-primary-600"
              title={onlineUsers.map((u) => u.name).join('、')}
            >
              <Users className="w-3.5 h-3.5" />
              {onlineUsers.length} 人在线
            </span>
          )}
          <button
            onClick={() => setShowCite(true)}
            className="flex items-center gap-1 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
            title="从书籍标注中插入引用"
          >
            <BookMarked className="w-4 h-4" />
            引用书籍
          </button>
          <button
            onClick={() => setShowHistory(true)}
            className="flex items-center gap-1 px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
            title="版本历史"
          >
            <History className="w-4 h-4" />
            历史
          </button>
          <button
            onClick={onDelete}
            className="flex items-center gap-1 px-3 py-1.5 text-sm text-red-600 hover:bg-red-50 rounded-lg transition-colors"
          >
            <Trash2 className="w-4 h-4" />
            删除
          </button>
        </div>
      </div>

      <input
        type="text"
        value={title}
        onChange={(e) => updateTitle(e.target.value)}
        placeholder="笔记标题"
        className="w-full text-2xl font-bold text-gray-900 px-0 py-2 border-none focus:outline-none bg-transparent"
      />

      {insight && (
        <div className="mb-4 rounded-xl border border-primary-100 bg-primary-50/60 p-4">
          <div className="flex items-center gap-2 text-sm font-medium text-primary-700 mb-2">
            <Sparkles className="w-4 h-4" />
            自动摘要与推荐标签
          </div>
          <p className="text-sm text-gray-700 leading-relaxed">{insight.summary}</p>
          {insight.suggested_tags.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap mt-3">
              {insight.suggested_tags.map((tag) => (
                <button
                  key={tag}
                  onClick={() => addSuggestedTag(tag)}
                  className="px-2.5 py-1 rounded-full text-xs bg-white border border-primary-100 text-primary-700 hover:bg-primary-100 transition-colors"
                  title="点击添加到标签"
                >
                  + {tag}
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="flex items-center gap-2 flex-wrap my-3">
        {tags.map((t) => (
          <span key={t} className="inline-flex items-center gap-1 rounded-full bg-primary-50 px-2 py-1 text-sm text-primary-700">
            {t}
            <button onClick={() => removeTag(t)} className="hover:text-primary-900">×</button>
          </span>
        ))}
        <input
          type="text"
          value={newTag}
          onChange={(e) => setNewTag(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && (e.preventDefault(), addTag())}
          placeholder="+ 标签"
          className="text-sm border-none bg-transparent focus:outline-none w-24 text-gray-600"
        />
      </div>

      {editor && <EditorToolbar editor={editor} />}

      <div ref={editorContainerRef} className="relative bg-white rounded-lg border border-gray-200 p-6">
        <EditorContent editor={editor} />

        {/* [[ 联想浮层 */}
        {mention && (
          <MentionMenu state={mention} candidates={mentionCandidates} onSelect={insertMention} />
        )}

        {/* 斜杠命令浮层 */}
        {slash && (
          <SlashMenu
            state={slash}
            items={slashItems}
            onHover={(index) => setSlash((s) => (s ? { ...s, index } : s))}
            onApply={(index) => applySlash(index)}
          />
        )}
      </div>

      <RelationPanels
        noteTitle={note.title}
        backlinks={backlinks}
        suggestedLinks={suggestedLinks}
        unlinked={unlinked}
        onOpenNote={onOpenNote}
        onLinkUnlinked={(id) => void handleLinkUnlinked(id)}
      />

      {/* [[双链]] 悬浮预览卡片 */}
      {wikilinkPreview && (
        <div
          className="fixed z-[70] w-72 rounded-lg border border-gray-200 bg-white shadow-xl"
          style={{ left: Math.min(wikilinkPreview.x, window.innerWidth - 300), top: wikilinkPreview.y }}
          onMouseLeave={() => setWikilinkPreview(null)}
          data-wikilink-preview
        >
          <div className="px-3 py-2.5">
            <p className="text-sm font-medium text-gray-900 truncate">
              {wikilinkPreview.doc ? wikilinkPreview.doc.title : wikilinkPreview.title}
            </p>
            {wikilinkPreview.doc ? (
              <>
                <p className="text-xs text-gray-400 mt-0.5">
                  {wikilinkPreview.doc.type === 'note' ? '笔记' : wikilinkPreview.doc.type}
                </p>
                {wikilinkPreview.doc.snippet && (
                  <p className="text-xs text-gray-500 mt-1 line-clamp-3">{wikilinkPreview.doc.snippet}</p>
                )}
                <button
                  onClick={() => {
                    if (wikilinkPreview.doc) onOpenNote(wikilinkPreview.doc.id);
                    setWikilinkPreview(null);
                  }}
                  className="mt-2 text-xs text-primary-600 hover:underline"
                >
                  打开文档 →
                </button>
              </>
            ) : (
              <p className="text-xs text-gray-400 mt-0.5">目标文档不存在（占位提及）</p>
            )}
          </div>
        </div>
      )}

      {/* 版本历史抽屉 */}
      {showHistory && (
        <HistoryDrawer
          docId={note.id}
          onClose={() => setShowHistory(false)}
          onRestored={handleRestored}
          beforeRestore={flushSave}
        />
      )}

      {/* 书籍引用弹窗 */}
      {showCite && <CitationDialog onClose={() => setShowCite(false)} onInsert={(hit) => void insertCitation(hit)} />}
    </main>
  );
}
