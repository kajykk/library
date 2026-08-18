import { test, expect } from '@playwright/test';
import path from 'node:path';
import { configureBackend, cleanupBooks, cleanupNotes } from './helpers';

test.describe('核心链路冒烟', () => {
  test('连接配置 → 上传阅读 → 书签 → 笔记协作 → 全文搜索', async ({ page, request }) => {
    // 1) 设置页配置后端连接
    await configureBackend(page);

    // 2) 上传 EPUB（先清理重试残留）
    await cleanupBooks(request, 'E2E 测试书');
    await page.goto('/library');
    await page.locator('input[type="file"]').first().setInputFiles(
      path.join(__dirname, 'fixtures', 'sample.epub'),
    );
    await expect(page.getByText('共处理 1 个文件，成功 1 个')).toBeVisible({ timeout: 30_000 });

    // 3) 打开阅读器，正文渲染 + 添加书签
    await page.goto('/library');
    await page.getByRole('button', { name: '开始阅读' }).first().click();
    await expect(page.getByRole('heading', { name: '第一章 开端' })).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('电磁脉冲 e2e搜索关键词')).toBeVisible();

    await page.getByTitle('批注与笔记').click();
    await page.getByTitle('添加书签').click();
    await expect(page.getByText('书签 (1)')).toBeVisible({ timeout: 10_000 });

    // 4) 新建笔记 + 实时协作连接 + 保存
    await cleanupNotes(request);
    await page.goto('/notes');
    await page.getByRole('button', { name: '新建笔记' }).click();
    await page.getByRole('button', { name: '📄空白笔记' }).click();
    await page.getByPlaceholder('笔记标题').fill('E2E 冒烟笔记');
    await expect(page.getByText('已连接')).toBeVisible({ timeout: 20_000 });
    await page.locator('.ProseMirror').click();
    await page.keyboard.type('这是一条 e2e冒烟笔记正文');

    // 返回列表并确认已保存
    await page.getByRole('button', { name: '返回列表' }).click();
    await expect(page.getByText('E2E 冒烟笔记')).toBeVisible({ timeout: 15_000 });

    // 5) 全文检索命中书籍正文与笔记
    await page.goto('/search');
    await page.getByPlaceholder('输入关键词即时搜索书名、作者、笔记内容...').fill('电磁脉冲');
    await expect(page.getByText('E2E 测试书')).toBeVisible({ timeout: 20_000 });
  });

  test('导入 Markdown 建笔记', async ({ page, request }) => {
    await configureBackend(page);
    await cleanupNotes(request);
    await page.goto('/notes');

    // 导入 .md 文件
    const md = path.join(__dirname, 'fixtures', 'note.md');
    const fs = await import('node:fs');
    fs.writeFileSync(md, '# 导入标题\n\n导入正文内容 e2eimportmarkdown');
    page.once('dialog', (dialog) => dialog.accept());
    await page.locator('input[accept=".md,.markdown,.txt"]').setInputFiles(md);
    await expect(page.getByText('导入标题')).toBeVisible({ timeout: 15_000 });
  });
});
