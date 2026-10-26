'use client';

import { X } from 'lucide-react';

interface FilterChipProps {
  label: string;
  count?: number;
  active?: boolean;
  onClear?: () => void;
  onClick?: () => void;
}

/**
 * 筛选标签胶囊：书架筛选、质量筛选、分面过滤等场景复用。
 * active 状态高亮；带 count 显示数量；带 onClear 显示清除按钮。
 */
export default function FilterChip({ label, count, active = false, onClear, onClick }: FilterChipProps) {
  const isButton = Boolean(onClick);

  return (
    <span
      role={isButton ? 'button' : undefined}
      tabIndex={isButton ? 0 : undefined}
      onClick={onClick}
      onKeyDown={isButton ? (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick?.(); } } : undefined}
      className={`inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-sm font-medium transition-colors select-none ${
        active
          ? 'bg-primary-100 text-primary-700 border border-primary-300'
          : 'bg-gray-100 text-gray-600 border border-transparent hover:bg-gray-200'
      } ${onClick ? 'cursor-pointer' : ''}`}
    >
      {label}
      {typeof count === 'number' && (
        <span className={`text-xs px-1.5 py-0.5 rounded-full ${
          active ? 'bg-primary-200 text-primary-800' : 'bg-gray-200 text-gray-600'
        }`}>
          {count}
        </span>
      )}
      {onClear && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onClear(); }}
          className="p-0.5 rounded-full hover:bg-gray-300 transition-colors"
          aria-label={`清除 ${label} 筛选`}
        >
          <X className="w-3 h-3" />
        </button>
      )}
    </span>
  );
}
