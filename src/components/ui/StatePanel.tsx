'use client';

import { Loader2, AlertCircle, Inbox } from 'lucide-react';

type StatePanelVariant = 'loading' | 'empty' | 'error';

interface StatePanelProps {
  variant: StatePanelVariant;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
  className?: string;
}

/**
 * 统一的加载/空态/错误态面板。
 * loading 用骨架+旋转图标，error 必须有重试入口，empty 可选操作按钮。
 */
export default function StatePanel({ variant, title, description, action, className = '' }: StatePanelProps) {
  const isAlert = variant === 'error';

  return (
    <div
      role={isAlert ? 'alert' : 'status'}
      aria-live={isAlert ? 'assertive' : 'polite'}
      className={`flex flex-col items-center justify-center py-12 px-4 text-center ${className}`}
    >
      {variant === 'loading' && (
        <Loader2 className="w-10 h-10 text-primary-400 animate-spin mb-4" aria-hidden />
      )}
      {variant === 'empty' && (
        <div className="w-16 h-16 rounded-full bg-gray-100 flex items-center justify-center mb-4">
          <Inbox className="w-8 h-8 text-gray-400" aria-hidden />
        </div>
      )}
      {variant === 'error' && (
        <div className="w-16 h-16 rounded-full bg-red-50 flex items-center justify-center mb-4">
          <AlertCircle className="w-8 h-8 text-red-400" aria-hidden />
        </div>
      )}

      <h3 className={`text-base font-medium mb-1 ${variant === 'error' ? 'text-red-800' : 'text-gray-900'}`}>
        {title}
      </h3>
      {description && (
        <p className={`text-sm max-w-md ${variant === 'error' ? 'text-red-600' : 'text-gray-500'}`}>
          {description}
        </p>
      )}

      {action && (
        <button
          type="button"
          onClick={action.onClick}
          className={`mt-4 px-4 py-2 rounded-lg text-sm font-medium transition-colors ${
            variant === 'error'
              ? 'bg-red-600 text-white hover:bg-red-700'
              : 'bg-primary-600 text-white hover:bg-primary-700'
          }`}
        >
          {action.label}
        </button>
      )}
    </div>
  );
}
