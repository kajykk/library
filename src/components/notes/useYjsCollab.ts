'use client';

import { useEffect, useRef, useState } from 'react';
import * as Y from 'yjs';
import { WebsocketProvider } from 'y-websocket';
import { getApiToken, getCollabWsUrl } from '@/lib/api/client';

export type CollabStatus = 'connecting' | 'connected' | 'offline';

export interface OnlineUser {
  clientId: number;
  name: string;
}

/**
 * Yjs 实时协作会话。
 *
 * WebSocket Provider 是网络资源、Y.Doc 是本地资源，在清理函数中成对销毁。
 * React StrictMode 双载（mount → cleanup → mount）下旧连接与旧 Doc 都会被
 * 完整回收（cleanup 中 destroy + 置空），重挂载时按需重建新实例，
 * 不会泄漏连接、Doc 或重复订阅。
 */
export function useYjsCollab(docId: string): {
  ydoc: Y.Doc;
  provider: WebsocketProvider | null;
  status: CollabStatus;
  onlineUsers: OnlineUser[];
} {
  const ydocRef = useRef<Y.Doc | null>(null);
  if (!ydocRef.current) {
    ydocRef.current = new Y.Doc();
  }

  const [provider, setProvider] = useState<WebsocketProvider | null>(null);
  const [status, setStatus] = useState<CollabStatus>('connecting');
  const [onlineUsers, setOnlineUsers] = useState<OnlineUser[]>([]);

  useEffect(() => {
    if (!ydocRef.current) {
      ydocRef.current = new Y.Doc();
    }
    const ydoc = ydocRef.current as Y.Doc;
    const wsProvider = new WebsocketProvider(getCollabWsUrl(docId), `doc-${docId}`, ydoc, {
      connect: true,
      params: { token: getApiToken() },
    });

    const onStatus = ({ status: next }: { status: string }) => {
      setStatus(next === 'connected' ? 'connected' : next === 'connecting' ? 'connecting' : 'offline');
    };
    const refreshUsers = () => {
      const awareness = wsProvider.awareness;
      const myId = awareness.clientID;
      const users: OnlineUser[] = [];
      awareness.getStates().forEach((state, clientId) => {
        if (clientId === myId) return;
        const user = (state as Record<string, { name?: string }>)?.user;
        users.push({ clientId, name: user?.name || `用户 ${String(clientId).slice(-4)}` });
      });
      setOnlineUsers(users);
    };

    wsProvider.awareness.setLocalStateField('user', {
      name:
        typeof window !== 'undefined'
          ? window.localStorage.getItem('kb_user_name') || '未命名用户'
          : '未命名用户',
    });
    wsProvider.awareness.on('change', refreshUsers);
    refreshUsers();
    wsProvider.on('status', onStatus);
    setProvider(wsProvider);

    return () => {
      wsProvider.awareness.off('change', refreshUsers);
      wsProvider.off('status', onStatus);
      wsProvider.destroy();
      // Doc 与 Provider 成对销毁：否则每打开一篇笔记泄漏一个
      // Y.Doc（更新日志 + 向量时钟），长时间使用内存持续上涨。
      ydoc.destroy();
      ydocRef.current = null;
      setProvider(null);
    };
  }, [docId]);

  // 消费方（NoteEditor）在 provider 就绪前渲染占位、不解引用 ydoc。
  // 函数体顶部的惰性初始化保证每次渲染时 ref 均非空，断言安全。
  return { ydoc: ydocRef.current as Y.Doc, provider, status, onlineUsers };
}
