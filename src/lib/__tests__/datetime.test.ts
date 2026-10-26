import { describe, expect, it } from 'vitest';
import { localDayKey, localDayStartIso, startOfLocalDay } from '../datetime';

describe('datetime 本地日界', () => {
  it('localDayKey 用本地年月日，跨午夜正确换日', () => {
    expect(localDayKey(new Date(2026, 0, 1, 0, 0, 0))).toBe('2026-01-01');
    expect(localDayKey(new Date(2026, 0, 1, 23, 59, 59))).toBe('2026-01-01');
    expect(localDayKey(new Date(2026, 0, 2, 0, 0, 1))).toBe('2026-01-02');
  });

  it('startOfLocalDay 归零到本地零点', () => {
    const start = startOfLocalDay(new Date(2026, 5, 16, 8, 30, 15, 250));
    expect(start.getFullYear()).toBe(2026);
    expect(start.getMonth()).toBe(5);
    expect(start.getDate()).toBe(16);
    expect(start.getHours()).toBe(0);
    expect(start.getMinutes()).toBe(0);
    expect(start.getSeconds()).toBe(0);
    expect(start.getMilliseconds()).toBe(0);
  });

  it('localDayStartIso 是本地零点对应的 UTC 时刻', () => {
    const afternoon = new Date(2026, 5, 16, 15, 0, 0);
    expect(new Date(localDayStartIso(afternoon)).getTime()).toBe(
      startOfLocalDay(afternoon).getTime(),
    );
  });

  it('跨年边界不会算错日期', () => {
    expect(localDayKey(new Date(2025, 11, 31, 23, 0))).toBe('2025-12-31');
    expect(localDayKey(new Date(2026, 0, 1, 0, 30))).toBe('2026-01-01');
  });
});
