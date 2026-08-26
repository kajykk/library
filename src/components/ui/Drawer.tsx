'use client';

import { ReactNode } from 'react';
import { X } from 'lucide-react';
import { useOverlayA11y } from './useOverlayA11y';

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
  labelledBy?: string;
  ariaLabel?: string;
}

/** 右侧抽屉原语（基于与 Modal 相同的无障碍行为，层级 z-[90]） */
export default function Drawer({ open, onClose, children, labelledBy, ariaLabel }: DrawerProps) {
  const ref = useOverlayA11y(open, onClose);
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[90]">
      <div className="absolute inset-0 bg-slate-900/30" onClick={onClose} />
      <div
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-label={ariaLabel}
        className="absolute right-0 top-0 h-full w-80 bg-white shadow-2xl flex flex-col outline-none"
      >
        {children}
      </div>
    </div>
  );
}

export function DrawerHeader({ title, onClose, id }: { title: ReactNode; onClose: () => void; id?: string }) {
  return (
    <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200">
      <h3 id={id} className="text-sm font-medium text-gray-900">{title}</h3>
      <button onClick={onClose} className="p-1 hover:bg-gray-100 rounded" aria-label="关闭">
        <X className="w-4 h-4 text-gray-500" />
      </button>
    </div>
  );
}
