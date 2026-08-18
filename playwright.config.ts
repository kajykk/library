import { defineConfig } from '@playwright/test';
import crypto from 'node:crypto';

const BACKEND_PORT = 8111;
const FRONTEND_PORT = 3111;
const RUN_ID = crypto.randomUUID().slice(0, 8);

export default defineConfig({
  testDir: 'e2e',
  timeout: 90_000,
  retries: 1,
  workers: 1,
  globalSetup: './e2e/global-setup.ts',
  reporter: [['list']],
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
      },
    },
    {
      // 使用已构建产物启动，避免 next dev 首次编译超时
      command: 'npm run start -- -p 3111',
      cwd: '.',
      url: `http://localhost:${FRONTEND_PORT}`,
      reuseExistingServer: false,
      timeout: 60_000,
    },
  ],
});