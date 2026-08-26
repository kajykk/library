'use client';

/**
 * 阅读器全局快捷键分发：Escape 分层关闭浮层 → 退出；←/→ 翻页；
 * F 全屏、S 书内搜索（带修饰键豁免与输入框聚焦守卫）。
 * 从 BookReader 抽离；行为契约见 __tests__/BookReader.test.tsx。
 */

import { useEffect, useRef } from 'react';

export interface ReaderShortcutsHandlers {
  /** Escape 分层关闭浮层（书内搜索→章节列表→侧栏）；消费了返回 true */
  onEscape: () => boolean;
  /** 无浮层可关时退出阅读器 */
  onClose: () => void;
  /** 翻页：delta 为 ±1 */
  onPageChange: (delta: number) => void;
  /** 切换全屏 */
  onToggleFullscreen: () => void;
  /** 切换书内搜索面板；仅章节格式处理并返回 true（此时才 preventDefault） */
  onToggleBookSearch: () => boolean;
}

function isTypingTarget(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  return (
    !!el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA' || el.isContentEditable)
  );
}

export function useReaderShortcuts(handlers: ReaderShortcutsHandlers) {
  // 经 ref 中转：handler 可依赖最新渲染状态，监听只绑定一次
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const h = handlersRef.current;
      if (e.key === 'Escape') {
        if (!h.onEscape()) h.onClose();
        return;
      }
      if (isTypingTarget(e.target)) return;
      // 带修饰键的组合键一律放行：不劫持浏览器/系统快捷键
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === 'ArrowLeft') {
        h.onPageChange(-1);
      } else if (e.key === 'ArrowRight') {
        h.onPageChange(1);
      } else if (e.key.toLowerCase() === 'f') {
        e.preventDefault();
        h.onToggleFullscreen();
      } else if (e.key.toLowerCase() === 's' && h.onToggleBookSearch()) {
        e.preventDefault();
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, []);
}
