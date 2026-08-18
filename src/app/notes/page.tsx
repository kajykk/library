'use client';

import { Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useSearchParams, useRouter } from 'next/navigation';
import { useEditor, EditorContent, Extension } from '@tiptap/react';
import StarterKit from '@tiptap/starter-kit';
import { Markdown } from 'tiptap-markdown';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useVirtualizer } from '@tanstack/react-virtual';
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import Collaboration from '@tiptap/extension-collaboration';
import { Plugin, PluginKey } from '@tiptap/pm/state';
import { Decoration, DecorationSet } from '@tiptap/pm/view';
import {
  NotebookPen,
  Plus,
  Trash2,
  ArrowLeft,
  Bold,
  Italic,
  Heading2,
  List,
  ListOrdered,
  Code,
  Quote,
  CalendarDays,
  Link2,
  History,
  BookMarked,
  BookOpen,
  X,
  RotateCcw,
  Loader2,
  ChevronDown,
  Wifi,
  WifiOff,
  Users,
} from 'lucide-react';
import { ApiDocument, listDocuments, patchDocument, createDocument, deleteDocument, setDocumentTags, getBacklinks, getUnlinkedMentions, createLink, getVersions, getVersion, restoreVersion, searchAnnotations, getCollabWsUrl, getApiToken, downloadMarkdownExport, AnnotationHit, DocumentVersionSummary, DocumentVersionDetail, Backlink, DocumentPatch } from '@/lib/api/client';
import { resolveMode } from '@/lib/dataSource';

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

// ---------- [[双链]] 装饰 + 悬浮预览 ----------

const wikilinkKey = new PluginKey('kb-wikilink');

function wikilinkDecorations(doc: any): DecorationSet {
  const decorations: Decoration[] = [];
  doc.descendants((node: { isText: boolean; text?: string }, pos: number) => {
    if (!node.isText || !node.text) return;
    const regex = /\[\[([^\[\]\n]+)\]\]/g;
    let m: RegExpExecArray | null;
    while ((m = regex.exec(node.text)) !== null) {
      decorations.push(
        Decoration.inline(pos + m.index, pos + m.index + m[0].length, {
          nodeName: 'span',
          class: 'kb-wikilink',
          'data-title': m[1],
        }),
      );
    }
  });
  return DecorationSet.create(doc, decorations);
}

const WikiLinkExtension = Extension.create({
  name: 'wikilink',
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: wikilinkKey,
        state: {
          init: (_, state) => wikilinkDecorations(state.doc),
          apply: (tr, old) => (tr.docChanged ? wikilinkDecorations(tr.doc) : old),
        },
        props: {
          decorations(state) {
            return this.getState(state);
          },
        },
      }),
    ];
  },
});

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
  const searchParams = useSearchParams();
  const queryClient = useQueryClient();
  const listParentRef = useRef<HTMLDivElement>(null);

  const notesQuery = useQuery({
    queryKey: ['documents', listType],
    queryFn: () => listDocuments({ type: listType }),
    enabled: mode === 'api',
  });
  const notes = notesQuery.data ?? [];
  const isLoading = notesQuery.isLoading;

  const rowVirtualizer = useVirtualizer({
    count: listType === 'note' ? notes.length : 0,
    getScrollElement: () => listParentRef.current,
    estimateSize: () => 96,
    overscan: 6,
  });

  useEffect(() => {
    resolveMode().then(setMode);
  }, []);

  const refetchNotes = () => queryClient.invalidateQueries({ queryKey: ['documents', listType] });

  // 支持 /notes?open=<id> 直达编辑
  useEffect(() => {
    const openId = searchParams.get('open');
    if (openId && mode === 'api') {
      listDocuments({ q: '' })
        .then((docs) => {
          if (docs.some((d) => d.id === openId)) setEditingId(openId);
        })
        .catch(() => undefined);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchParams, mode]);

  const handleCreate = async (template?: typeof NOTE_TEMPLATES[number]) => {
    setShowTemplateMenu(false);
    const content = template
      ? template.content(new Date().toLocaleDateString('zh-CN'))
      : '';
    const doc = await createDocument({
      type: 'note',
      title: template ? template.name : '无标题笔记',
      content,
    });
    refetchNotes();
    setEditingId(doc.id);
  };

  const handleDailyNote = async () => {
    const now = new Date();
    const title = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
    let existing = notes.find((n) => n.title === title);
    if (!existing) {
      const all = await listDocuments();
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
      <div className="flex items-center justify-between mb-4">
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
          <div className="flex items-center gap-2">
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
            <div className="relative">
              <button
                onClick={() => setShowTemplateMenu((v) => !v)}
                className="flex items-center gap-2 px-4 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
              >
                <Plus className="w-4 h-4" />
                新建笔记
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
        )}
      </div>

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
        <div
          ref={listParentRef}
          className="h-[calc(100vh-220px)] overflow-y-auto"
        >
          <div
            className="relative w-full"
            style={{ height: `${rowVirtualizer.getTotalSize()}px` }}
          >
            {rowVirtualizer.getVirtualItems().map((virtualRow) => {
              const note = notes[virtualRow.index];
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
                        <div className="flex items-center gap-2 mt-2">
                          <span className="text-xs text-gray-400">
                            {new Date(note.updated_at).toLocaleString('zh-CN')}
                          </span>
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

function NoteEditor({
  note,
  onBack,
  onDelete,
  onOpenNote,
}: {
  note: ApiDocument;
  onBack: () => void;
  onDelete: () => void;
  onOpenNote: (id: string) => void;
}) {
  const router = useRouter();
  const [title, setTitle] = useState(note.title);
  const [tags, setTags] = useState(note.tags.map((t) => t.name));
  const [newTag, setNewTag] = useState('');
  const [saveState, setSaveState] = useState<'saved' | 'saving' | 'dirty'>('saved');
  const [mention, setMention] = useState<{ query: string; top: number; left: number } | null>(null);
  const [allTitles, setAllTitles] = useState<Array<{ id: string; title: string }>>([]);
  const [backlinks, setBacklinks] = useState<Backlink[]>([]);
  const [unlinked, setUnlinked] = useState<Backlink[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [versions, setVersions] = useState<DocumentVersionSummary[]>([]);
  const [viewingVersion, setViewingVersion] = useState<DocumentVersionDetail | null>(null);
  const [showCite, setShowCite] = useState(false);
  const [citeQuery, setCiteQuery] = useState('');
  const [citeHits, setCiteHits] = useState<AnnotationHit[]>([]);
  const [citeLoading, setCiteLoading] = useState(false);
  const citeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const saveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const contentRef = useRef(note.content || '');
  const titleRef = useRef(note.title);
  const tagsRef = useRef(tags);
  const editorContainerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    listDocuments()
      .then((docs) => setAllTitles(docs.filter((d) => d.id !== note.id).map((d) => ({ id: d.id, title: d.title }))))
      .catch(() => undefined);
    getBacklinks(note.id).then(setBacklinks).catch(() => undefined);
    getUnlinkedMentions(note.id).then(setUnlinked).catch(() => undefined);
  }, [note.id]);

  const handleLinkUnlinked = async (sourceId: string) => {
    try {
      await createLink(sourceId, note.id, 'mention');
      setUnlinked((prev) => prev.filter((u) => u.id !== sourceId));
      getBacklinks(note.id).then(setBacklinks).catch(() => undefined);
    } catch (err) {
      alert('创建链接失败：' + (err as Error).message);
    }
  };

  const openHistory = async () => {
    setShowHistory(true);
    try {
      setVersions(await getVersions(note.id));
    } catch (err) {
      console.error('加载版本历史失败:', err);
    }
  };

  const openVersionDetail = async (versionId: string) => {
    try {
      setViewingVersion(await getVersion(note.id, versionId));
    } catch (err) {
      alert('加载版本失败：' + (err as Error).message);
    }
  };

  const handleRestore = async () => {
    if (!viewingVersion) return;
    if (!confirm('将文档内容恢复到此版本？恢复前会自动保存当前版本。')) return;
    try {
      const restored = await restoreVersion(note.id, viewingVersion.id);
      setTitle(restored.title);
      titleRef.current = restored.title;
      contentRef.current = restored.content || '';
      editor?.commands.setContent(restored.content || '');
      setViewingVersion(null);
      setVersions(await getVersions(note.id));
      setSaveState('saved');
    } catch (err) {
      alert('恢复失败：' + (err as Error).message);
    }
  };

  useEffect(() => {
    if (!showCite) return;
    setCiteLoading(true);
    searchAnnotations(citeQuery)
      .then(setCiteHits)
      .catch(() => setCiteHits([]))
      .finally(() => setCiteLoading(false));
    return () => undefined;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showCite]);

  useEffect(() => {
    if (!showCite) return;
    if (citeTimer.current) clearTimeout(citeTimer.current);
    citeTimer.current = setTimeout(async () => {
      setCiteLoading(true);
      try {
        setCiteHits(await searchAnnotations(citeQuery));
      } catch {
        setCiteHits([]);
      } finally {
        setCiteLoading(false);
      }
    }, 300);
    return () => {
      if (citeTimer.current) clearTimeout(citeTimer.current);
    };
  }, [citeQuery, showCite]);

  const insertCitation = async (hit: AnnotationHit) => {
    const quoteText = hit.quote || hit.content || '未命名标注';
    const html = `<blockquote><p>「${quoteText.replace(/</g, '&lt;').replace(/>/g, '&gt;')}」—— 《${hit.document_title}》</p></blockquote><p></p>`;
    editor?.chain().focus().insertContent(html).run();
    try {
      await createLink(note.id, hit.document_id, 'cite');
    } catch (err) {
      console.warn('创建 cite 链接失败:', err);
    }
    setShowCite(false);
    setCiteQuery('');
  };

  const scheduleSave = useCallback(() => {
    setSaveState('dirty');
    if (saveTimer.current) clearTimeout(saveTimer.current);
    saveTimer.current = setTimeout(async () => {
      setSaveState('saving');
      try {
        const patch: DocumentPatch = { title: titleRef.current || '无标题笔记', content: contentRef.current };
        await patchDocument(note.id, patch);
        if (tagsRef.current !== note.tags.map((t) => t.name)) {
          await setDocumentTags(note.id, tagsRef.current);
        }
        setSaveState('saved');
      } catch (err) {
        console.error('自动保存失败:', err);
        setSaveState('dirty');
      }
    }, 800);
  }, [note.id, note.tags]);

  // 实时协作（Yjs + 后端 WebSocket）
  const [collabStatus, setCollabStatus] = useState<'connecting' | 'connected' | 'offline'>('connecting');
  const [onlineUsers, setOnlineUsers] = useState<{ clientId: number; name: string }[]>([]);
  const collabRef = useRef<{ ydoc: Y.Doc; provider: WebsocketProvider; seeded: boolean } | null>(null);
  if (!collabRef.current) {
    const ydoc = new Y.Doc();
    const provider = new WebsocketProvider(getCollabWsUrl(note.id), `doc-${note.id}`, ydoc, {
      connect: true,
      params: { token: getApiToken() },
    });
    collabRef.current = { ydoc, provider, seeded: false };
  }

  const [wikilinkPreview, setWikilinkPreview] = useState<{
    title: string;
    x: number;
    y: number;
    doc: { id: string; title: string; type: string; snippet: string } | null;
  } | null>(null);

  const editor = useEditor({
    extensions: [
      StarterKit,
      Markdown.configure({ html: false, transformPastedText: true }),
      WikiLinkExtension,
      Collaboration.configure({ document: collabRef.current.ydoc }),
    ],
    content: '',
    onUpdate: ({ editor }) => {
      contentRef.current = (editor.storage as { markdown?: { getMarkdown: () => string } }).markdown?.getMarkdown() ?? '';
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
        const rect = target.getBoundingClientRect();
        setWikilinkPreview({ title, x: rect.left, y: rect.bottom + 6, doc: null });
        listDocuments({ q: title })
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
          .catch(() => undefined);
        return true;
      },
    },
  });

  // 协作：状态指示 + 在线成员（awareness）+ 本地内容种子
  useEffect(() => {
    const { provider } = collabRef.current as { provider: WebsocketProvider };
    const onStatus = ({ status }: { status: string }) => {
      setCollabStatus(status === 'connected' ? 'connected' : status === 'connecting' ? 'connecting' : 'offline');
    };
    const onSync = (isSynced: boolean) => {
      if (!isSynced) return;
      const collab = collabRef.current;
      if (!collab || collab.seeded) return;
      collab.seeded = true;
      if (editor && editor.isEmpty && note.content) {
        editor.commands.setContent(note.content);
      }
    };
    const refreshUsers = () => {
      const awareness = provider.awareness;
      const myId = awareness.clientID;
      const users: { clientId: number; name: string }[] = [];
      awareness.getStates().forEach((state, clientId) => {
        if (clientId === myId) return;
        const user = (state as Record<string, { name?: string }>)?.user;
        users.push({ clientId, name: user?.name || `用户 ${String(clientId).slice(-4)}` });
      });
      setOnlineUsers(users);
    };
    provider.awareness.setLocalStateField('user', {
      name: typeof window !== 'undefined' ? window.localStorage.getItem('kb_user_name') || '未命名用户' : '未命名用户',
    });
    provider.awareness.on('change', refreshUsers);
    refreshUsers();
    provider.on('status', onStatus);
    provider.on('sync', onSync);
    return () => {
      provider.awareness.off('change', refreshUsers);
      provider.off('status', onStatus);
      provider.off('sync', onSync);
    };
  }, [editor, note.content]);

  useEffect(() => {
    return () => {
      const collab = collabRef.current;
      if (collab) {
        collab.provider.destroy();
        collab.ydoc.destroy();
        collabRef.current = null;
      }
    };
  }, []);

  // [[ 双链联想：检测光标前是否有未闭合的 [[
  useEffect(() => {
    if (!editor) return;
    const checkMention = () => {
      const { state, view } = editor;
      const pos = state.selection.from;
      const textBefore = state.doc.textBetween(Math.max(0, pos - 40), pos, '\n', '\0');
      const m = textBefore.match(/\[\[([^\[\]\n]*)$/);
      if (!m) {
        setMention(null);
        return;
      }
      let top = 80;
      let left = 24;
      try {
        const coords = view.coordsAtPos(pos - m[1].length);
        const container = editorContainerRef.current?.getBoundingClientRect();
        if (container) {
          top = coords.top - container.top;
          left = coords.left - container.left;
        }
      } catch {
        /* 回退默认位置 */
      }
      setMention({ query: m[1], top, left });
    };
    editor.on('update', checkMention);
    return () => {
      editor.off('update', checkMention);
    };
  }, [editor]);

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

  useEffect(() => {
    return () => {
      if (saveTimer.current) clearTimeout(saveTimer.current);
      // 卸载时若还有未落盘的修改，补发一次保存，避免快速返回丢标题/正文
      patchDocument(note.id, {
        title: titleRef.current || '无标题笔记',
        content: contentRef.current,
      }).catch((err) => console.error('卸载保存失败:', err));
    };
  }, [note.id]);

  const updateTitle = (value: string) => {
    setTitle(value);
    titleRef.current = value;
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

  const btn = (active: boolean) =>
    `p-1.5 rounded transition-colors ${active ? 'bg-gray-200 text-gray-900' : 'text-gray-500 hover:bg-gray-100'}`;

  return (
    <main className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 py-6">
      <div className="flex items-center justify-between mb-4">
        <button onClick={onBack} className="flex items-center gap-2 text-gray-600 hover:text-gray-900 transition-colors">
          <ArrowLeft className="w-4 h-4" />
          返回列表
        </button>
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
            onClick={openHistory}
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

      {editor && (
        <div className="flex items-center gap-1 border-b border-gray-200 pb-2 mb-4 flex-wrap">
          <button onClick={() => editor.chain().focus().toggleBold().run()} className={btn(editor.isActive('bold'))} title="加粗">
            <Bold className="w-4 h-4" />
          </button>
          <button onClick={() => editor.chain().focus().toggleItalic().run()} className={btn(editor.isActive('italic'))} title="斜体">
            <Italic className="w-4 h-4" />
          </button>
          <button onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()} className={btn(editor.isActive('heading', { level: 2 }))} title="标题">
            <Heading2 className="w-4 h-4" />
          </button>
          <button onClick={() => editor.chain().focus().toggleBulletList().run()} className={btn(editor.isActive('bulletList'))} title="无序列表">
            <List className="w-4 h-4" />
          </button>
          <button onClick={() => editor.chain().focus().toggleOrderedList().run()} className={btn(editor.isActive('orderedList'))} title="有序列表">
            <ListOrdered className="w-4 h-4" />
          </button>
          <button onClick={() => editor.chain().focus().toggleCodeBlock().run()} className={btn(editor.isActive('codeBlock'))} title="代码块">
            <Code className="w-4 h-4" />
          </button>
          <button onClick={() => editor.chain().focus().toggleBlockquote().run()} className={btn(editor.isActive('blockquote'))} title="引用">
            <Quote className="w-4 h-4" />
          </button>
        </div>
      )}

      <div ref={editorContainerRef} className="relative bg-white rounded-lg border border-gray-200 p-6">
        <EditorContent editor={editor} />

        {/* [[ 联想浮层 */}
        {mention && mentionCandidates.length > 0 && (
          <div
            className="absolute z-20 w-64 rounded-lg border border-gray-200 bg-white shadow-lg overflow-hidden"
            style={{ top: mention.top + 24, left: mention.left }}
          >
            {mentionCandidates.map((c) => (
              <button
                key={c.id}
                onClick={() => insertMention(c.title)}
                className="block w-full text-left px-3 py-2 text-sm text-gray-700 hover:bg-primary-50 truncate"
              >
                {c.title}
              </button>
            ))}
          </div>
        )}
      </div>

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
      {unlinked.length > 0 && (
        <div className="mt-6">
          <h3 className="text-sm font-medium text-gray-700 mb-2">
            未链接提及（{unlinked.length}）
          </h3>
          <p className="text-xs text-gray-400 mb-2">
            以下文档提到了「{note.title}」但尚未建立链接
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
                  onClick={() => handleLinkUnlinked(u.id)}
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

      {/* [[双链]] 悬浮预览卡片 */}
      {wikilinkPreview && (
        <div
          className="fixed z-[70] w-72 rounded-lg border border-gray-200 bg-white shadow-xl"
          style={{ left: Math.min(wikilinkPreview.x, window.innerWidth - 300), top: wikilinkPreview.y }}
          onMouseLeave={() => setWikilinkPreview(null)}
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
        <div className="fixed inset-0 z-50">
          <div className="absolute inset-0 bg-slate-900/30" onClick={() => setShowHistory(false)} />
          <aside className="absolute right-0 top-0 h-full w-80 bg-white shadow-2xl flex flex-col">
            <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
              <h3 className="text-sm font-medium text-gray-900">版本历史（{versions.length}）</h3>
              <button onClick={() => setShowHistory(false)} className="p-1 hover:bg-gray-100 rounded">
                <X className="w-4 h-4 text-gray-500" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto p-2 space-y-1">
              {versions.length === 0 ? (
                <p className="text-center text-sm text-gray-400 py-8">暂无历史版本</p>
              ) : (
                versions.map((v) => (
                  <button
                    key={v.id}
                    onClick={() => openVersionDetail(v.id)}
                    className="block w-full text-left px-3 py-2 rounded-lg hover:bg-gray-50 transition-colors"
                  >
                    <p className="text-xs text-gray-500">
                      {new Date(v.created_at).toLocaleString('zh-CN')}
                    </p>
                    <p className="text-sm text-gray-700 truncate">{v.title}</p>
                    <p className="text-xs text-gray-400 truncate">{v.preview || '（空）'}</p>
                  </button>
                ))
              )}
            </div>
          </aside>
        </div>
      )}

      {/* 版本预览/恢复弹窗 */}
      {viewingVersion && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center p-4">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setViewingVersion(null)} />
          <div className="relative w-full max-w-2xl max-h-[80vh] bg-white rounded-xl shadow-2xl flex flex-col">
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
              <div>
                <h3 className="text-sm font-medium text-gray-900">{viewingVersion.title}</h3>
                <p className="text-xs text-gray-400">
                  {new Date(viewingVersion.created_at).toLocaleString('zh-CN')}
                </p>
              </div>
              <button onClick={() => setViewingVersion(null)} className="p-1 hover:bg-gray-100 rounded">
                <X className="w-4 h-4 text-gray-500" />
              </button>
            </div>
            <div className="flex-1 overflow-y-auto px-5 py-4">
              <pre className="whitespace-pre-wrap font-sans text-sm text-gray-700 leading-relaxed">
                {viewingVersion.content || '（空）'}
              </pre>
            </div>
            <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-200">
              <button
                onClick={() => setViewingVersion(null)}
                className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
              >
                关闭
              </button>
              <button
                onClick={handleRestore}
                className="flex items-center gap-1 px-3 py-1.5 text-sm bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                恢复此版本
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 书籍引用弹窗 */}
      {showCite && (
        <div className="fixed inset-0 z-[60] flex items-start justify-center pt-[12vh] p-4">
          <div className="absolute inset-0 bg-slate-900/40" onClick={() => setShowCite(false)} />
          <div className="relative w-full max-w-xl bg-white rounded-xl shadow-2xl flex flex-col max-h-[70vh]">
            <div className="flex items-center justify-between px-5 py-3 border-b border-gray-200">
              <h3 className="text-sm font-medium text-gray-900">插入书籍标注引用</h3>
              <button onClick={() => setShowCite(false)} className="p-1 hover:bg-gray-100 rounded">
                <X className="w-4 h-4 text-gray-500" />
              </button>
            </div>
            <div className="px-5 py-3 border-b border-gray-100">
              <input
                type="text"
                value={citeQuery}
                onChange={(e) => setCiteQuery(e.target.value)}
                placeholder="搜索划线或批注内容…"
                autoFocus
                className="w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none"
              />
            </div>
            <div className="flex-1 overflow-y-auto p-2">
              {citeLoading ? (
                <div className="flex items-center justify-center gap-2 py-8 text-sm text-gray-400">
                  <Loader2 className="w-4 h-4 animate-spin" /> 搜索中...
                </div>
              ) : citeHits.length === 0 ? (
                <p className="text-center text-sm text-gray-400 py-8">没有找到匹配的标注</p>
              ) : (
                citeHits.map((hit) => {
                  const anchorPos = hit.anchor
                    ? (hit.anchor as { chapter?: number; page?: number }).chapter ??
                      (hit.anchor as { page?: number }).page
                    : undefined;
                  return (
                    <div
                      key={hit.id}
                      className="flex items-start gap-2 px-3 py-2.5 rounded-lg hover:bg-primary-50 transition-colors"
                    >
                      <button
                        onClick={() => insertCitation(hit)}
                        className="block flex-1 min-w-0 text-left"
                      >
                        <p className="text-sm text-gray-800 line-clamp-2">
                          {hit.quote || hit.content || '（无内容）'}
                        </p>
                        <p className="text-xs text-gray-400 mt-1">
                          《{hit.document_title}》 · {hit.type === 'highlight' ? '划线' : hit.type === 'note' ? '批注' : '书签'}
                        </p>
                      </button>
                      {anchorPos !== undefined && hit.document_type !== 'note' && (
                        <button
                          onClick={() => router.push(`/reader/${hit.document_id}?position=${anchorPos}`)}
                          className="shrink-0 self-center flex items-center gap-1 px-2 py-1 text-xs text-primary-600 border border-primary-200 rounded-md hover:bg-primary-50 transition-colors"
                          title="跳转到书中标注位置"
                        >
                          <BookOpen className="w-3 h-3" />
                          跳转
                        </button>
                      )}
                    </div>
                  );
                })
              )}
            </div>
            <div className="px-5 py-2.5 border-t border-gray-100 text-xs text-gray-400">
              插入引用后将自动建立笔记与书籍的「引用」连接
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
