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
 * Y.Doc 是纯本地对象，随组件实例惰性创建（供 Collaboration 扩展同步绑定）；
 * WebSocket Provider 是网络资源，在 useEffect 中创建、清理函数中销毁，
 * React StrictMode 双载（mount → cleanup → mount）下旧连接会被完整回收，
 * 第二次挂载重建新 Provider，不会泄漏连接或重复订阅。
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
      setProvider(null);
    };
  }, [docId]);

  return { ydoc: ydocRef.current, provider, status, onlineUsers };
}
