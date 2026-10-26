import { describe, expect, it } from 'vitest';
import { normalizeLanguageCode, languageLabel } from '../language';

describe('normalizeLanguageCode', () => {
  it('别名归一化为两字母码', () => {
    expect(normalizeLanguageCode('en-US')).toBe('en');
    expect(normalizeLanguageCode('ENG')).toBe('en');
    expect(normalizeLanguageCode('zh-TW')).toBe('zh');
    expect(normalizeLanguageCode('zh-Hant')).toBe('zh');
    expect(normalizeLanguageCode('JPN')).toBe('ja');
  });

  it('空值归到 und', () => {
    expect(normalizeLanguageCode('')).toBe('und');
    expect(normalizeLanguageCode('   ')).toBe('und');
    expect(normalizeLanguageCode(null)).toBe('und');
    expect(normalizeLanguageCode(undefined)).toBe('und');
  });

  it('未知码保留小写原值，不误归 und', () => {
    expect(normalizeLanguageCode('fr')).toBe('fr');
    expect(normalizeLanguageCode('PT-BR')).toBe('pt-br');
  });

  it('给已知码提供中文标签，未知码原样显示', () => {
    expect(languageLabel('zh')).toBe('中文');
    expect(languageLabel('und')).toBe('未标注');
    expect(languageLabel('pt')).toBe('葡萄牙语');
    expect(languageLabel('xx')).toBe('xx');
  });
});
