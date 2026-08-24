'use client';

import { useCallback, useEffect, useRef } from 'react';
import { DocumentPatch, patchDocument, setDocumentTags } from '@/lib/api/client';

export type SaveState = 'saved' | 'saving' | 'dirty';

const SAVE_DEBOUNCE_MS = 800;

interface DebouncedSaveOptions {
  docId: string;
  titleRef: { readonly current: string };
  contentRef: { readonly current: string };
  tagsRef: { readonly current: string[] };
  /** 最近一次已落库的标签集合：增删都按与它的差异决定是否提交（避免删回初始值被跳过） */
  savedTagsRef: { current: string[] };
  onStateChange: (state: SaveState) => void;
}

/**
 * 800ms 防抖自动保存。
 * - schedule(): 编辑/改标题时调用，重置计时器；
 * - flush():   失焦等场景强制立即落盘（仅在确有未保存修改时发请求）；
 * - 卸载清理：清掉计时器并补发一次保存，避免快速返回丢标题/正文。
 */
export function useDebouncedSave({
  docId,
  titleRef,
  contentRef,
  tagsRef,
  savedTagsRef,
  onStateChange,
}: DebouncedSaveOptions) {
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const pendingRef = useRef(false);

  const performSave = useCallback(async () => {
    pendingRef.current = false;
    onStateChange('saving');
    try {
      const patch: DocumentPatch = { title: titleRef.current || '无标题笔记', content: contentRef.current };
      await patchDocument(docId, patch);
      if (JSON.stringify(tagsRef.current) !== JSON.stringify(savedTagsRef.current)) {
        await setDocumentTags(docId, tagsRef.current);
        savedTagsRef.current = [...tagsRef.current];
      }
      onStateChange('saved');
    } catch (err) {
      console.error('自动保存失败:', err);
      onStateChange('dirty');
    }
  }, [docId, onStateChange, savedTagsRef, tagsRef, titleRef, contentRef]);

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
    }
  }, [performSave]);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      // 卸载补发保存：刻意读取 refs 的最新值（标题/正文此刻的终态）
      patchDocument(docId, {
        // eslint-disable-next-line react-hooks/exhaustive-deps
        title: titleRef.current || '无标题笔记',
        // eslint-disable-next-line react-hooks/exhaustive-deps
        content: contentRef.current,
      }).catch((err) => console.error('卸载保存失败:', err));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  return { schedule, flush };
}
