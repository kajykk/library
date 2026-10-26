import { describe, expect, it } from 'vitest';
import {
  DEFAULT_FILTERS,
  buildActiveChips,
  buildApiParams,
  clearLibraryFilters,
  filtersEqual,
  isLibraryFiltered,
  parseLibraryFilters,
  serializeLibraryFilters,
  type LibraryFilters,
} from '../libraryFilters';

function filters(overrides: Partial<LibraryFilters> = {}): LibraryFilters {
  return { ...DEFAULT_FILTERS, ...overrides };
}

describe('parseLibraryFilters', () => {
  it('空 URL 返回全默认值', () => {
    expect(parseLibraryFilters(new URLSearchParams())).toEqual(DEFAULT_FILTERS);
  });

  it('解析全部字段，并归一化格式/语言', () => {
    const parsed = parseLibraryFilters(
      new URLSearchParams(
        'q=  历史  &shelf=reading&category=中国史&author=张三&quality=issues&format=PDF&language=en-US&tag=近代&sort=title_asc&view=list',
      ),
    );
    expect(parsed).toEqual({
      q: '历史',
      shelf: 'reading',
      category: '中国史',
      author: '张三',
      quality: 'issues',
      format: 'pdf',
      language: 'en',
      tag: '近代',
      sort: 'title_asc',
      view: 'list',
    });
  });

  it('未知枚举回退默认，不抛错', () => {
    const parsed = parseLibraryFilters(
      new URLSearchParams('shelf=xxx&quality=zzz&sort=yyy&view=cards'),
    );
    expect(parsed.shelf).toBe('all');
    expect(parsed.quality).toBeNull();
    expect(parsed.sort).toBe('created_desc');
    expect(parsed.view).toBe('grid');
  });

  it('shelf=recent 强制 created_desc，忽略 URL 里的 sort', () => {
    const parsed = parseLibraryFilters(new URLSearchParams('shelf=recent&sort=title_asc'));
    expect(parsed.shelf).toBe('recent');
    expect(parsed.sort).toBe('created_desc');
  });

  it('空字符串参数视为未设置', () => {
    const parsed = parseLibraryFilters(
      new URLSearchParams('q=&category=&author=&format=&language=&tag=&quality='),
    );
    expect(parsed).toEqual(DEFAULT_FILTERS);
  });
});

describe('serializeLibraryFilters', () => {
  it('默认值不写进 URL', () => {
    expect(serializeLibraryFilters(filters()).toString()).toBe('');
  });

  it('输出顺序固定：q,shelf,category,author,quality,format,language,tag,sort,view', () => {
    const qs = serializeLibraryFilters(
      filters({
        q: 'history',
        shelf: 'reading',
        category: 'history',
        author: 'author',
        quality: 'issues',
        format: 'pdf',
        language: 'en',
        tag: 'tag',
        sort: 'title_asc',
        view: 'list',
      }),
    ).toString();
    expect(qs).toBe(
      'q=history&shelf=reading&category=history&author=author&quality=issues&format=pdf&language=en&tag=tag&sort=title_asc&view=list',
    );
  });

  it('shelf=recent 时不写 sort', () => {
    const qs = serializeLibraryFilters(filters({ shelf: 'recent', sort: 'title_asc' }));
    expect(qs.get('shelf')).toBe('recent');
    expect(qs.has('sort')).toBe(false);
  });

  it('round-trip 幂等', () => {
    const original = filters({
      q: 'foo',
      shelf: 'unread',
      category: '历史',
      author: '张三',
      quality: 'no_cover',
      format: 'epub',
      language: 'zh',
      tag: '近代',
      sort: 'file_size_desc',
      view: 'list',
    });
    const roundTripped = parseLibraryFilters(serializeLibraryFilters(original));
    expect(roundTripped).toEqual(original);
    expect(filtersEqual(roundTripped, original)).toBe(true);
  });
});

describe('buildApiParams', () => {
  it('与后端参数名逐一对应，分类不在这里出现', () => {
    const params = buildApiParams(
      filters({
        q: ' 历史 ',
        shelf: 'reading',
        category: '中国史',
        author: '张三',
        quality: 'issues',
        format: 'pdf',
        language: 'en',
        tag: '近代',
        sort: 'title_asc',
      }),
    );
    expect(params).toEqual({
      sort: 'title_asc',
      q: '历史',
      status: 'reading',
      author: '张三',
      quality: 'issues',
      format: 'pdf',
      language: 'en',
      tag: '近代',
    });
    expect(params.collection_id).toBeUndefined();
  });

  it('recent 传 created_desc 且不带 status', () => {
    const params = buildApiParams(filters({ shelf: 'recent', sort: 'title_asc' }));
    expect(params.sort).toBe('created_desc');
    expect(params.status).toBeUndefined();
  });
});

describe('活跃 chips 与清除', () => {
  it('每个非默认字段恰好一个 chip，sort/view 不算筛选', () => {
    const chips = buildActiveChips(
      filters({
        q: '历史',
        shelf: 'reading',
        category: '中国史',
        author: '张三',
        quality: 'issues',
        format: 'pdf',
        language: 'en',
        tag: '近代',
        sort: 'title_asc',
        view: 'list',
      }),
    );
    expect(chips.map((chip) => chip.key)).toEqual([
      'q',
      'shelf',
      'category',
      'author',
      'quality',
      'format',
      'language',
      'tag',
    ]);
  });

  it('recent 只有一个书架 chip，不生成 sort chip', () => {
    const chips = buildActiveChips(filters({ shelf: 'recent', sort: 'title_asc' }));
    expect(chips).toEqual([{ key: 'shelf', label: '书架：最近添加' }]);
  });

  it('默认筛选不算"已筛选"', () => {
    expect(isLibraryFiltered(filters({ sort: 'title_asc', view: 'list' }))).toBe(false);
    expect(isLibraryFiltered(filters({ tag: '近代' }))).toBe(true);
  });

  it('clear 保留 sort/view', () => {
    const cleared = clearLibraryFilters(
      filters({ q: 'x', shelf: 'done', tag: 't', sort: 'author_asc', view: 'list' }),
    );
    expect(cleared).toEqual(filters({ sort: 'author_asc', view: 'list' }));
  });
});
