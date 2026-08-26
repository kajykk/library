'use client';

/**
 * 浮层无障碍行为共享 Hook：焦点陷阱 + Escape 关闭 + 背景滚动锁 + 焦点归还。
 * Modal / Drawer 及其他自绘浮层统一复用，替代各处散落的手写监听。
 */

import { useEffect, useRef } from 'react';

export const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useOverlayA11y(open: boolean, onClose: () => void) {
  const ref = useRef<HTMLDivElement>(null);
  // onClose 可能是内联箭头函数：经 ref 中转避免 effect 因身份变化反复绑定
  const onCloseRef = useRef(onClose);
  onCloseRef.current = onClose;

  useEffect(() => {
    if (!open) return;

    const previouslyFocused = document.activeElement as HTMLElement | null;

    // 初始聚焦面板内第一个可交互元素（无则聚焦面板本身，保证 Tab 从面板出发）。
    // 不做布局级可见性过滤：jsdom 等测试环境无布局信息，会误杀全部元素。
    const container = ref.current;
    if (container) {
      if (!container.hasAttribute('tabindex')) container.tabIndex = -1;
      const first = container.querySelector<HTMLElement>(FOCUSABLE_SELECTOR);
      (first ?? container).focus();
    }

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (e.key !== 'Tab') return;
      // 焦点陷阱：Tab 循环限制在浮层内
      const nodes = Array.from(
        container?.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR) ?? [],
      );
      if (nodes.length === 0) {
        e.preventDefault();
        container?.focus();
        return;
      }
      const firstEl = nodes[0];
      const lastEl = nodes[nodes.length - 1];
      const active = document.activeElement;
      if (e.shiftKey && (active === firstEl || !(container && container.contains(active)))) {
        e.preventDefault();
        lastEl.focus();
      } else if (!e.shiftKey && active === lastEl) {
        e.preventDefault();
        firstEl.focus();
      }
    };

    // 捕获阶段拦截：优先于浮层内部的输入框等处理器
    document.addEventListener('keydown', onKeyDown, true);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    return () => {
      document.removeEventListener('keydown', onKeyDown, true);
      document.body.style.overflow = previousOverflow;
      // 焦点归还触发打开的元素
      previouslyFocused?.focus?.();
    };
  }, [open]);

  return ref;
}
