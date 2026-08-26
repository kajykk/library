'use client';

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import DOMPurify from 'dompurify';
import { Book } from '@/lib/db';
import {
  getFileArrayBuffer,
  saveReadingSession,
  listAnnotations,
  addBookmarkAnnotation,
  addNoteAnnotation,
  createHighlight,
  deleteAnnotation as dsDeleteAnnotation,
  UnifiedAnnotation,
} from '@/lib/dataSource';
import {
  parseEpubChapters,
  parseMobiChapters,
  ParsedChapter,
} from '@/lib/parsers/foliate';
import { useDialog, useToast } from '@/components/ui';
import { useReadingProgress } from './reader/hooks/useReadingProgress';
import { useReaderShortcuts } from './reader/hooks/useReaderShortcuts';
import { usePdfDocument } from './reader/hooks/usePdfDocument';
import {
  ChevronLeft,
  ChevronRight,
  BookmarkPlus,
  PanelLeftClose,
  PanelLeftOpen,
  Highlighter,
  Search,
  X,
} from 'lucide-react';
import AnnotationsSidebar from './reader/panels/AnnotationsSidebar';

interface BookReaderProps {
  book: Book;
  onClose: () => void;
  initialPositionOverride?: number;
}

const TXT_LINES_PER_PAGE = 40;

const CHAPTERED_FORMATS = ['epub', 'mobi', 'azw3'];

const isChapteredFormat = (format: string) => CHAPTERED_FORMATS.includes(format);

export default function BookReader({ book, onClose, initialPositionOverride }: BookReaderProps) {
  const dialog = useDialog();
  const toast = useToast();
  const initialPosition =
    initialPositionOverride ?? book.lastPosition ?? (book.format === 'pdf' ? 1 : 0);
  const [currentPage, setCurrentPage] = useState(initialPosition);
  const [totalPages, setTotalPages] = useState(book.totalPages || 100);
  const [fontSize, setFontSize] = useState(16);
  const [showSidebar, setShowSidebar] = useState(false);
  const [sidebarTab, setSidebarTab] = useState<'bookmarks' | 'annotations'>('bookmarks');
  const [annotations, setAnnotations] = useState<UnifiedAnnotation[]>([]);
  const [newNoteContent, setNewNoteContent] = useState('');
  const [showNoteInput, setShowNoteInput] = useState(false);
  const [selectionMenu, setSelectionMenu] = useState<{ x: number; y: number; text: string } | null>(null);
  const [content, setContent] = useState<string>('');
  const [isLoading, setIsLoading] = useState(true);
  const [epubContent, setEpubContent] = useState<ParsedChapter[]>([]);
  const [currentChapter, setCurrentChapter] = useState(0);
  const [showChapterList, setShowChapterList] = useState(false);
  const [scrollMode, setScrollMode] = useState(false);
  const [showBookSearch, setShowBookSearch] = useState(false);
  const [bookSearchQuery, setBookSearchQuery] = useState('');
  const [bookSearchResults, setBookSearchResults] = useState<
    Array<{ chapter: number; title: string; before: string; match: string; after: string }>
  >([]);
  const [searchTarget, setSearchTarget] = useState<{ chapter: number; query: string } | null>(null);

  const contentRef = useRef<HTMLDivElement>(null);
  const txtLinesRef = useRef<string[] | null>(null);
  const readingStartTimeRef = useRef<number>(Date.now());
  const currentPageRef = useRef<number>(initialPosition);
  const initialPositionRef = useRef<number>(initialPosition);
  const scrollSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // PDF 生命周期/页缓存/渲染守卫抽离至 usePdfDocument
  const { load: loadPdfDocument, renderPage: renderPdfPage, destroy: destroyPdf } =
    usePdfDocument(setContent);

  // 进度保存（在途登记 + 退出前 flush）抽离至 useReadingProgress
  const { save: saveProgress, flush: flushProgress } = useReadingProgress(book.id);

  const handleClose = useCallback(async () => {
    // 滚动模式的防抖保存若已排队，先触发再等待
    if (scrollSaveTimerRef.current) {
      clearTimeout(scrollSaveTimerRef.current);
      scrollSaveTimerRef.current = null;
      const idx = currentPageRef.current;
      const progress =
        epubContent.length > 0 ? Math.round(((idx + 1) / epubContent.length) * 100) : 0;
      saveProgress(progress, idx);
    }
    await flushProgress();
    onClose();
  }, [epubContent.length, flushProgress, onClose, saveProgress]);

  useEffect(() => {
    const bookId = book.id;
    const startPosition = initialPositionRef.current;
    loadContent();
    loadAnnotations();
    readingStartTimeRef.current = Date.now();

    return () => {
      // 通过 ref 读取最新值，避免闭包捕获首帧状态导致记录失真
      const start = readingStartTimeRef.current;
      const duration = Math.floor((Date.now() - start) / 60000);
      if (duration > 0) {
        saveReadingSession(
          bookId,
          start,
          Date.now(),
          Math.max(0, currentPageRef.current - startPosition),
        ).catch((err) => console.error('保存阅读记录失败:', err));
      }
      // 释放 pdf.js 资源与页面缓存（多次进出 PDF 阅读器不再叠加泄漏）
      destroyPdf();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    currentPageRef.current = currentPage;
  }, [currentPage]);

  const renderTxtPage = (page: number) => {
    const lines = txtLinesRef.current;
    if (!lines) return;
    const start = page * TXT_LINES_PER_PAGE;
    setContent(lines.slice(start, start + TXT_LINES_PER_PAGE).join('\n'));
  };

  const loadContent = async () => {
    setIsLoading(true);
    try {
      if (isChapteredFormat(book.format)) {
        await loadChapteredContent();
      } else if (book.format === 'txt') {
        const arrayBuffer = await getFileArrayBuffer(book.id);
        if (arrayBuffer) {
          const text = new TextDecoder('utf-8').decode(arrayBuffer);
          const lines = text.split('\n');
          txtLinesRef.current = lines;
          const pages = Math.max(1, Math.ceil(lines.length / TXT_LINES_PER_PAGE));
          setTotalPages(pages);
          const saved = Math.min(Math.max(book.lastPosition ?? 0, 0), pages - 1);
          setCurrentPage(saved);
          renderTxtPage(saved);
        }
      } else if (book.format === 'pdf') {
        await loadPdfContent();
      } else {
        setContent('<p>当前格式 (' + book.format.toUpperCase() + ') 的完整解析正在开发中。</p><p>您可以使用页面导航和书签功能来管理阅读进度。</p>');
      }
    } catch (error) {
      console.error('Failed to load content:', error);
      setContent('<p>内容加载失败</p>');
    }
    setIsLoading(false);
  };

  const loadChapteredContent = async () => {
    const formatLabel = book.format.toUpperCase();
    try {
      const arrayBuffer = await getFileArrayBuffer(book.id);
      if (!arrayBuffer) {
        setContent(`<p>${formatLabel} 文件加载失败</p>`);
        return;
      }

      const chapters = book.format === 'epub'
        ? await parseEpubChapters(arrayBuffer)
        : await parseMobiChapters(arrayBuffer);

      setEpubContent(chapters);
      setTotalPages(Math.max(chapters.length, 1));

      if (chapters.length > 0) {
        const initialIndex = Math.min(Math.max(book.lastPosition ?? 0, 0), chapters.length - 1);
        setCurrentChapter(initialIndex);
        setCurrentPage(initialIndex);
        setContent(chapters[initialIndex]?.content ?? '');
      } else {
        setContent(`<p>${formatLabel} 中未解析到可阅读章节</p>`);
      }
    } catch (error) {
      console.error(`${formatLabel} parsing failed:`, error);
      setContent(`<p>${formatLabel} 解析失败</p>`);
    }
  };

  const loadPdfContent = async () => {
    const arrayBuffer = await getFileArrayBuffer(book.id);
    if (!arrayBuffer) {
      setContent('<p>PDF 文件加载失败</p>');
      return;
    }
    try {
      const numPages = await loadPdfDocument(arrayBuffer);
      setTotalPages(numPages);

      const savedPage = Math.min(Math.max(book.lastPosition ?? 1, 1), numPages);
      setCurrentPage(savedPage);

      await renderPdfPage(savedPage);
    } catch (error) {
      console.error('PDF parsing failed:', error);
      setContent('<p>PDF 解析失败</p>');
    }
  };

  const loadAnnotations = async () => {
    try {
      setAnnotations(await listAnnotations(book.id));
    } catch (err) {
      console.error('加载批注失败:', err);
    }
  };

  /** 划线定位用的"章节"键：EPUB/MOBI/AZW3 为章节索引，TXT 为分页索引 */
  const chapterKey = isChapteredFormat(book.format) ? currentChapter : currentPage;

  const handlePageChange = useCallback(async (newPage: number) => {
    const isPdf = book.format === 'pdf';
    const minPage = isPdf ? 1 : 0;
    const maxPage = isPdf ? totalPages : Math.max(totalPages - 1, 0);
    const target = Math.min(Math.max(Math.round(newPage), minPage), maxPage);
    if (target === currentPage) return;
    setCurrentPage(target);

    if (isChapteredFormat(book.format) && epubContent.length > 0) {
      const chapterIndex = Math.min(target, epubContent.length - 1);
      setCurrentChapter(chapterIndex);
      setContent(epubContent[chapterIndex]?.content ?? '');
    } else if (book.format === 'pdf') {
      await renderPdfPage(target);
    } else if (book.format === 'txt') {
      renderTxtPage(target);
    }

    const progress = totalPages > 0
      ? Math.round(((isPdf ? target : target + 1) / totalPages) * 100)
      : 0;
    await saveProgress(progress, target);
  }, [book.format, currentPage, epubContent, saveProgress, totalPages]);

  const handleAddBookmark = async () => {
    const note = isChapteredFormat(book.format) && epubContent[currentChapter]
      ? epubContent[currentChapter].title
      : `第 ${currentPage} 页`;
    try {
      const created = await addBookmarkAnnotation(book.id, currentPage, chapterKey, note);
      setAnnotations((prev) => [created, ...prev]);
    } catch (err) {
      toast.error('添加书签失败：' + (err as Error).message);
    }
  };

  const handleAddNote = async () => {
    if (!newNoteContent.trim()) return;
    try {
      const created = await addNoteAnnotation(book.id, currentPage, chapterKey, newNoteContent.trim());
      setAnnotations((prev) => [created, ...prev]);
    } catch (err) {
      toast.error('添加笔记失败：' + (err as Error).message);
      return;
    }
    setNewNoteContent('');
    setShowNoteInput(false);
  };

  const handleDeleteAnnotation = async (annotation: UnifiedAnnotation) => {
    try {
      await dsDeleteAnnotation(annotation);
    } catch (err) {
      toast.error('删除失败：' + (err as Error).message);
      return;
    }
    setAnnotations((prev) => prev.filter((a) => a.id !== annotation.id));
    if (annotation.type === 'highlight') {
      unwrapHighlight(annotation.id);
    }
  };

  // ---------- 划线高亮 ----------

  const HIGHLIGHT_SUPPORT = book.format !== 'pdf';

  const applyHighlightMark = (quote: string, annotationId: string, containerEl?: HTMLElement | null): boolean => {
    const container = containerEl ?? contentRef.current;
    if (!container || !quote) return false;
    const walker = document.createTreeWalker(container, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      const textNode = node as Text;
      const idx = textNode.textContent?.indexOf(quote) ?? -1;
      if (idx >= 0) {
        const hit = textNode.splitText(idx);
        hit.splitText(quote.length);
        const mark = document.createElement('mark');
        mark.className = 'kb-highlight';
        mark.dataset.aid = annotationId;
        hit.parentNode?.insertBefore(mark, hit);
        mark.appendChild(hit);
        return true;
      }
      node = walker.nextNode();
    }
    return false;
  };

  const unwrapHighlight = (annotationId: string) => {
    contentRef.current?.querySelectorAll(`mark[data-aid="${annotationId}"]`).forEach((el) => {
      const parent = el.parentNode;
      if (!parent) return;
      while (el.firstChild) parent.insertBefore(el.firstChild, el);
      parent.removeChild(el);
      parent.normalize();
    });
  };

  const handleContentMouseUp = () => {
    if (!HIGHLIGHT_SUPPORT) return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !contentRef.current) {
      setSelectionMenu(null);
      return;
    }
    const range = selection.getRangeAt(0);
    const text = selection.toString().trim();
    if (!text || text.length > 500 || !contentRef.current.contains(range.commonAncestorContainer)) {
      setSelectionMenu(null);
      return;
    }
    const rect = range.getBoundingClientRect();
    setSelectionMenu({ x: rect.left + rect.width / 2, y: rect.top - 8, text });
  };

  const handleCreateHighlight = async (withNote: boolean) => {
    const text = selectionMenu?.text;
    setSelectionMenu(null);
    window.getSelection()?.removeAllRanges();
    if (!text) return;

    let note = '';
    if (withNote) {
      const input = await dialog.prompt({
        title: '添加批注',
        message: '为这条划线补充批注内容（可留空）：',
      });
      if (input === null) return;
      note = input.trim();
    }
    try {
      const created = await createHighlight(book.id, chapterKey, currentPage, text, note);
      setAnnotations((prev) => [created, ...prev]);
      applyHighlightMark(text, created.id);
      toast.success('划线已保存');
    } catch (err) {
      toast.error((err as Error).message);
    }
  };

  // 章节内容渲染完成后恢复当前章节的划线
  useEffect(() => {
    if (isLoading || !content) return;
    annotations
      .filter((a) => a.type === 'highlight' && a.quote && a.chapter === chapterKey)
      .forEach((a) => {
        if (!contentRef.current?.querySelector(`mark[data-aid="${a.id}"]`)) {
          applyHighlightMark(a.quote, a.id);
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [content, isLoading, annotations, chapterKey]);

  // 连续滚动模式：恢复各章节划线
  useEffect(() => {
    if (!scrollMode || isLoading) return;
    annotations
      .filter((a) => a.type === 'highlight' && a.quote)
      .forEach((a) => {
        const section = contentRef.current?.querySelector(
          `section[data-chapter="${a.chapter}"]`,
        ) as HTMLElement | null;
        if (section && !section.querySelector(`mark[data-aid="${a.id}"]`)) {
          applyHighlightMark(a.quote, a.id, section);
        }
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollMode, isLoading, annotations]);

  // ---------- 书内搜索 ----------

  // 章节纯文本缓存：首次搜索时构建一次；epubContent 变化（换书）后自动失效。
  // 避免每次按键都对全书重建 DOM + sanitize（大书直接卡死）。
  const chapterTextsRef = useRef<{ source: ParsedChapter[]; texts: string[] } | null>(null);

  const getChapterTexts = useCallback((): string[] => {
    const cached = chapterTextsRef.current;
    if (cached && cached.source === epubContent) return cached.texts;
    const div = document.createElement('div');
    const texts = epubContent.map((ch) => {
      // 章节内容来自外部 EPUB/MOBI 文件，必须先经 DOMPurify 净化再解析
      div.innerHTML = DOMPurify.sanitize(ch.content);
      return (div.textContent || '').replace(/\s+/g, ' ');
    });
    chapterTextsRef.current = { source: epubContent, texts };
    return texts;
  }, [epubContent]);

  const runBookSearch = useCallback(
    (q: string) => {
      const query = q.trim();
      if (!query) {
        setBookSearchResults([]);
        return;
      }
      const texts = getChapterTexts();
      const lowerQuery = query.toLowerCase();
      const results: Array<{ chapter: number; title: string; before: string; match: string; after: string }> = [];
      for (let i = 0; i < epubContent.length && results.length < 50; i++) {
        const text = texts[i];
        if (!text) continue;
        const lowerText = text.toLowerCase();
        let idx = lowerText.indexOf(lowerQuery);
        // 每章报告全部命中（此前只报第一个）
        while (idx >= 0 && results.length < 50) {
          results.push({
            chapter: i,
            title: epubContent[i]?.title ?? '',
            before: text.slice(Math.max(0, idx - 30), idx),
            match: text.slice(idx, idx + query.length),
            after: text.slice(idx + query.length, idx + query.length + 40),
          });
          idx = lowerText.indexOf(lowerQuery, idx + query.length);
        }
      }
      setBookSearchResults(results);
    },
    [epubContent, getChapterTexts],
  );

  // 输入防抖 250ms：停止输入后再扫描全书
  useEffect(() => {
    if (!showBookSearch) return;
    const timer = setTimeout(() => runBookSearch(bookSearchQuery), 250);
    return () => clearTimeout(timer);
  }, [bookSearchQuery, showBookSearch, runBookSearch]);

  const jumpToSearchResult = (chapter: number, query: string) => {
    setShowBookSearch(false);
    if (scrollMode) {
      setCurrentChapter(chapter);
      setCurrentPage(chapter);
      contentRef.current
        ?.querySelector(`section[data-chapter="${chapter}"]`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      setSearchTarget({ chapter, query });
    } else {
      handlePageChange(chapter);
      setSearchTarget({ chapter, query });
    }
  };

  // 命中章节渲染完成后高亮并滚动到匹配处
  useEffect(() => {
    if (!searchTarget || !contentRef.current) return;
    const timer = setTimeout(() => {
      const scope = scrollMode
        ? contentRef.current?.querySelector(`section[data-chapter="${searchTarget.chapter}"]`) as HTMLElement | null
        : contentRef.current;
      if (!scope) return;
      const targetMark = scope.querySelector('mark[data-aid="search-hit"]');
      if (!targetMark) applyHighlightMark(searchTarget.query, 'search-hit', scope);
      scope
        .querySelector('mark[data-aid="search-hit"]')
        ?.scrollIntoView({ behavior: 'smooth', block: 'center' });
    }, 120);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchTarget, content, scrollMode]);

  // ---------- 连续滚动模式 ----------

  useEffect(() => {
    if (!scrollMode || !isChapteredFormat(book.format) || epubContent.length === 0) return;
    const container = contentRef.current;
    if (!container) return;

    const sections = Array.from(container.querySelectorAll('section[data-chapter]'));
    // 环境防御：极老 webview / 测试环境可能无 IntersectionObserver，
    // 此时仅失去自动进度跟踪，滚动阅读与手动翻页仍可用
    if (typeof IntersectionObserver === 'undefined') return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (!entry.isIntersecting) continue;
          const idx = Number((entry.target as HTMLElement).dataset.chapter);
          if (Number.isNaN(idx)) continue;
          setCurrentChapter(idx);
          setCurrentPage(idx);
          currentPageRef.current = idx;
          if (scrollSaveTimerRef.current) clearTimeout(scrollSaveTimerRef.current);
          scrollSaveTimerRef.current = setTimeout(() => {
            const progress =
              epubContent.length > 0 ? Math.round(((idx + 1) / epubContent.length) * 100) : 0;
            saveProgress(progress, idx);
          }, 1000);
        }
      },
      { root: null, threshold: 0.25 },
    );
    sections.forEach((s) => observer.observe(s));
    return () => observer.disconnect();
  }, [scrollMode, epubContent, book.id, book.format]);

  // 滚动模式初始定位
  useEffect(() => {
    if (!scrollMode || epubContent.length === 0) return;
    const idx = Math.min(Math.max(currentChapter, 0), epubContent.length - 1);
    const timer = setTimeout(() => {
      const section = contentRef.current?.querySelector(`section[data-chapter="${idx}"]`);
      // scrollIntoView 在 jsdom/极老环境可能缺失，可选调用避免抛错
      section?.scrollIntoView?.({ block: 'start' });
    }, 80);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollMode]);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) {
      void document.exitFullscreen();
    } else {
      void document.documentElement.requestFullscreen?.();
    }
  }, []);

  // 快捷键分发抽离至 useReaderShortcuts（Escape 分层关闭 → 翻页/全屏/书内搜索）
  useReaderShortcuts({
    onEscape: () => {
      // 逐层关闭浮层，最后才退出阅读器
      if (showBookSearch) {
        setShowBookSearch(false);
        return true;
      }
      if (showChapterList) {
        setShowChapterList(false);
        return true;
      }
      if (showSidebar) {
        setShowSidebar(false);
        return true;
      }
      return false;
    },
    onClose: () => void handleClose(),
    onPageChange: (delta) => handlePageChange(currentPage + delta),
    onToggleFullscreen: toggleFullscreen,
    onToggleBookSearch: () => {
      if (!isChapteredFormat(book.format)) return false;
      setShowBookSearch((v) => !v);
      if (showBookSearch) setBookSearchQuery('');
      return true;
    },
  });

  // 书籍内容来自外部文件，必须经 DOMPurify 白名单净化，防止恶意 EPUB/MOBI 注入脚本
  const sanitizedHtml = useMemo(
    () => (book.format === 'txt' ? '' : DOMPurify.sanitize(content)),
    [content, book.format]
  );

  // 连续滚动模式：净化结果按 epubContent 记忆化。
  // 此前写在渲染体里，改变字号等任意 setState 都会对全书重新 sanitize + 全量 reconcile。
  const scrollSanitizedChapters = useMemo(
    () => epubContent.map((ch) => ({ href: ch.href, title: ch.title, html: DOMPurify.sanitize(ch.content) })),
    [epubContent],
  );

  const getPageLabel = () => {
    if (isChapteredFormat(book.format) && epubContent[currentChapter]) {
      return epubContent[currentChapter].title;
    }
    return `第 ${currentPage} 页`;
  };

  return (
    <div className="fixed inset-0 bg-white z-50 flex flex-col">
      {/* Reader Header */}
      <header className="flex items-center justify-between px-4 py-3 border-b border-gray-200 bg-white">
        <div className="flex items-center gap-3">
          <button
            onClick={() => void handleClose()}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            title="返回书架"
          >
            <ChevronLeft className="w-5 h-5" />
          </button>
          <div>
            <h2 className="font-medium text-gray-900 truncate max-w-md">{book.title}</h2>
            <p className="text-sm text-gray-500">{book.author}</p>
          </div>
        </div>
        
        <div className="flex items-center gap-2">
          {isChapteredFormat(book.format) && (
            <button
              onClick={() => setShowChapterList(!showChapterList)}
              className="px-3 py-2 hover:bg-gray-100 rounded-lg transition-colors text-sm"
              title="章节列表"
            >
              章节
            </button>
          )}

          {isChapteredFormat(book.format) && (
            <button
              onClick={() => {
                setShowBookSearch((v) => !v);
                if (!showBookSearch) setBookSearchQuery('');
              }}
              className={`p-2 rounded-lg transition-colors ${showBookSearch ? 'bg-primary-50 text-primary-600' : 'hover:bg-gray-100'}`}
              title="书内搜索"
            >
              <Search className="w-5 h-5" />
            </button>
          )}

          {isChapteredFormat(book.format) && (
            <button
              onClick={() => setScrollMode((v) => !v)}
              className={`px-3 py-2 rounded-lg transition-colors text-sm ${scrollMode ? 'bg-primary-50 text-primary-600' : 'hover:bg-gray-100'}`}
              title={scrollMode ? '切换为分页模式' : '切换为连续滚动模式'}
            >
              {scrollMode ? '分页' : '连续'}
            </button>
          )}
          
          <button
            onClick={handleAddBookmark}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            title="添加书签"
          >
            <BookmarkPlus className="w-5 h-5" />
          </button>
          
          <button
            onClick={() => {
              setSidebarTab('annotations');
              setShowSidebar(!showSidebar);
            }}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            title="批注与笔记"
          >
            <Highlighter className="w-5 h-5" />
          </button>
          
          <button
            onClick={() => {
              setSidebarTab('bookmarks');
              setShowSidebar(!showSidebar);
            }}
            className="p-2 hover:bg-gray-100 rounded-lg transition-colors"
            title="书签"
          >
            {showSidebar ? <PanelLeftClose className="w-5 h-5" /> : <PanelLeftOpen className="w-5 h-5" />}
          </button>
          
          <div className="flex items-center gap-1 ml-4">
            <button
              onClick={() => setFontSize(Math.max(12, fontSize - 2))}
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors text-sm"
            >
              A-
            </button>
            <span className="text-sm text-gray-500 w-8 text-center">{fontSize}</span>
            <button
              onClick={() => setFontSize(Math.min(32, fontSize + 2))}
              className="p-2 hover:bg-gray-100 rounded-lg transition-colors text-lg"
            >
              A+
            </button>
          </div>
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* Chapter List Sidebar */}
        {showChapterList && isChapteredFormat(book.format) && (
          <aside className="w-64 border-r border-gray-200 bg-gray-50 flex flex-col">
            <div className="px-4 py-3 border-b border-gray-200">
              <h3 className="font-medium text-gray-900">章节列表</h3>
            </div>
            <div className="flex-1 overflow-y-auto p-2">
              {epubContent.map((chapter, index) => (
                <button
                  key={chapter.href}
                  onClick={() => {
                    handlePageChange(index);
                    setShowChapterList(false);
                  }}
                  className={`w-full text-left px-3 py-2 rounded-lg text-sm transition-colors ${
                    currentChapter === index 
                      ? 'bg-primary-50 text-primary-700' 
                      : 'hover:bg-gray-100 text-gray-700'
                  }`}
                >
                  {chapter.title}
                </button>
              ))}
            </div>
          </aside>
        )}

        {/* Main Content */}
        <main className="flex-1 overflow-y-auto relative">
          {/* 书内搜索面板 */}
          {showBookSearch && (
            <div className="absolute top-0 right-4 z-20 w-96 rounded-lg border border-gray-200 bg-white shadow-xl">
              <div className="flex items-center gap-2 px-3 py-2 border-b border-gray-100">
                <Search className="w-4 h-4 text-gray-400" />
                <input
                  type="text"
                  value={bookSearchQuery}
                  onChange={(e) => setBookSearchQuery(e.target.value)}
                  placeholder="在全书内容中搜索..."
                  autoFocus
                  className="flex-1 text-sm outline-none py-1"
                />
                <button onClick={() => setShowBookSearch(false)} className="text-gray-400 hover:text-gray-600">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="max-h-80 overflow-y-auto">
                {bookSearchResults.length === 0 ? (
                  <p className="px-3 py-6 text-center text-sm text-gray-400">
                    {bookSearchQuery.trim() ? '没有找到匹配内容' : '输入关键词开始搜索'}
                  </p>
                ) : (
                  bookSearchResults.map((r, i) => (
                    <button
                      key={`${r.chapter}-${i}`}
                      onClick={() => jumpToSearchResult(r.chapter, bookSearchQuery.trim())}
                      className="block w-full text-left px-3 py-2 hover:bg-primary-50 transition-colors border-b border-gray-50 last:border-0"
                    >
                      <p className="text-xs text-primary-600 truncate">{r.title}</p>
                      <p className="text-xs text-gray-500 mt-0.5 line-clamp-2">
                        {r.before}
                        <mark>{r.match}</mark>
                        {r.after}
                      </p>
                    </button>
                  ))
                )}
              </div>
            </div>
          )}

          <div
            ref={contentRef}
            className="max-w-3xl mx-auto px-8 py-8"
            style={{ fontSize: `${fontSize}px`, lineHeight: '1.8' }}
            onMouseUp={handleContentMouseUp}
          >
            {isLoading ? (
              <div className="text-center py-12 text-gray-500">加载中...</div>
            ) : isChapteredFormat(book.format) && scrollMode ? (
              <div className="space-y-10">
                {scrollSanitizedChapters.map((ch, i) => (
                  <section
                    key={ch.href}
                    data-chapter={i}
                    // content-visibility:auto：视口外章节跳过渲染/排版，长书滚动性能提升一个量级
                    style={{ contentVisibility: 'auto', containIntrinsicSize: 'auto 800px' } as React.CSSProperties}
                  >
                    <h2 className="text-lg font-semibold text-gray-900 mb-4 pb-2 border-b border-gray-100">
                      {ch.title}
                    </h2>
                    <div className="prose max-w-none" dangerouslySetInnerHTML={{ __html: ch.html }} />
                  </section>
                ))}
              </div>
            ) : (
              <div className="prose max-w-none">
                {book.format === 'txt' ? (
                  <pre className="whitespace-pre-wrap font-sans">{content}</pre>
                ) : (
                  <div dangerouslySetInnerHTML={{ __html: sanitizedHtml }} />
                )}
              </div>
            )}
          </div>
          
          {/* Page Navigation */}
          {!(isChapteredFormat(book.format) && scrollMode) && (
            <>
            <div className="flex items-center justify-center gap-4 py-6 border-t border-gray-200">
              <button
                onClick={() => handlePageChange(currentPage - 1)}
                disabled={currentPage <= (book.format === 'pdf' ? 1 : 0)}
                title="上一页"
                className="p-2 hover:bg-gray-100 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronLeft className="w-5 h-5" />
              </button>

              <div className="flex items-center gap-2">
                {isChapteredFormat(book.format) ? (
                  <span className="text-sm text-gray-700">{getPageLabel()}</span>
                ) : (
                  <>
                    <input
                      type="number"
                      value={currentPage}
                      onChange={(e) => {
                        // 清空/半输入状态（NaN）时忽略，避免误跳第 0 页
                        const parsed = Number.parseInt(e.target.value, 10);
                        if (!Number.isNaN(parsed)) handlePageChange(parsed);
                      }}
                      className="w-16 px-2 py-1 text-center border border-gray-300 rounded focus:ring-2 focus:ring-primary-500 outline-none"
                      min={book.format === 'pdf' ? 1 : 0}
                      max={book.format === 'pdf' ? totalPages : Math.max(totalPages - 1, 0)}
                    />
                    <span className="text-gray-500">/ {book.format === 'pdf' ? totalPages : Math.max(totalPages - 1, 0)}</span>
                  </>
                )}
              </div>

              <button
                onClick={() => handlePageChange(currentPage + 1)}
                disabled={currentPage >= (book.format === 'pdf' ? totalPages : Math.max(totalPages - 1, 0))}
                title="下一页"
                className="p-2 hover:bg-gray-100 rounded-lg disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
              >
                <ChevronRight className="w-5 h-5" />
              </button>
            </div>
            <p className="pb-2 text-center text-xs text-gray-300 select-none">
              ← → 翻页 · F 全屏 · S 书内搜索 · Esc 关闭
            </p>
          </>
          )}
          
          {/* Progress Bar */}
          {!(isChapteredFormat(book.format) && scrollMode) && (
            <div className="h-1 bg-gray-200">
              <div 
                className="h-full bg-primary-500 transition-all"
                style={{ width: `${totalPages > 0 ? (currentPage / totalPages) * 100 : 0}%` }}
              />
            </div>
          )}
        </main>

        {/* Sidebar */}
        {showSidebar && (
          <AnnotationsSidebar
            annotations={annotations}
            tab={sidebarTab}
            onTabChange={setSidebarTab}
            showNoteInput={showNoteInput}
            onToggleNoteInput={() => setShowNoteInput(!showNoteInput)}
            newNoteContent={newNoteContent}
            onNewNoteContentChange={setNewNoteContent}
            onAddNote={handleAddNote}
            onJump={(position) => handlePageChange(position)}
            onDelete={handleDeleteAnnotation}
            highlightSupported={HIGHLIGHT_SUPPORT}
          />
        )}
      </div>

      {/* 划线工具条 */}
      {selectionMenu && (
        <>
          <div className="fixed inset-0 z-[60]" onMouseDown={() => setSelectionMenu(null)} />
          <div
            className="fixed z-[61] flex items-center gap-1 rounded-lg bg-slate-900 text-white px-2 py-1 shadow-xl"
            style={{ left: selectionMenu.x, top: selectionMenu.y, transform: 'translate(-50%, -100%)' }}
          >
            <button
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => handleCreateHighlight(false)}
              className="px-2.5 py-1 text-xs hover:bg-slate-700 rounded transition-colors"
            >
              划线
            </button>
            <button
              onMouseDown={(e) => e.preventDefault()}
              onClick={() => handleCreateHighlight(true)}
              className="px-2.5 py-1 text-xs hover:bg-slate-700 rounded transition-colors"
            >
              划线并批注
            </button>
          </div>
        </>
      )}
    </div>
  );
}
