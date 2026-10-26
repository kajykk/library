// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createElement as h } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import OnboardingBanner from '../OnboardingBanner';

afterEach(() => {
  cleanup();
});

describe('OnboardingBanner', () => {
  it('渲染三步引导', () => {
    render(h(OnboardingBanner, { onDismiss: () => {} }));
    expect(screen.getByText('开始使用你的知识库')).toBeInTheDocument();
    expect(screen.getByText(/启动后端服务/)).toBeInTheDocument();
    expect(screen.getByText(/打开设置/)).toBeInTheDocument();
    expect(screen.getByText(/保存并测试/)).toBeInTheDocument();
  });

  it('点击「以后再说」触发 onDismiss', () => {
    const onDismiss = vi.fn();
    render(h(OnboardingBanner, { onDismiss }));
    fireEvent.click(screen.getByRole('button', { name: '以后再说' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('关闭按钮触发 onDismiss', () => {
    const onDismiss = vi.fn();
    render(h(OnboardingBanner, { onDismiss }));
    fireEvent.click(screen.getByRole('button', { name: '不再提示' }));
    expect(onDismiss).toHaveBeenCalledTimes(1);
  });

  it('有「去设置」链接', () => {
    render(h(OnboardingBanner, { onDismiss: () => {} }));
    expect(screen.getByRole('link', { name: '去设置' })).toHaveAttribute('href', '/settings');
  });
});
