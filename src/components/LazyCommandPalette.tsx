'use client';

import dynamic from 'next/dynamic';
import { useEffect, useState } from 'react';

// 命令面板按需加载：首屏不下载面板相关代码与文档列表查询依赖；
// 仅当用户按 Ctrl/Cmd+K 时挂载，dynamic() 才会拉取对应 chunk。
const CommandPaletteImpl = dynamic(() => import('@/components/CommandPalette'), {
  ssr: false,
  loading: () => null,
});

export default function LazyCommandPalette() {
  const [enabled, setEnabled] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        // 首次触发即挂载并保持常驻，后续打开不再重复下载（子组件内部管理开合状态）
        setEnabled(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // 挂载后由子组件接管 Ctrl+K 的打开/关闭；这里仅在首次按键后渲染子组件。
  if (!enabled) return null;
  return <CommandPaletteImpl initialOpen />;
}