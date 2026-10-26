// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createElement as h } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import StatePanel from '../StatePanel';
import FilterChip from '../FilterChip';

afterEach(() => {
  cleanup();
});

describe('StatePanel', () => {
  it('loading 变体渲染 status 角色与旋转图标', () => {
    render(h(StatePanel, { variant: 'loading', title: '加载中' }));
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText('加载中')).toBeInTheDocument();
  });

  it('empty 变体渲染 status 角色', () => {
    render(h(StatePanel, { variant: 'empty', title: '暂无数据', description: '还没有内容' }));
    expect(screen.getByRole('status')).toBeInTheDocument();
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
    expect(screen.getByText('还没有内容')).toBeInTheDocument();
  });

  it('error 变体渲染 alert 角色与重试按钮', () => {
    const onRetry = vi.fn();
    render(h(StatePanel, { variant: 'error', title: '出错了', action: { label: '重试', onClick: onRetry } }));
    expect(screen.getByRole('alert')).toBeInTheDocument();
    const btn = screen.getByRole('button', { name: '重试' });
    fireEvent.click(btn);
    expect(onRetry).toHaveBeenCalledTimes(1);
  });
});

describe('FilterChip', () => {
  it('渲染 label 与 count', () => {
    render(h(FilterChip, { label: 'PDF', count: 42 }));
    expect(screen.getByText('PDF')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('active 状态有 aria 可点击时是 button 角色', () => {
    const onClick = vi.fn();
    render(h(FilterChip, { label: '已读', active: true, onClick }));
    const chip = screen.getByRole('button', { name: /已读/ });
    fireEvent.click(chip);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it('onClear 清除按钮独立触发', () => {
    const onClear = vi.fn();
    const onClick = vi.fn();
    render(h(FilterChip, { label: '标签', onClear, onClick }));
    const clearBtn = screen.getByRole('button', { name: '清除 标签 筛选' });
    fireEvent.click(clearBtn);
    expect(onClear).toHaveBeenCalledTimes(1);
    expect(onClick).not.toHaveBeenCalled(); // stopPropagation
  });
});
