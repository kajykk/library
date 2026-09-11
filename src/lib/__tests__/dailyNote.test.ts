// @vitest-environment jsdom
import { describe, expect, it } from 'vitest';
import { AnnotationHit } from '../api/client';
import { buildDailyNoteContent } from '../dailyNote';

function hit(partial: Partial<AnnotationHit>): AnnotationHit {
  return {
    id: 'a1',
    document_id: 'd1',
    document_title: '测试书',
    document_type: 'book',
    type: 'highlight',
    anchor: { chapter: 0 },
    quote: '划线原文',
    content: '',
    created_at: '2026-06-16T08:00:00Z',
    ...partial,
  };
}

describe('buildDailyNoteContent', () => {
  it('按书分组并生成引用块与 [[书名]] 双链', () => {
    const md = buildDailyNoteContent(
      [
        hit({ document_title: '书A', quote: '原文一', content: '批注一' }),
        hit({ document_title: '书A', quote: '原文二' }),
        hit({ document_title: '书B', type: 'note', quote: '', content: '纯批注' }),
      ],
      '2026-06-16',
    );
    expect(md).toContain('# 2026-06-16 阅读记录');
    expect(md).toContain('今日标注 3 条');
    expect(md).toContain('### 《[[书A]]》');
    expect(md).toContain('### 《[[书B]]》');
    expect(md).toContain('> 原文一');
    expect(md).toContain('> 原文二');
    expect(md).toContain('批注一');
    expect(md).toContain('纯批注');
    // 分组顺序：书A 在 书B 前
    expect(md.indexOf('[[书A]]')).toBeLessThan(md.indexOf('[[书B]]'));
    expect(md).toContain('## 收获与反思');
  });

  it('无有效标注时返回友好空态', () => {
    const md = buildDailyNoteContent([], '2026-06-16');
    expect(md).toContain('今天还没有新的划线与批注');
  });

  it('纯书签（无 quote 无 content）不进入正文', () => {
    const md = buildDailyNoteContent([hit({ type: 'bookmark', quote: '', content: '' })], '2026-06-16');
    expect(md).toContain('今天还没有新的划线与批注');
  });
});
