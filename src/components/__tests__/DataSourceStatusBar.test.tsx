// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createElement as h } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Mock useDataSourceStatus
const mockStatus = vi.fn();
const mockRefresh = vi.fn();
vi.mock('@/lib/useDataSourceStatus', () => ({
  useDataSourceStatus: () => ({
    status: mockStatus(),
    isResolving: false,
    refresh: mockRefresh,
  }),
}));

import DataSourceStatusBar from '../DataSourceStatusBar';

afterEach(() => {
  cleanup();
  sessionStorage.clear();
  vi.clearAllMocks();
});

describe('DataSourceStatusBar', () => {
  it('api 模式不渲染', () => {
    mockStatus.mockReturnValue({
      mode: 'api', reason: 'api_ok', baseUrl: 'http://localhost:8000',
      tokenConfigured: true, health: null,
    });
    const { container } = render(h(DataSourceStatusBar));
    expect(container.firstChild).toBeNull();
  });

  it('local/no_token 显示蓝色信息条', () => {
    mockStatus.mockReturnValue({
      mode: 'local', reason: 'no_token', baseUrl: 'http://localhost:8000',
      tokenConfigured: false, health: null,
    });
    render(h(DataSourceStatusBar));
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText(/未连接后端/)).toBeInTheDocument();
  });

  it('degraded/health_failed 显示琥珀色警告条与重试按钮', () => {
    mockStatus.mockReturnValue({
      mode: 'degraded', reason: 'health_failed', baseUrl: 'http://localhost:8000',
      tokenConfigured: true, health: null,
    });
    render(h(DataSourceStatusBar));
    expect(screen.getByRole('alert')).toBeInTheDocument();
    expect(screen.getByText(/后端不可达/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /重试/ })).toBeInTheDocument();
  });

  it('checking 状态不渲染', () => {
    mockStatus.mockReturnValue({
      mode: 'local', reason: 'checking', baseUrl: '',
      tokenConfigured: true, health: null,
    });
    const { container } = render(h(DataSourceStatusBar));
    expect(container.firstChild).toBeNull();
  });

  it('null status 不渲染', () => {
    mockStatus.mockReturnValue(null);
    const { container } = render(h(DataSourceStatusBar));
    expect(container.firstChild).toBeNull();
  });

  it('点击关闭后写 sessionStorage', () => {
    mockStatus.mockReturnValue({
      mode: 'local', reason: 'no_token', baseUrl: 'http://localhost:8000',
      tokenConfigured: false, health: null,
    });
    render(h(DataSourceStatusBar));
    fireEvent.click(screen.getByRole('button', { name: '关闭提示' }));
    expect(sessionStorage.getItem('kb_status_bar_dismissed')).toBe('1');
  });

  it('degraded 重试调用 refresh', () => {
    mockStatus.mockReturnValue({
      mode: 'degraded', reason: 'health_failed', baseUrl: 'http://localhost:8000',
      tokenConfigured: true, health: null,
    });
    render(h(DataSourceStatusBar));
    fireEvent.click(screen.getByRole('button', { name: /重试/ }));
    expect(mockRefresh).toHaveBeenCalled();
  });

  it('不显示 token 文本', () => {
    mockStatus.mockReturnValue({
      mode: 'degraded', reason: 'health_failed', baseUrl: 'http://localhost:8000',
      tokenConfigured: true, health: null,
    });
    render(h(DataSourceStatusBar));
    expect(screen.queryByText(/token/i)).not.toBeInTheDocument();
  });
});
