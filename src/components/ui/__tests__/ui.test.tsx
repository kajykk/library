// @vitest-environment jsdom
// 说明：项目 tsconfig 为 jsx:"preserve"（Next 约定），vitest 的 esbuild 不转换
// 测试文件中的 JSX 字面量，故本文件统一使用 createElement（children 走 props）。
import '@testing-library/jest-dom/vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { createElement as h, useEffect, useState } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import Modal from '../Modal';
import Drawer from '../Drawer';
import { DialogProvider, useDialog, useToast, ToastProvider } from '../index';

// 未开启 vitest globals，RTL 的自动清理不会注册：手动清理避免跨用例 DOM 累积
afterEach(() => {
  cleanup();
});

describe('Modal', () => {
  it('open=false 时不渲染', () => {
    render(h(Modal, { open: false, onClose: () => undefined, children: h('div', null, '内容') }));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('渲染 dialog 角色 + aria-modal，Escape 触发 onClose', () => {
    const onClose = vi.fn();
    render(
      h(Modal, {
        open: true,
        onClose,
        ariaLabel: '测试对话框',
        children: h('button', { type: 'button' }, '确定'),
      }),
    );
    expect(screen.getByRole('dialog')).toHaveAttribute('aria-modal', 'true');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('Tab 在面板内循环（焦点陷阱）', () => {
    render(
      h(Modal, {
        open: true,
        onClose: () => undefined,
        ariaLabel: 't',
        children: [
          h('button', { type: 'button', key: 'a' }, '第一个'),
          h('button', { type: 'button', key: 'b' }, '第二个'),
        ],
      }),
    );
    const buttons = screen.getAllByRole('button');
    const lastButton = buttons[buttons.length - 1];
    if (!lastButton) throw new Error('应存在两个按钮');
    lastButton.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    // 从最后一个 Tab 应回到第一个
    expect(document.activeElement).toBe(buttons[0]);
  });

  it('关闭时归还焦点到打开前元素', () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return h('div', null,
        h('button', { type: 'button', onClick: () => setOpen(true) }, '打开'),
        h(Modal, {
          open,
          onClose: () => setOpen(false),
          ariaLabel: 'm',
          children: h('button', { type: 'button', onClick: () => setOpen(false) }, '关闭'),
        }),
      );
    }
    render(h(Harness));
    const opener = screen.getByText('打开');
    opener.focus();
    fireEvent.click(opener);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    fireEvent.click(screen.getByText('关闭'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.activeElement).toBe(opener);
  });
});

describe('DialogProvider（Promise 化 confirm/prompt）', () => {
  it('confirm 解析为 true/false', async () => {
    let result: boolean | undefined;
    let result2: boolean | undefined;

    function Host({ target }: { target: (v: boolean) => void }) {
      const dialog = useDialog();
      useEffect(() => {
        void dialog.confirm({ message: '确认操作？' }).then(target);
      }, [dialog, target]);
      return null;
    }

    const t1 = (v: boolean) => {
      result = v;
    };
    const { unmount } = render(h(DialogProvider, { children: h(Host, { target: t1 }) }));
    await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: '确定' }));
    await act(async () => {});
    expect(result).toBe(true);
    unmount();

    const t2 = (v: boolean) => {
      result2 = v;
    };
    render(h(DialogProvider, { children: h(Host, { target: t2 }) }));
    await act(async () => {});
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    await act(async () => {});
    expect(result2).toBe(false);
  });

  it('prompt 提交输入值；Enter 直提', async () => {
    let result: string | null | undefined;
    function Host() {
      const dialog = useDialog();
      useEffect(() => {
        void dialog.prompt({ message: '输入内容', initial: '初值' }).then((r) => {
          result = r;
        });
      }, [dialog]);
      return null;
    }
    render(h(DialogProvider, { children: h(Host) }));
    await act(async () => {});
    const input = screen.getByRole('textbox');
    expect(input).toHaveValue('初值');
    fireEvent.change(input, { target: { value: '新批注' } });
    fireEvent.keyDown(input, { key: 'Enter' });
    await act(async () => {});
    expect(result).toBe('新批注');
  });

  it('Escape 取消 prompt 返回 null 且 Promise 必然 settle', async () => {
    let result: string | null | undefined;
    function Host() {
      const dialog = useDialog();
      useEffect(() => {
        void dialog.prompt({ message: 'x' }).then((r) => {
          result = r;
        });
      }, [dialog]);
      return null;
    }
    render(h(DialogProvider, { children: h(Host) }));
    await act(async () => {});
    fireEvent.keyDown(document, { key: 'Escape' });
    await act(async () => {});
    expect(result).toBeNull();
  });
});

describe('Toast', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('显示并自动消失', async () => {
    function Host() {
      const toast = useToast();
      return h('button', { type: 'button', onClick: () => toast.error('出错了') }, 'trigger');
    }
    render(h(ToastProvider, { children: h(Host) }));
    fireEvent.click(screen.getByText('trigger'));
    expect(screen.getByTestId('toast-error')).toHaveTextContent('出错了');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(4500);
    });
    expect(screen.queryByTestId('toast-error')).not.toBeInTheDocument();
  });
});

describe('Drawer', () => {
  it('渲染抽屉与 Escape 关闭', () => {
    const onClose = vi.fn();
    render(
      h(Drawer, { open: true, onClose, ariaLabel: '历史', children: h('div', null, '版本列表') }),
    );
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(screen.getByText('版本列表')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
