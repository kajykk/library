import { defineConfig } from '@playwright/test';
import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';

const BACKEND_PORT = 8111;
const FRONTEND_PORT = 3111;
const RUN_ID = crypto.randomUUID().slice(0, 8);

// standalone 产物不含 .next/static 与 public（Dockerfile 由镜像层另行 COPY），
// e2e 启动 standalone server 前补齐，否则页面 JS/静态资源全部 404。
const standaloneDir = path.join(process.cwd(), '.next', 'standalone');
if (fs.existsSync(path.join(standaloneDir, 'server.js'))) {
  const standaloneAssets: Array<[string, string]> = [
    [path.join(process.cwd(), '.next', 'static'), path.join(standaloneDir, '.next', 'static')],
    [path.join(process.cwd(), 'public'), path.join(standaloneDir, 'public')],
  ];
  for (const [src, dest] of standaloneAssets) {
    if (fs.existsSync(src)) {
      fs.cpSync(src, dest, { recursive: true });
    }
  }
}

export default defineConfig({
  testDir: 'e2e',
  timeout: 90_000,
  retries: 1,
  workers: 1,
  globalSetup: './e2e/global-setup.ts',
  reporter: [['list'], ['html', { open: 'never' }]],
  use: {
    baseURL: `http://localhost:${FRONTEND_PORT}`,
    // 本机 win 用已安装的 Edge；CI(linux) 用 Playwright 自带 chromium
    channel: process.platform === 'win32' ? 'msedge' : undefined,
    headless: true,
    trace: 'retain-on-failure',
    navigationTimeout: 90_000,
  },
  webServer: [
    {
      // 每次运行使用全新临时库，避免污染真实数据
      command: 'uvicorn app.main:app --port 8111',
      cwd: 'server',
      url: `http://localhost:${BACKEND_PORT}/api/health`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        KB_DATABASE_URL: `sqlite:///./e2e-${RUN_ID}.db`,
        KB_DATA_DIR: `./e2e-data-${RUN_ID}`,
        KB_API_TOKEN: 'e2e-token',
        KB_AUTO_CREATE_TABLES: '1',
        KB_CORS_ORIGINS: `http://localhost:${FRONTEND_PORT}`,
        // 仅 e2e：允许剪藏本机地址（SSRF 防护默认开启）
        KB_ALLOW_LOCAL_CLIP: '1',
        // 仅 e2e：关闭全局限流。所有 spec 共享同一来源 IP，累计计数会打断正常流程
        KB_RATE_LIMIT_ENABLED: '0',
      },
    },
    {
      // 使用 standalone 产物启动（比 next start 轻量，启动更快）
      command: 'node .next/standalone/server.js',
      cwd: '.',
      url: `http://localhost:${FRONTEND_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
      env: {
        PORT: String(FRONTEND_PORT),
        HOSTNAME: '0.0.0.0',
        NODE_ENV: 'production',
      },
    },
  ],
});