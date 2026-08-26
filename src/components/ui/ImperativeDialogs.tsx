'use client';

/**
 * Promise 化 confirm/prompt：替代原生阻塞式对话框。
 * 用法：
 *   const dialog = useDialog();
 *   if (!(await dialog.confirm({ message: '确定删除？', danger: true }))) return;
 *   const note = await dialog.prompt({ message: '批注内容（可留空）：' });
 */

import { createContext, ReactNode, useCallback, useContext, useRef, useState } from 'react';
import Modal from './Modal';

export interface ConfirmOptions {
  title?: string;
  message: ReactNode;
  confirmText?: string;
  cancelText?: string;
  /** 危险操作：确认按钮显示为红色 */
  danger?: boolean;
}

export interface PromptOptions extends ConfirmOptions {
  initial?: string;
  placeholder?: string;
}

type Pending =
  | { kind: 'confirm'; options: ConfirmOptions; resolve: (ok: boolean) => void }
  | { kind: 'prompt'; options: PromptOptions; resolve: (value: string | null) => void }
  | null;

/** 按挂起类型给出取消语义：confirm→false，prompt→null（先收窄再调用 resolve） */
function resolveCancel(pending: { kind: 'confirm'; resolve: (ok: boolean) => void } | { kind: 'prompt'; resolve: (v: string | null) => void }) {
  if (pending.kind === 'confirm') pending.resolve(false);
  else pending.resolve(null);
}

interface DialogApi {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  prompt: (options: PromptOptions) => Promise<string | null>;
}

const noop: DialogApi = {
  confirm: async () => false,
  prompt: async () => null,
};

const DialogContext = createContext<DialogApi>(noop);

export function useDialog(): DialogApi {
  return useContext(DialogContext);
}

export function DialogProvider({ children }: { children: ReactNode }) {
  const [pending, setPending] = useState<Pending>(null);
  const pendingRef = useRef<Pending>(null);

  const settle = useCallback(() => {
    pendingRef.current = null;
    setPending(null);
  }, []);

  const confirm = useCallback((options: ConfirmOptions) => {
    // 已有挂起请求时先以取消结算，保证每个 Promise 必然 settle
    const previous = pendingRef.current;
    if (previous) resolveCancel(previous);
    return new Promise<boolean>((resolve) => {
      const entry: Pending = { kind: 'confirm', options, resolve };
      pendingRef.current = entry;
      setPending(entry);
    });
  }, []);

  const prompt = useCallback((options: PromptOptions) => {
    const previous = pendingRef.current;
    if (previous) resolveCancel(previous);
    return new Promise<string | null>((resolve) => {
      const entry: Pending = { kind: 'prompt', options, resolve };
      pendingRef.current = entry;
      setPending(entry);
    });
  }, []);

  const api = useRef<DialogApi>({ confirm, prompt }).current;

  /** 结算当前挂起请求（确认按钮传 true，prompt 提交传输入值） */
  const finish = (result: boolean | string) => {
    const current = pendingRef.current;
    if (!current) return;
    settle();
    if (current.kind === 'confirm') current.resolve(Boolean(result));
    else current.resolve(String(result));
  };

  const cancel = () => {
    const current = pendingRef.current;
    if (!current) return;
    settle();
    resolveCancel(current);
  };

  const options = pending?.options;
  const isDanger = 'danger' in (options ?? {}) ? Boolean(options?.danger) : false;

  return (
    <DialogContext.Provider value={api}>
      {children}
      <Modal
        open={pending !== null}
        onClose={cancel}
        panelClassName="w-full max-w-sm"
        ariaLabel={typeof options?.title === 'string' ? options.title : '确认'}
      >
        <div className="px-5 py-4">
          {options?.title && (
            <h3 className="text-sm font-semibold text-gray-900 mb-2">{options.title}</h3>
          )}
          <div className="text-sm text-gray-600 leading-relaxed break-words whitespace-pre-wrap">
            {options?.message}
          </div>
          {pending?.kind === 'prompt' && (
            <PromptInput
              initial={pending.options.initial}
              placeholder={pending.options.placeholder}
              onSubmit={(value) => finish(value)}
            />
          )}
        </div>
        <div className="flex justify-end gap-2 px-5 py-3 border-t border-gray-100">
          <button
            type="button"
            onClick={cancel}
            className="px-3 py-1.5 text-sm text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
          >
            {options?.cancelText ?? '取消'}
          </button>
          <button
            type="button"
            onClick={() => finish(true)}
            autoFocus
            className={`px-3 py-1.5 text-sm text-white rounded-lg transition-colors ${
              isDanger ? 'bg-red-600 hover:bg-red-700' : 'bg-primary-600 hover:bg-primary-700'
            }`}
          >
            {options?.confirmText ?? '确定'}
          </button>
        </div>
      </Modal>
    </DialogContext.Provider>
  );
}

function PromptInput({
  initial,
  placeholder,
  onSubmit,
}: {
  initial?: string;
  placeholder?: string;
  onSubmit: (value: string) => void;
}) {
  const [value, setValue] = useState(initial ?? '');
  return (
    <input
      type="text"
      value={value}
      onChange={(e) => setValue(e.target.value)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          e.stopPropagation(); // 不触发外层 Escape/Enter 分发
          onSubmit(value);
        }
      }}
      placeholder={placeholder}
      className="mt-3 w-full px-3 py-2 border border-gray-300 rounded-lg text-sm focus:ring-2 focus:ring-primary-500 outline-none"
    />
  );
}
