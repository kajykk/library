// @vitest-environment jsdom
import '@testing-library/jest-dom/vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { createElement as h } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('next/navigation', () => ({
  usePathname: () => '/library',
}));

import Nav from '../Nav';

afterEach(cleanup);

describe('Nav', () => {
  it('包含 /import、/quality 与 /queue 入口', () => {
    render(h(Nav));
    expect(screen.getByRole('link', { name: '导入' })).toHaveAttribute('href', '/import');
    expect(screen.getByRole('link', { name: '质量' })).toHaveAttribute('href', '/quality');
    expect(screen.getByRole('link', { name: '队列' })).toHaveAttribute('href', '/queue');
  });

  it('移动端只显示图标，标签在 sm 以上显示', () => {
    render(h(Nav));
    const importLabel = screen.getByText('导入');
    expect(importLabel.className).toContain('hidden');
    expect(importLabel.className).toContain('sm:inline');
  });
});
