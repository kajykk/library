import { test, expect } from '@playwright/test';
import path from 'node:path';
import { configureBackend, cleanupBooks, cleanupNotes, API_BASE, TOKEN } from './helpers';

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
    await expect.poll(async () => {
      const resp = await request.get('http://localhost:8111/api/documents', {
        headers: { 'X-API-Token': 'e2e-token' },
      });
      const docs = (await resp.json()) as Array<{ title: string; read_progress?: number }>;
      return (docs.find((d) => d.title === 'E2E 测试书')?.read_progress ?? 0) * 100;
    }, { timeout: 30_000 }).toBe(100);
  });

  test('命令面板剪藏网页', async ({ page, request }) => {
    await configureBackend(page);
    await cleanupBooks(request, 'E2E 测试书');
    await page.goto('/library');

    // Ctrl+K 打开命令面板，输入 "剪藏 <URL>" 触发剪藏动作项
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder(/搜索文档/).fill('剪藏 http://localhost:8111/api/health');
    await expect(page.getByText(/剪藏网页：http/)).toBeVisible();
    await page.keyboard.press('Enter');

    // 抓取成功 → 跳转到新建 webclip 笔记（JSON 无 <title>，回退标题为 URL）
    await expect(page.getByRole('button', { name: /剪藏/ }).first()).toBeVisible({ timeout: 30_000 });
    const titleInput = page.locator('input[placeholder="笔记标题"]');
    await expect(titleInput).toHaveValue(/api\/health/);

    // 剪藏文档已入库（webclip 类型）
    const docs = await (
      await request.get(`http://localhost:8111/api/documents`, {
        headers: { 'X-API-Token': 'e2e-token' },
      })
    ).json();
    const clip = docs.find((d: { type: string }) => d.type === 'webclip');
    expect(clip).toBeTruthy();
    expect(clip.source_url).toBe('http://localhost:8111/api/health');
  });

  test('命令面板跳转笔记', async ({ page, request }) => {
    await configureBackend(page);
    await cleanupNotes(request);
    const created = await request.post(`${API_BASE}/api/documents`, {
      headers: { 'X-API-Token': TOKEN },
      data: { type: 'note', title: '命令面板直达测试', content: '<p>hello</p>' },
    });
    expect(created.ok()).toBeTruthy();
    const doc = (await created.json()) as { id: string };

    await page.goto('/library');
    await page.keyboard.press('Control+k');
    await page.getByPlaceholder(/搜索文档/).fill('命令面板直达测试');
    await page.keyboard.press('Enter');
    await expect(page.locator('input[placeholder="笔记标题"]')).toHaveValue('命令面板直达测试', {
      timeout: 15_000,
    });
    // 编辑器内可正常渲染并定位到目标文档
    expect(page.url()).toContain(`/notes?open=${doc.id}`);
  });

  test('笔记标签增删与持久化', async ({ page, request }) => {
    await configureBackend(page);
    await cleanupNotes(request);
    const created = await request.post(`${API_BASE}/api/documents`, {
      headers: { 'X-API-Token': TOKEN },
      data: { type: 'note', title: '标签管理测试', content: '' },
    });
    const doc = (await created.json()) as { id: string };

    // 打开编辑器 → 输入新标签回车
    await page.goto(`/notes?open=${doc.id}`);
    await expect(page.locator('input[placeholder="笔记标题"]')).toHaveValue('标签管理测试');
    await page.getByPlaceholder('+ 标签').fill('e2e标签');
    await page.keyboard.press('Enter');
    // chip 含删除按钮，归一化文本为 "e2e标签×"，用子串匹配
    await expect(page.getByText('e2e标签')).toBeVisible({ timeout: 10_000 });

    // API 侧断言标签已持久化（前端 800ms 防抖保存，轮询等待落库；/api/tags 含孤儿行，查文档自身）
    const docHasTag = async () => {
      const resp = await request.get(`${API_BASE}/api/documents/${doc.id}`, {
        headers: { 'X-API-Token': TOKEN },
      });
      const d = (await resp.json()) as { tags: Array<{ name: string }> };
      return d.tags.some((t) => t.name === 'e2e标签');
    };
    await expect.poll(docHasTag, { timeout: 15_000 }).toBeTruthy();

    // 删除标签 chip → 文档侧标签消失
    await page.getByText('e2e标签').locator('..').getByRole('button').click();
    await expect(page.getByText('e2e标签')).toHaveCount(0, { timeout: 10_000 });
    await expect.poll(docHasTag, { timeout: 15_000 }).toBeFalsy();
  });

  test('设置页备份下载', async ({ page }) => {
    await configureBackend(page);
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: '下载备份 (zip)' }).click();
    const download = await downloadPromise;
    expect(download.suggestedFilename()).toMatch(/\.zip$/);
    const filePath = await download.path();
    expect(filePath).toBeTruthy();
  });
});
