'use client';

import { useState, useEffect, useRef, useCallback, useMemo } from 'react';
import DOMPurify from 'dompurify';
import { Book } from '@/lib/db';
import {
  getFileArrayBuffer,
  saveReadingProgress,
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
import { 
  ChevronLeft, 
  ChevronRight, 
  Bookmark as BookmarkIcon, 
  BookmarkPlus,
  Plus,
  Trash2,
  PanelLeftClose,
  PanelLeftOpen,
  Highlighter,
  Search,
  X,
} from 'lucide-react';

interface BookReaderProps {
  book: Book;
  onClose: () => void;
  initialPositionOverride?: number;
}

const TXT_LINES_PER_PAGE = 40;

const CHAPTERED_FORMATS = ['epub', 'mobi', 'azw3'];

const isChapteredFormat = (format: string) => CHAPTERED_FORMATS.includes(format);

export default function BookReader({ book, onClose, initialPositionOverride }: BookReaderProps) {
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
    Array<{ chapter: number; title: string; snippet: string }>
  >([]);
  const [searchTarget, setSearchTarget] = useState<{ chapter: number; query: string } | null>(null);

  const contentRef = useRef<HTMLDivElement>(null);
  const txtLinesRef = useRef<string[] | null>(null);
  const pdfDocRef = useRef<{ getPage: (n: number) => Promise<unknown> } | null>(null);
  // PDF 页面渲染缓存（dataURL，最多保留 12 页）+ 渲染序号防快速翻页乱序
  const pdfPageCache = useRef(new Map<number, string>());
  const pdfRenderSeq = useRef(0);
  const readingStartTimeRef = useRef<number>(Date.now());
  const currentPageRef = useRef<number>(initialPosition);
  const initialPositionRef = useRef<number>(initialPosition);
  const scrollSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
        setContent(chapters[initialIndex].content);
      } else {
        setContent(`<p>${formatLabel} 中未解析到可阅读章节</p>`);
      }
    } catch (error) {
      console.error(`${formatLabel} parsing failed:`, error);
      setContent(`<p>${formatLabel} 解析失败</p>`);
    }
  };

  const loadPdfContent = async () => {
    try {
      const arrayBuffer = await getFileArrayBuffer(book.id);
      if (!arrayBuffer) {
        setContent('<p>PDF 文件加载失败</p>');
        return;
      }

      const pdfjsLib = await import('pdfjs-dist/legacy/build/pdf.js');
      pdfjsLib.GlobalWorkerOptions.workerSrc = new URL(
        'pdfjs-dist/legacy/build/pdf.worker.min.js',
        import.meta.url,
      ).toString();

      const loadingTask = pdfjsLib.getDocument({ data: arrayBuffer });
      const pdf = await loadingTask.promise;

      setTotalPages(pdf.numPages);
      pdfDocRef.current = pdf;

      const savedPage = Math.min(Math.max(book.lastPosition ?? 1, 1), pdf.numPages);
      setCurrentPage(savedPage);

      await renderPdfPage(pdf, savedPage);
    } catch (error) {
      console.error('PDF parsing failed:', error);
      setContent('<p>PDF 解析失败</p>');
    }
  };

  const pdfImageHtml = (imageUrl: string) =>
    `<div style="text-align: center;"><img src="${imageUrl}" style="max-width: 100%; height: auto; box-shadow: 0 4px 6px rgba(0,0,0,0.1);" /></div>`;

  const cachePdfPage = (pageNumber: number, imageUrl: string) => {
    const cache = pdfPageCache.current;
    if (cache.has(pageNumber)) return;
    if (cache.size >= 12) {
      const oldest = cache.keys().next().value;
      if (oldest !== undefined) cache.delete(oldest);
    }
    cache.set(pageNumber, imageUrl);
  };

  const prefetchPdfPages = (pdf: any, pageNumber: number) => {
    const targets = [pageNumber + 1, pageNumber - 1, pageNumber + 2, pageNumber - 2]
      .filter((n) => n >= 1 && n <= pdf.numPages && !pdfPageCache.current.has(n));
    targets.forEach(async (n) => {
      try {
        const page = await pdf.getPage(n);
        const viewport = page.getViewport({ scale: 1.5 });
        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        if (!context) return;
        canvas.height = viewport.height;
        canvas.width = viewport.width;
        await page.render({ canvasContext: context, viewport }).promise;
        cachePdfPage(n, canvas.toDataURL('image/png'));
      } catch {
        /* 预取失败忽略 */
      }
    });
  };

  const renderPdfPage = async (pdf: any, pageNumber: number) => {
    const cached = pdfPageCache.current.get(pageNumber);
    if (cached) {
      setContent(pdfImageHtml(cached));
      return;
    }
    const seq = ++pdfRenderSeq.current;
    try {
      const page = await pdf.getPage(pageNumber);
      const scale = 1.5;
      const viewport = page.getViewport({ scale });

      const canvas = document.createElement('canvas');
      const context = canvas.getContext('2d');
      if (!context) return;

      canvas.height = viewport.height;
      canvas.width = viewport.width;

      await page.render({
        canvasContext: context,
        viewport: viewport
      }).promise;

      const imageUrl = canvas.toDataURL('image/png');
      cachePdfPage(pageNumber, imageUrl);
      if (seq === pdfRenderSeq.current) {
        setContent(pdfImageHtml(imageUrl));
      }
      prefetchPdfPages(pdf, pageNumber);
    } catch (error) {
      if (seq === pdfRenderSeq.current) {
        console.error('PDF page render failed:', error);
        setContent('<p>PDF 页面渲染失败</p>');
      }
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
      setContent(epubContent[chapterIndex].content);
    } else if (book.format === 'pdf' && pdfDocRef.current) {
      await renderPdfPage(pdfDocRef.current, target);
    } else if (book.format === 'txt') {
      renderTxtPage(target);
    }

    const progress = totalPages > 0
      ? Math.round(((isPdf ? target : target + 1) / totalPages) * 100)
      : 0;
    await saveReadingProgress(book.id, Math.min(100, progress), target);
  }, [book.format, book.id, currentPage, epubContent, totalPages]);

  const handleAddBookmark = async () => {
    const note = isChapteredFormat(book.format) && epubContent[currentChapter]
      ? epubContent[currentChapter].title
      : `第 ${currentPage} 页`;
    try {
      const created = await addBookmarkAnnotation(book.id, currentPage, chapterKey, note);
      setAnnotations((prev) => [created, ...prev]);
    } catch (err) {
      alert('添加书签失败：' + (err as Error).message);
    }
  };

  const handleAddNote = async () => {
    if (!newNoteContent.trim()) return;
    try {
      const created = await addNoteAnnotation(book.id, currentPage, chapterKey, newNoteContent.trim());
      setAnnotations((prev) => [created, ...prev]);
    } catch (err) {
      alert('添加笔记失败：' + (err as Error).message);
      return;
    }
    setNewNoteContent('');
    setShowNoteInput(false);
  };

  const handleDeleteAnnotation = async (annotation: UnifiedAnnotation) => {
    try {
      await dsDeleteAnnotation(annotation);
    } catch (err) {
      alert('删除失败：' + (err as Error).message);
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
      const input = prompt('批注内容（可留空）：');
      if (input === null) return;
      note = input.trim();
    }
    try {
      const created = await createHighlight(book.id, chapterKey, currentPage, text, note);
      setAnnotations((prev) => [created, ...prev]);
      applyHighlightMark(text, created.id);
    } catch (err) {
      alert((err as Error).message);
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

  const runBookSearch = (q: string) => {
    const query = q.trim();
    if (!query) {
      setBookSearchResults([]);
      return;
    }
    const results: Array<{ chapter: number; title: string; snippet: string }> = [];
    epubContent.forEach((ch, i) => {
      const div = document.createElement('div');
      div.innerHTML = ch.content;
      const text = (div.textContent || '').replace(/\s+/g, ' ');
      const idx = text.toLowerCase().indexOf(query.toLowerCase());
      if (idx >= 0) {
        results.push({
          chapter: i,
          title: ch.title,
          snippet: `${text.slice(Math.max(0, idx - 30), idx)}<mark>${text.slice(idx, idx + query.length)}</mark>${text.slice(idx + query.length, idx + query.length + 40)}`,
        });
      }
    });
    setBookSearchResults(results.slice(0, 50));
  };

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
            saveReadingProgress(book.id, Math.min(100, progress), idx).catch(() => undefined);
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
      contentRef.current
        ?.querySelector(`section[data-chapter="${idx}"]`)
        ?.scrollIntoView({ block: 'start' });
    }, 80);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scrollMode]);

  const handleKeyDown = useCallback((e: KeyboardEvent) => {
    if (e.key === 'ArrowLeft') {
      handlePageChange(currentPage - 1);
    } else if (e.key === 'ArrowRight') {
      handlePageChange(currentPage + 1);
    } else if (e.key === 'Escape') {
      onClose();
    }
  }, [currentPage, handlePageChange, onClose]);

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [handleKeyDown]);

  // 书籍内容来自外部文件，必须经 DOMPurify 白名单净化，防止恶意 EPUB/MOBI 注入脚本
  const sanitizedHtml = useMemo(
    () => (book.format === 'txt' ? '' : DOMPurify.sanitize(content)),
    [content, book.format]
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
            onClick={onClose}
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
                  onChange={(e) => {
                    setBookSearchQuery(e.target.value);
                    runBookSearch(e.target.value);
                  }}
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
                      <p
                        className="text-xs text-gray-500 mt-0.5 line-clamp-2"
                        dangerouslySetInnerHTML={{ __html: r.snippet }}
                      />
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
                {epubContent.map((ch, i) => (
                  <section key={ch.href} data-chapter={i}>
                    <h2 className="text-lg font-semibold text-gray-900 mb-4 pb-2 border-b border-gray-100">
                      {ch.title}
                    </h2>
                    <div
                      className="prose max-w-none"
                      dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(ch.content) }}
                    />
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
                      onChange={(e) => handlePageChange(parseInt(e.target.value) || 0)}
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
          <aside className="w-80 border-l border-gray-200 bg-gray-50 flex flex-col">
            <div className="flex border-b border-gray-200">
              <button
                onClick={() => setSidebarTab('bookmarks')}
                className={`flex-1 py-3 text-sm font-medium transition-colors ${
                  sidebarTab === 'bookmarks'
                    ? 'text-primary-600 border-b-2 border-primary-600 bg-white'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                书签 ({annotations.filter(a => a.type === 'bookmark').length})
              </button>
              <button
                onClick={() => setSidebarTab('annotations')}
                className={`flex-1 py-3 text-sm font-medium transition-colors ${
                  sidebarTab === 'annotations'
                    ? 'text-primary-600 border-b-2 border-primary-600 bg-white'
                    : 'text-gray-500 hover:text-gray-700'
                }`}
              >
                批注 ({annotations.filter(a => a.type !== 'bookmark').length})
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              {sidebarTab === 'bookmarks' ? (
                <div className="space-y-2">
                  {annotations.filter(a => a.type === 'bookmark').length === 0 ? (
                    <p className="text-center text-gray-400 py-8">暂无书签</p>
                  ) : (
                    annotations.filter(a => a.type === 'bookmark').map(bookmark => (
                      <div
                        key={bookmark.id}
                        className="flex items-center justify-between p-3 bg-white rounded-lg border border-gray-200 group"
                      >
                        <button
                          onClick={() => handlePageChange(bookmark.chapter)}
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
                          onClick={() => handleDeleteAnnotation(bookmark)}
                          className="p-1 opacity-0 group-hover:opacity-100 hover:bg-red-50 rounded transition-all"
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
                    onClick={() => setShowNoteInput(!showNoteInput)}
                    className="w-full flex items-center justify-center gap-2 py-2 bg-primary-600 text-white rounded-lg hover:bg-primary-700 transition-colors"
                  >
                    <Plus className="w-4 h-4" />
                    添加笔记
                  </button>

                  {showNoteInput && (
                    <div className="bg-white p-3 rounded-lg border border-gray-200">
                      <textarea
                        value={newNoteContent}
                        onChange={(e) => setNewNoteContent(e.target.value)}
                        placeholder="输入笔记内容..."
                        className="w-full h-24 px-3 py-2 border border-gray-300 rounded resize-none focus:ring-2 focus:ring-primary-500 outline-none text-sm"
                      />
                      <div className="flex justify-end gap-2 mt-2">
                        <button
                          onClick={() => setShowNoteInput(false)}
                          className="px-3 py-1 text-sm text-gray-600 hover:bg-gray-100 rounded transition-colors"
                        >
                          取消
                        </button>
                        <button
                          onClick={handleAddNote}
                          className="px-3 py-1 text-sm bg-primary-600 text-white rounded hover:bg-primary-700 transition-colors"
                        >
                          保存
                        </button>
                      </div>
                    </div>
                  )}

                  {annotations.filter(a => a.type !== 'bookmark').length === 0 ? (
                    <p className="text-center text-gray-400 py-8">
                      暂无批注
                      {HIGHLIGHT_SUPPORT ? '，选中正文即可划线' : ''}
                    </p>
                  ) : (
                    annotations.filter(a => a.type !== 'bookmark').map(ann => (
                      <div
                        key={ann.id}
                        className="bg-white p-3 rounded-lg border border-gray-200 group"
                      >
                        {ann.type === 'highlight' ? (
                          <p className="text-sm text-gray-500 border-l-4 border-yellow-300 pl-2 italic">
                            {ann.quote}
                          </p>
                        ) : null}
                        {ann.content ? (
                          <p className={`text-sm text-gray-700 ${ann.type === 'highlight' ? 'mt-1' : ''}`}>{ann.content}</p>
                        ) : null}
                        <div className="flex items-center justify-between mt-2">
                          <span className="text-xs text-gray-400">
                            {ann.type === 'highlight' ? '划线' : `第 ${ann.page} 页`} · {new Date(ann.createdAt).toLocaleDateString('zh-CN')}
                          </span>
                          <div className="flex items-center gap-1">
                            <button
                              onClick={() => handlePageChange(ann.chapter)}
                              className="text-xs text-primary-600 hover:underline opacity-0 group-hover:opacity-100 transition-all"
                            >
                              跳转
                            </button>
                            <button
                              onClick={() => handleDeleteAnnotation(ann)}
                              className="p-1 opacity-0 group-hover:opacity-100 hover:bg-red-50 rounded transition-all"
                            >
                              <Trash2 className="w-3 h-3 text-red-500" />
                            </button>
                          </div>
                        </div>
                      </div>
                    ))
                  )}
                </div>
              )}
            </div>
          </aside>
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
