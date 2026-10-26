'use client';

import { useEffect, useState } from 'react';
import Link from 'next/link';
import { AlertTriangle, Info, RefreshCw, X, Settings } from 'lucide-react';
import { useDataSourceStatus } from '@/lib/useDataSourceStatus';

const DISMISS_KEY = 'kb_status_bar_dismissed';

/**
 * 全局数据源状态条（B1-07）。
 * - api: 不渲染
 * - local/no_token 或 unconfigured: 蓝色信息条
 * - degraded/health_failed: 琥珀色警告条
 * - checking / null: 不渲染，避免闪烁
 */
export default function DataSourceStatusBar() {
  const { status, refresh } = useDataSourceStatus();
  const [dismissed, setDismissed] = useState(false);
  const [retrying, setRetrying] = useState(false);

  useEffect(() => {
    setDismissed(sessionStorage.getItem(DISMISS_KEY) === '1');
  }, []);

  if (!status || dismissed) return null;
  if (status.mode === 'api') return null;
  if (status.reason === 'checking') return null;

  const isDegraded = status.mode === 'degraded';
  const isLocal = status.mode === 'local' || status.mode === 'unconfigured';

  const handleDismiss = () => {
    sessionStorage.setItem(DISMISS_KEY, '1');
    setDismissed(true);
  };

  const handleRetry = async () => {
    setRetrying(true);
    refresh();
    // 给 fetch 一点时间
    setTimeout(() => setRetrying(false), 1500);
  };

  return (
    <div
      role={isDegraded ? 'alert' : 'status'}
      className={`flex items-center gap-2 px-4 py-2 text-sm ${
        isDegraded
          ? 'bg-amber-50 border-b border-amber-200 text-amber-800'
          : 'bg-blue-50 border-b border-blue-200 text-blue-800'
      }`}
    >
      {isDegraded ? (
        <AlertTriangle className="w-4 h-4 shrink-0" aria-hidden />
      ) : (
        <Info className="w-4 h-4 shrink-0" aria-hidden />
      )}

      <span className="flex-1 truncate">
        {isDegraded ? (
          <>
            后端不可达（
            <span className="font-mono text-xs">{status.baseUrl}</span>
            ），已回退本地数据
          </>
        ) : (
          '未连接后端，当前使用浏览器本地数据'
        )}
      </span>

      {isDegraded && (
        <button
          type="button"
          onClick={handleRetry}
          disabled={retrying}
          className="flex items-center gap-1 px-2 py-1 rounded hover:bg-amber-100 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3 h-3 ${retrying ? 'animate-spin' : ''}`} />
          重试
        </button>
      )}

      <Link
        href="/settings"
        className="flex items-center gap-1 px-2 py-1 rounded hover:bg-opacity-80 transition-colors"
      >
        <Settings className="w-3 h-3" />
        设置
      </Link>

      <button
        type="button"
        onClick={handleDismiss}
        className="p-1 rounded hover:bg-opacity-80 transition-colors"
        aria-label="关闭提示"
      >
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
