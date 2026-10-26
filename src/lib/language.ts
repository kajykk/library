/**
 * 语言码归一化：与后端 services/sql_utils.py 保持同一口径。
 * 分面统计按归一化值分组，前端本地过滤与 URL 解析也必须用同一份映射，
 * 否则 `en-US` 在页码筛选里命中、在本地过滤里却漏掉。
 */

const LANGUAGE_ALIASES: Record<string, string> = {
  'en-us': 'en',
  'en-gb': 'en',
  eng: 'en',
  'zh-tw': 'zh',
  'zh-hk': 'zh',
  'zh-hans': 'zh',
  'zh-hant': 'zh',
  cht: 'zh',
  chs: 'zh',
  'ja-jp': 'ja',
  jpn: 'ja',
};

export const UNDETERMINED_LANGUAGE = 'und';

/** 空/NULL → `und`；已知别名 → 两字母码；未知值保留小写原值。 */
export function normalizeLanguageCode(value: string | null | undefined): string {
  const key = (value || '').trim().toLowerCase();
  if (!key) return UNDETERMINED_LANGUAGE;
  return LANGUAGE_ALIASES[key] ?? key;
}

const LANGUAGE_LABELS: Record<string, string> = {
  zh: '中文',
  en: '英语',
  ja: '日语',
  und: '未标注',
  fr: '法语',
  de: '德语',
  es: '西班牙语',
  ru: '俄语',
  pt: '葡萄牙语',
  ko: '韩语',
  it: '意大利语',
};

export function languageLabel(code: string): string {
  return LANGUAGE_LABELS[code] ?? code;
}
