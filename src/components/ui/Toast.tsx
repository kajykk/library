'use client';

/**
 * 轻量 Toast 体系：替代阻塞式 alert，统一反馈样式且可测试。
 * 用法：const toast = useToast(); toast.error('...'); toast.success('...');
 */

import { createContext, useCallback, useContext, useMemo, useRef, useState, ReactNode } from 'react';
import { CheckCircle2, XCircle, Info, X } from 'lucide-react';

type ToastKind = 'success' | 'error' | 'info';

interface ToastItem {
  id: number;
  kind: ToastKind;
  message: string;
}

type PushToast = (kind: ToastKind, message: string) => void;

const ToastContext = createContext<PushToast>(() => {
  /* Provider 未挂载时静默降级（不阻塞调用方） */
});

export function useToast() {
  const push = useContext(ToastContext);
  return useMemo(
    () => ({
      success: (message: string) => push('success', message),
      error: (message: string) => push('error', message),
      info: (message: string) => push('info', message),
    }),
    [push],
  );
}

const KIND_STYLES: Record<ToastKind, string> = {
  success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
  error: 'border-red-200 bg-red-50 text-red-800',
  info: 'border-gray-200 bg-white text-gray-700',
};

function KindIcon({ kind }: { kind: ToastKind }) {
  if (kind === 'success') return <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-500" />;
  if (kind === 'error') return <XCircle className="w-4 h-4 shrink-0 text-red-500" />;
  return <Info className="w-4 h-4 shrink-0 text-gray-400" />;
}

const AUTO_DISMISS_MS = 4000;
const MAX_VISIBLE = 4;

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seqRef = useRef(0);

  const dismiss = useCallback((id: number) => {
    setItems((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const push = useCallback<PushToast>(
    (kind, message) => {
      const id = ++seqRef.current;
      setItems((prev) => [...prev.slice(-(MAX_VISIBLE - 1)), { id, kind, message }]);
      setTimeout(() => dismiss(id), AUTO_DISMISS_MS);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={push}>
      {children}
      {/* aria-live：读屏器播报新出现的提示 */}
      <div aria-live="polite" className="fixed top-4 right-4 z-[130] flex flex-col gap-2 w-80 max-w-[calc(100vw-2rem)]">
        {items.map((t) => (
          <div
            key={t.id}
            role="status"
            data-testid={`toast-${t.kind}`}
            className={`flex items-start gap-2 rounded-lg border px-3 py-2.5 text-sm shadow-lg ${KIND_STYLES[t.kind]}`}
          >
            <KindIcon kind={t.kind} />
            <p className="flex-1 min-w-0 break-words whitespace-pre-wrap">{t.message}</p>
            <button
              onClick={() => dismiss(t.id)}
              className="shrink-0 opacity-60 hover:opacity-100"
              aria-label="关闭提示"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}
