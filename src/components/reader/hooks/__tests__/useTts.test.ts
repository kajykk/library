// @vitest-environment jsdom
/**
 * 用 TTS hook 的守卫行为：jsdom 无 speechSynthesis，supported 应为 false，
 * start/stop/togglePause/setRate 在任何环境下都不抛异常。
 */
import { renderHook, act } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { useTts } from '../useTts';

function noop() {
  return [] as HTMLElement[];
}

describe('useTts', () => {
  it('jsdom 环境下不支持朗读，start 为 no-op', () => {
    const { result } = renderHook(() => useTts(noop));
    expect(result.current.supported).toBe(false);
    expect(result.current.speaking).toBe(false);

    act(() => {
      result.current.start();
      result.current.togglePause();
      result.current.stop();
    });
    expect(result.current.speaking).toBe(false);
  });

  it('setRate 更新语速状态', () => {
    const { result } = renderHook(() => useTts(noop));
    act(() => result.current.setRate(1.5));
    expect(result.current.rate).toBe(1.5);
  });
});