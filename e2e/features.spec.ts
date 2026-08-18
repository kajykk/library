import { test, expect } from '@playwright/test';
import path from 'node:path';
import { configureBackend, cleanupBooks } from './helpers';

test.describe('功能扩展冒烟', () => {
  test('书内搜索与连续滚动', async ({ page, request }) => {
    await configureBackend(page);
    await cleanupBooks(request, 'E2E 测试书');
    await page.goto('/library');
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(__dirname, 'fixtures', 'sample.epub'),
    );
    await expect(page.getByText('共处理 1 个文件，成功 1 个')).toBeVisible({ timeout: 30_000 });

    await page.goto('/library');
    await page.getByRole('button', { name: '开始阅读' }).first().click();
    await expect(page.getByRole('heading', { name: '第一章 开端' })).toBeVisible({ timeout: 30_000 });

    // 书内搜索命中并跳转
    await page.getByTitle('书内搜索').click();
    await page.getByPlaceholder('在全书内容中搜索...').fill('电磁脉冲');
    const hit = page.locator('div.max-h-80 button').first();
    await expect(hit).toBeVisible({ timeout: 10_000 });
    await hit.click();
    await expect(page.getByText('电磁脉冲 e2e搜索关键词')).toBeVisible();

    // 切换连续滚动模式
    await page.getByTitle('切换为连续滚动模式').click();
    await expect(page.getByTitle('切换为分页模式')).toBeVisible();
    await expect(page.getByText('电磁脉冲 e2e搜索关键词')).toBeVisible();
  });

  test('设置页全文索引维护', async ({ page }) => {
    await configureBackend(page);
    await page.getByRole('button', { name: '重建全文索引' }).click();
    await expect(page.getByText(/上次索引完成/)).toBeVisible({ timeout: 60_000 });
  });

  test('知识图谱渲染', async ({ page, request }) => {
    await configureBackend(page);
    await cleanupBooks(request, 'E2E 测试书');
    await page.goto('/library');
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(__dirname, 'fixtures', 'sample.epub'),
    );
    await expect(page.getByText('成功 1 个')).toBeVisible({ timeout: 30_000 });

    await page.goto('/graph');
    await expect(page.getByRole('heading', { name: '知识图谱' })).toBeVisible();
    await expect(page.getByText(/\d+ 节点 · \d+ 连接/)).toBeVisible({ timeout: 15_000 });
  });

  test('阅读进度持久化', async ({ page, request }) => {
    await configureBackend(page);
    await cleanupBooks(request, 'E2E 测试书');
    await page.goto('/library');
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(__dirname, 'fixtures', 'sample.epub'),
    );
    await expect(page.getByText('成功 1 个')).toBeVisible({ timeout: 30_000 });

    // 打开阅读器翻到第二章
    await page.goto('/library');
    await page.getByRole('button', { name: '开始阅读' }).first().click();
    await expect(page.getByRole('heading', { name: '第一章 开端' })).toBeVisible({ timeout: 30_000 });
    await page.getByTitle('下一页').click();
    await expect(page.getByRole('heading', { name: '第二章 深入' })).toBeVisible({ timeout: 10_000 });

    // 返回书架：进度已持久化（2 章读完 → 100%）
    await page.getByTitle('返回书架').click();
    await expect(page.getByText('已读 100%')).toBeVisible({ timeout: 15_000 });
  });
});
