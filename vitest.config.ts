import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import { fileURLToPath } from 'node:url';

export default defineConfig({
  // 官方 React 插件负责测试图内 JSX 转换：Next 主 tsconfig 为 jsx:"preserve"
  //（其约定），vite 的 esbuild 会遵循它导致含 JSX 的组件在测试中无法解析。
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.{ts,tsx}'],
  },
});
