import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import JSZip from 'jszip';

/**
 * 生成 e2e 冒烟用最小合法 EPUB（标题 E2E 测试书，正文含唯一关键词）
 */
export default async function globalSetup() {
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
  </manifest>
  <spine><itemref idref="ch1"/></spine>
</package>`,
  );

  zip.file(
    'OEBPS/chapter1.xhtml',
    '<?xml version="1.0"?><html xmlns="http://www.w3.org/1999/xhtml"><body>' +
      '<h1>第一章 开端</h1><p>电磁脉冲 e2e搜索关键词 测试正文内容</p></body></html>',
  );

  const buf = await zip.generateAsync({ type: 'nodebuffer' });
  const dir = path.join(__dirname, 'fixtures');
  mkdirSync(dir, { recursive: true });
  writeFileSync(path.join(dir, 'sample.epub'), buf);
  console.log('[e2e] fixture sample.epub generated');
}