import { mkdirSync, writeFileSync, readdirSync, rmSync, existsSync } from 'node:fs';
import path from 'node:path';
import JSZip from 'jszip';

/** Windows 上瞬时文件占用(上一轮 uvicorn 收尾/杀毒扫描)会偶发 EPERM，重试几次 */
function removeRetry(target: string, attempts = 3) {
  for (let i = 0; i < attempts; i += 1) {
    try {
      rmSync(target, { recursive: true, force: true });
      return;
    } catch (err) {
      if (i === attempts - 1) {
        console.warn(`[e2e] 清理失败(跳过): ${target} - ${(err as Error).message}`);
        return;
      }
      const until = Date.now() + 300;
      while (Date.now() < until) {
        /* 忙等 300ms 后重试 */
      }
    }
  }
}

/**
 * 清理上一次运行残留的临时库/数据目录（server/e2e-*.db、server/e2e-data-*）
 */
function cleanupPreviousRun() {
  const serverDir = path.join(__dirname, '..', 'server');
  if (!existsSync(serverDir)) return;
  let removed = 0;
  for (const name of readdirSync(serverDir)) {
    if (/^e2e-.*\.db$/.test(name) || /^e2e-data-/.test(name)) {
      removeRetry(path.join(serverDir, name));
      removed += 1;
    }
  }
  if (removed > 0) console.log(`[e2e] cleaned ${removed} leftover artifact(s)`);
}

/**
 * 生成 e2e 冒烟用最小合法 EPUB（标题 E2E 测试书，正文含唯一关键词）
 */
export default async function globalSetup() {
  cleanupPreviousRun();

  const zip = new JSZip();

  zip.file(
    'META-INF/container.xml',
    '<?xml version="1.0"?><container version="1.0" ' +
      'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>' +
      '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>' +
      '</rootfiles></container>',
  );

  zip.file(
    'OEBPS/content.opf',
    `<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" xmlns:dc="http://purl.org/dc/elements/1.1/" version="3.0" unique-identifier="uid">
  <metadata>
    <dc:title>E2E 测试书</dc:title>
    <dc:creator>E2E 作者</dc:creator>
    <dc:language>zh</dc:language>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="ch1"/><itemref idref="ch2"/></spine>
</package>`,
  );

  zip.file(
    'OEBPS/chapter1.xhtml',
    '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>' +
      '<h1>第一章 开端</h1><p>电磁脉冲 e2e搜索关键词 测试正文内容</p></body></html>',
  );

  zip.file(
    'OEBPS/chapter2.xhtml',
    '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>' +
      '<h1>第二章 深入</h1><p>双向链接与知识图谱 进阶内容</p></body></html>',
  );

  const buf = await zip.generateAsync({ type: 'nodebuffer' });
  const dir = path.join(__dirname, 'fixtures');
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, 'sample.epub'), buf);
  console.log('[e2e] fixture sample.epub generated');
}