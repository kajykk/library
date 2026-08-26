'use client';

import { ReactNode } from 'react';
import { X } from 'lucide-react';
import { useOverlayA11y } from './useOverlayA11y';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  /** 面板附加类名（覆盖默认宽度/对齐等） */
  panelClassName?: string;
  /** 无障碍命名：外部传入标题元素 id */
  labelledBy?: string;
  ariaLabel?: string;
  /** 显示右上角关闭按钮 */
  showCloseButton?: boolean;
}

/** 项目级模态框原语：焦点陷阱 + Escape + 背景滚动锁 + 焦点归还。
 * 层级约定：Modal=z-[100]，Drawer=z-[90]，Toast=z-[130]，命令面板=z-[110]。 */
export default function Modal({
  open,
  onClose,
  children,
  panelClassName = 'w-full max-w-lg',
  labelledBy,
  ariaLabel,
  showCloseButton = false,
}: ModalProps) {
  const ref = useOverlayA11y(open, onClose);
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[100] flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-slate-900/40" onClick={onClose} />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-label={ariaLabel}
        className={`relative bg-white rounded-xl shadow-2xl flex flex-col outline-none ${panelClassName}`}
      >
        {showCloseButton && (
          <button
            type="button"
            onClick={onClose}
            className="absolute right-3 top-3 p-1.5 rounded hover:bg-gray-100 text-gray-400 hover:text-gray-600"
            aria-label="关闭"
          >
            <X className="w-4 h-4" />
          </button>
        )}
        {children}
      </div>
    </div>
  );
}
