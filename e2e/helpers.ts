import { Page, APIRequestContext, expect } from '@playwright/test';

export const API_BASE = 'http://localhost:8111';
export const TOKEN = 'e2e-token';

export async function configureBackend(page: Page) {
  await page.goto('/settings');
  await page.getByPlaceholder('http://localhost:8000').fill(API_BASE);
  await page.getByPlaceholder('与服务端 KB_API_TOKEN 一致').fill(TOKEN);
  await page.getByRole('button', { name: '保存并测试连接' }).click();
  await expect(page.getByText('连接正常')).toBeVisible({ timeout: 15_000 });
}

/** 重试时后端 DB 复用，先清掉同名文档使用例幂等 */
export async function cleanupBooks(request: APIRequestContext, title: string) {
  const headers = { 'X-API-Token': TOKEN };
  const res = await request.get(`${API_BASE}/api/documents`, { headers });
  const docs = (await res.json()) as Array<{ id: string; title: string }>;
  for (const d of docs) {
    if (d.title === title) {
      await request.delete(`${API_BASE}/api/documents/${d.id}?hard=true`, { headers });
    }
  }
}

/** 清掉所有笔记/剪藏（测试产物），避免重试时标题重复 */
export async function cleanupNotes(request: APIRequestContext) {
  const headers = { 'X-API-Token': TOKEN };
  const res = await request.get(`${API_BASE}/api/documents`, { headers });
  const docs = (await res.json()) as Array<{ id: string; title: string; type: string }>;
  for (const d of docs) {
    if (d.type !== 'book') {
      await request.delete(`${API_BASE}/api/documents/${d.id}?hard=true`, { headers });
    }
  }
}
