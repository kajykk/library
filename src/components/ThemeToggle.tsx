'use client';

/**
 * 亮/暗主题切换：`dark` class 挂在 <html> 上（tailwind darkMode: 'class'）。
 * 偏好存 localStorage；首屏由 layout.tsx 内联脚本提前设置，避免闪烁（FOUC）。
 */

import { useEffect, useState } from 'react';
import { Moon, Sun } from 'lucide-react';

export const THEME_STORAGE_KEY = 'kb_theme';

export default function ThemeToggle() {
  const [dark, setDark] = useState(false);
  const [mounted, setMounted] = useState(false);

  useEffect(() => {
    setMounted(true);
    setDark(document.documentElement.classList.contains('dark'));
  }, []);

  const toggle = () => {
    const next = !dark;
    setDark(next);
    document.documentElement.classList.toggle('dark', next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next ? 'dark' : 'light');
    } catch {
      // localStorage 不可用（隐私模式）时仅本次会话生效
    }
  };

  if (!mounted) {
    return <span className="inline-block w-7 h-7" aria-hidden />;
  }

  return (
    <button
      onClick={toggle}
      className="p-1.5 rounded text-slate-300 hover:bg-slate-800 hover:text-white transition-colors"
      title={dark ? '切换为亮色' : '切换为暗色'}
      aria-label={dark ? '切换为亮色主题' : '切换为暗色主题'}
    >
      {dark ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
    </button>
  );
}
