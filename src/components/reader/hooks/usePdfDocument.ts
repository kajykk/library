'use client';

/**
 * PDF 文档生命周期与页面渲染（pdf.js）：
 * 加载/销毁、LRU 页缓存（dataURL，12 页）、±1/±2 页预取、渲染乱序守卫。
 * 从 BookReader 抽离；行为契约见 __tests__/BookReader.formats.test.tsx。
 */

import { useCallback, useRef } from 'react';

interface PdfPageLike {
  getViewport(options: { scale: number }): { width: number; height: number };
  // 方法语法（双变）以兼容 pdfjs 真实 PDFPageProxy 的具体参数类型
  render(params: {
    canvasContext: CanvasRenderingContext2D;
    viewport: unknown;
  }): { promise: Promise<void> };
}

interface PdfDocumentLike {
  numPages: number;
  getPage: (pageNumber: number) => Promise<PdfPageLike>;
  destroy: () => Promise<void>;
}

const PDF_SCALE = 1.5;
const PDF_CACHE_MAX = 12;

function pdfImageHtml(imageUrl: string): string {
  return `<div style="text-align: center;"><img src="${imageUrl}" style="max-width: 100%; height: auto; box-shadow: 0 4px 6px rgba(0,0,0,0.1);" /></div>`;
}

export function usePdfDocument(onContent: (html: string) => void) {
  const docRef = useRef<PdfDocumentLike | null>(null);
  const pageCacheRef = useRef(new Map<number, string>());
  const renderSeqRef = useRef(0);
  const cleanupRef = useRef<(() => void) | null>(null);

  // onContent 多为 setState（身份稳定）；经 ref 中转以防内联箭头函数
  const onContentRef = useRef(onContent);
  onContentRef.current = onContent;

  const cachePdfPage = useCallback((pageNumber: number, imageUrl: string) => {
    const cache = pageCacheRef.current;
    if (cache.has(pageNumber)) return;
    if (cache.size >= PDF_CACHE_MAX) {
      const oldest = cache.keys().next().value;
      if (oldest !== undefined) cache.delete(oldest);
    }
    cache.set(pageNumber, imageUrl);
  }, []);

  const prefetchPdfPages = useCallback(
    (pdf: PdfDocumentLike, pageNumber: number) => {
      const targets = [pageNumber + 1, pageNumber - 1, pageNumber + 2, pageNumber - 2].filter(
        (n) => n >= 1 && n <= pdf.numPages && !pageCacheRef.current.has(n),
      );
      targets.forEach(async (n) => {
        try {
          const page = await pdf.getPage(n);
          const viewport = page.getViewport({ scale: PDF_SCALE });
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
    },
    [cachePdfPage],
  );

  const renderPage = useCallback(
    async (pageNumber: number) => {
      const pdf = docRef.current;
      if (!pdf) return;
      const cached = pageCacheRef.current.get(pageNumber);
      if (cached) {
        onContentRef.current(pdfImageHtml(cached));
        return;
      }
      const seq = ++renderSeqRef.current;
      try {
        const page = await pdf.getPage(pageNumber);
        const viewport = page.getViewport({ scale: PDF_SCALE });

        const canvas = document.createElement('canvas');
        const context = canvas.getContext('2d');
        if (!context) return;

        canvas.height = viewport.height;
        canvas.width = viewport.width;

        await page.render({ canvasContext: context, viewport }).promise;

        const imageUrl = canvas.toDataURL('image/png');
        cachePdfPage(pageNumber, imageUrl);
        if (seq === renderSeqRef.current) {
          onContentRef.current(pdfImageHtml(imageUrl));
        }
        prefetchPdfPages(pdf, pageNumber);
      } catch (error) {
        if (seq === renderSeqRef.current) {
          console.error('PDF page render failed:', error);
          onContentRef.current('<p>PDF 页面渲染失败</p>');
        }
      }
    },
    [cachePdfPage, prefetchPdfPages],
  );

  /** 加载文档并返回页数；加载/打开失败时抛出（由调用方决定错误展示） */
  const load = useCallback(async (arrayBuffer: ArrayBuffer): Promise<number> => {
    // pdfjs-dist v4：legacy 构建仅提供 ESM 入口；worker 用 public 下同源 .mjs
    const pdfjsLib = await import('pdfjs-dist/legacy/build/pdf.mjs');
    pdfjsLib.GlobalWorkerOptions.workerSrc = '/pdf.worker.min.mjs';

    const loadingTask = pdfjsLib.getDocument({ data: arrayBuffer });
    const pdf = await loadingTask.promise;

    cleanupRef.current = () => {
      try {
        void pdf.destroy();
      } catch {
        /* 已销毁则忽略 */
      }
      try {
        void loadingTask.destroy();
      } catch {
        /* 已销毁则忽略 */
      }
    };

    docRef.current = pdf;
    return pdf.numPages;
  }, []);

  /** 卸载时销毁文档与 loadingTask，释放 worker 内存 */
  const destroy = useCallback(() => {
    cleanupRef.current?.();
    cleanupRef.current = null;
    docRef.current = null;
    pageCacheRef.current.clear();
  }, []);

  return { load, renderPage, destroy };
}
