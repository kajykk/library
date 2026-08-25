// @vitest-environment jsdom
import { StrictMode } from 'react';
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import * as Y from 'yjs';
import { useYjsCollab } from '../useYjsCollab';

vi.mock('@/lib/api/client', () => ({
  getApiToken: () => 'test-token',
  getCollabWsUrl: (id: string) => `ws://collab.test/${id}`,
}));

// 按实际导出边界 mock y-websocket（WebsocketProvider），记录构造/销毁/事件监听
const h = vi.hoisted(() => ({
  instances: [] as any[],
}));

vi.mock('y-websocket', () => ({
  WebsocketProvider: class {
    url: string;
    roomname: string;
    doc: unknown;
    opts: Record<string, unknown>;
    destroyed = false;
    handlers: Record<string, Array<(payload?: unknown) => void>> = {};
    awareness;

    constructor(url: string, roomname: string, doc: unknown, opts: Record<string, unknown>) {
      this.url = url;
      this.roomname = roomname;
      this.doc = doc;
      this.opts = opts;
      const self = this;
      this.awareness = {
        clientID: 4242,
        peers: new Map<number, { user?: { name?: string } }>(),
        localField: null as Record<string, unknown> | null,
        handlers: {} as Record<string, Array<() => void>>,
        setLocalStateField(field: string, value: unknown) {
          this.localField = { [field]: value };
        },
        getStates() {
          const m = new Map<number, unknown>(this.peers);
          if (this.localField) m.set(this.clientID, this.localField);
          return m;
        },
        on(evt: string, cb: () => void) {
          (this.handlers[evt] ||= []).push(cb);
        },
        off(evt: string, cb: () => void) {
          this.handlers[evt] = (this.handlers[evt] || []).filter((f) => f !== cb);
        },
        emit(evt: string) {
          (this.handlers[evt] || []).forEach((cb) => cb());
        },
      };
      void self;
      h.instances.push(this);
    }

    on(evt: string, cb: (payload?: unknown) => void) {
      (this.handlers[evt] ||= []).push(cb);
    }

    off(evt: string, cb: (payload?: unknown) => void) {
      this.handlers[evt] = (this.handlers[evt] || []).filter((f) => f !== cb);
    }

    destroy() {
      this.destroyed = true;
    }

    emitStatus(status: string) {
      (this.handlers.status || []).forEach((cb) => cb({ status }));
    }
  },
}));

function lastInstance() {
  return h.instances[h.instances.length - 1];
}

describe('useYjsCollab', () => {
  beforeEach(() => {
    h.instances.length = 0;
    window.localStorage.clear();
  });

  it('单挂载只建一个连接：url/room/token 边界参数正确且暴露当前实例', () => {
    const { result } = renderHook(() => useYjsCollab('note-9'));

    expect(h.instances).toHaveLength(1);
    const inst = h.instances[0];
    expect(inst.destroyed).toBe(false);
    expect(inst.url).toBe('ws://collab.test/note-9');
    expect(inst.roomname).toBe('doc-note-9');
    expect(inst.opts.connect).toBe(true);
    expect(inst.opts.params.token).toBe('test-token');
    expect(result.current.provider).toBe(inst);
    expect(result.current.ydoc).toBeInstanceOf(Y.Doc);
    // 本地 awareness 用户名回退默认值
    expect(inst.awareness.localField.user.name).toBe('未命名用户');
  });

  it('StrictMode 双挂载：首个 Provider 即被 destroy、仅一条存活连接，卸载后全部回收', () => {
    const view = renderHook(() => useYjsCollab('note-1'), { wrapper: StrictMode });

    expect(h.instances).toHaveLength(2);
    expect(h.instances[0].destroyed).toBe(true);
    expect(h.instances[1].destroyed).toBe(false);
    // 第一条连接的事件监听在清理时被移除
    expect(h.instances[0].handlers.status).toHaveLength(0);
    expect(h.instances[0].awareness.handlers.change).toHaveLength(0);

    expect(view.result.current.provider).toBe(h.instances[1]);
    expect(view.result.current.ydoc).toBeInstanceOf(Y.Doc);

    view.unmount();
    expect(h.instances.every((i) => i.destroyed)).toBe(true);
    // 卸载后组件不再渲染，result 保留的是已被 destroy 的最后实例引用
    expect(view.result.current.provider).toBe(lastInstance());
    expect(h.instances.at(-1)?.destroyed).toBe(true);
  });

  it('provider status 事件映射为 connecting/connected/offline 状态', () => {
    const { result } = renderHook(() => useYjsCollab('n'));
    const inst = lastInstance();

    expect(result.current.status).toBe('connecting');
    act(() => inst.emitStatus('connected'));
    expect(result.current.status).toBe('connected');
    act(() => inst.emitStatus('disconnected'));
    expect(result.current.status).toBe('offline');
  });

  it('awareness change 派生在线用户列表：排除自身并回退匿名昵称', () => {
    window.localStorage.setItem('kb_user_name', '测试者');
    const { result } = renderHook(() => useYjsCollab('n'));
    const aw = lastInstance().awareness;

    aw.peers.set(1, { user: { name: 'Alice' } });
    aw.peers.set(12345, {});
    act(() => aw.emit('change'));

    expect(result.current.onlineUsers).toEqual([
      { clientId: 1, name: 'Alice' },
      { clientId: 12345, name: '用户 2345' },
    ]);
    // 本地字段使用 localStorage 中配置的用户名
    expect(aw.localField.user.name).toBe('测试者');
  });
});
