/**
 * 每日阅读笔记内容生成：把当日标注（划线/批注/书签）按书分组，
 * 生成带 [[书名]] 双链的 Markdown 骨架（对标 Obsidian 每日笔记）。
 */

import type { AnnotationHit } from './api/client';

export function buildDailyNoteContent(hits: AnnotationHit[], dateLabel: string): string {
  const withBody = hits.filter((h) => (h.quote || '').trim() || (h.content || '').trim());
  if (withBody.length === 0) {
    return `# ${dateLabel} 阅读记录\n\n今天还没有新的划线与批注。\n`;
  }

  const byBook = new Map<string, AnnotationHit[]>();
  for (const hit of withBody) {
    const key = hit.document_title || '未命名文档';
    const group = byBook.get(key) ?? [];
    group.push(hit);
    byBook.set(key, group);
  }

  const lines: string[] = [`# ${dateLabel} 阅读记录`, '', `今日标注 ${withBody.length} 条`, '', '## 今日划线', ''];
  for (const [bookTitle, group] of byBook) {
    lines.push(`### 《[[${bookTitle}]]》`, '');
    for (const hit of group) {
      const quote = (hit.quote || '').trim();
      const note = (hit.content || '').trim();
      if (quote) {
        lines.push(...quote.split('\n').map((line) => `> ${line}`));
        lines.push('');
      }
      if (note) {
        lines.push(note, '');
      }
    }
  }
  lines.push('## 收获与反思', '');
  return lines.join('\n');
}
