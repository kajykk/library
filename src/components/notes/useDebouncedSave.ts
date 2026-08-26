'use client';

import { useCallback, useEffect, useRef } from 'react';
import { DocumentPatch, patchDocument, setDocumentTags } from '@/lib/api/client';

export type SaveState = 'saved' | 'saving' | 'dirty';

const SAVE_DEBOUNCE_MS = 800;

interface DebouncedSaveOptions {
  docId: string;
  titleRef: { readonly current: string };
  contentRef: { readonly current: string };
  /** 可选：保存时刻实时取正文（如 TipTap 惰性序列化 Markdown）。
   * 返回 null 表示编辑器未就绪/已销毁，回退 contentRef。 */
  getContent?: { readonly current: () => string | null };
  tagsRef: { readonly current: string[] };
  /** 最近一次已落库的标签集合：增删都按与它的差异决定是否提交（避免删回初始值被跳过） */
  savedTagsRef: { current: string[] };
  onStateChange: (state: SaveState) => void;
}

/**
 * 800ms 防抖自动保存。
 * - schedule(): 编辑/改标题时调用，重置计时器；
 * - flush():   失焦等场景强制立即落盘（仅在确有未保存修改时发请求）；
 * - 卸载清理：清掉计时器；仅当确有未落盘修改时补发一次保存
 *   （复用 performSave 以包含 tags 差异逻辑），打开即关不再产生空写。
 *
 * 保存串行化：所有保存请求按顺序执行（后一个等待前一个落地），
 * 消除"在途保存期间再次触发导致两个并发 PATCH 最后写赢"的竞态。
 */
export function useDebouncedSave({
  docId,
  titleRef,
  contentRef,
  getContent,
  tagsRef,
  savedTagsRef,
  onStateChange,
}: DebouncedSaveOptions) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingRef = useRef(false);
  // 在途保存链尾：新保存排队在其后
  const savingChainRef = useRef<Promise<void>>(Promise.resolve());

  const performSave = useCallback(async () => {
    pendingRef.current = false;
    const run = async () => {
      onStateChange('saving');
      try {
        const fromEditor = getContent ? getContent.current() : null;
        const patch: DocumentPatch = {
          title: titleRef.current || '无标题笔记',
          content: fromEditor !== null ? fromEditor : contentRef.current,
        };
        await patchDocument(docId, patch);
        // 排序后再比较：同集合不同顺序不触发多余请求
        if (
          JSON.stringify([...tagsRef.current].sort()) !== JSON.stringify([...savedTagsRef.current].sort())
        ) {
          await setDocumentTags(docId, tagsRef.current);
          savedTagsRef.current = [...tagsRef.current];
        }
        onStateChange('saved');
      } catch (err) {
        console.error('自动保存失败:', err);
        onStateChange('dirty');
      }
    };
    const prev = savingChainRef.current;
    const current = prev.then(run, run);
    // 链尾吞错：单个保存失败不影响后续排队保存执行
    savingChainRef.current = current.catch(() => undefined);
    await current;
  }, [docId, getContent, onStateChange, savedTagsRef, tagsRef, titleRef, contentRef]);

  const schedule = useCallback(() => {
    pendingRef.current = true;
    onStateChange('dirty');
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      timerRef.current = null;
      void performSave();
    }, SAVE_DEBOUNCE_MS);
  }, [onStateChange, performSave]);

  const flush = useCallback(async () => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    if (pendingRef.current) {
      await performSave();
    } else {
      // 无新修改也要等在途保存完成（版本恢复等场景需要确定性的时点）
      await savingChainRef.current;
    }
  }, [performSave]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      // 卸载补发：仅当确有未落盘修改时执行。此前无条件 PATCH 会污染
      // 版本历史/updated_at，且丢失 tags（只补发了 title/content）。
      // 刻意读取 refs 的最新值（标题/正文此刻的终态）。
      if (pendingRef.current) {
        void performSave();
      }
    };
  }, [docId, performSave]);

  return { schedule, flush };
}
