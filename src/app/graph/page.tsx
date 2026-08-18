'use client';

import { useEffect, useMemo, useState } from 'react';
import ReactFlow, { Background, Controls, Edge, Node } from 'reactflow';
import dagre from '@dagrejs/dagre';
import 'reactflow/dist/style.css';
import { Share2, RefreshCw, Unplug } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { getGraph, GraphData } from '@/lib/api/client';
import { resolveMode } from '@/lib/dataSource';

const TYPE_COLORS: Record<string, string> = {
  book: '#3b82f6',
  pdf: '#8b5cf6',
  note: '#f59e0b',
  article: '#10b981',
  webclip: '#ec4899',
};

const TYPE_LABELS: Record<string, string> = {
  book: '书籍',
  pdf: 'PDF',
  note: '笔记',
  article: '文章',
  webclip: '剪藏',
};

const NODE_WIDTH = 180;
const NODE_HEIGHT = 44;

function layoutGraph(data: GraphData): { nodes: Node[]; edges: Edge[] } {
  const g = new dagre.graphlib.Graph();
  g.setGraph({ rankdir: 'LR', nodesep: 40, ranksep: 80 });
  g.setDefaultEdgeLabel(() => ({}));

  data.nodes.forEach((n) => g.setNode(n.id, { width: NODE_WIDTH, height: NODE_HEIGHT }));
  data.edges.forEach((e) => g.setEdge(e.source, e.target));

  dagre.layout(g);

  const degree = new Map<string, number>();
  data.edges.forEach((e) => {
    degree.set(e.source, (degree.get(e.source) || 0) + 1);
    degree.set(e.target, (degree.get(e.target) || 0) + 1);
  });

  const nodes: Node[] = data.nodes.map((n) => {
    const d = degree.get(n.id) || 0;
    const width = Math.min(NODE_WIDTH + d * 10, 300);
    return {
      id: n.id,
      position: { x: g.node(n.id).x - width / 2, y: g.node(n.id).y - NODE_HEIGHT / 2 },
      data: { label: d > 0 ? `${n.title} (${d})` : n.title },
      style: {
        borderRadius: '8px',
        border: `2px solid ${TYPE_COLORS[n.type] || '#94a3b8'}`,
        padding: '4px 10px',
        fontSize: '12px',
        width,
      },
    };
  });

  const edges: Edge[] = data.edges.map((e) => ({
    id: `${e.source}-${e.target}-${e.type}`,
    source: e.source,
    target: e.target,
    animated: e.type === 'mention',
    style: { stroke: e.type === 'mention' ? '#f59e0b' : '#94a3b8', strokeWidth: 1.5 },
  }));

  return { nodes, edges };
}

export default function GraphPage() {
  const [mode, setMode] = useState<'api' | 'local' | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  const [showOrphans, setShowOrphans] = useState(false);

  useEffect(() => {
    resolveMode().then(setMode);
  }, []);

  const graphQuery = useQuery({
    queryKey: ['graph'],
    queryFn: getGraph,
    enabled: mode === 'api',
  });
  const data = graphQuery.data ?? null;
  const error = graphQuery.error ? (graphQuery.error as Error).message : null;

  const load = () => graphQuery.refetch();

  const { nodes, edges } = useMemo(() => (data ? layoutGraph(data) : { nodes: [], edges: [] }), [data]);

  const orphanIds = useMemo(() => {
    if (!data) return new Set<string>();
    const degree = new Map<string, number>();
    data.edges.forEach((e) => {
      degree.set(e.source, (degree.get(e.source) || 0) + 1);
      degree.set(e.target, (degree.get(e.target) || 0) + 1);
    });
    return new Set(data.nodes.filter((n) => !degree.has(n.id)).map((n) => n.id));
  }, [data]);

  const visibleNodes = useMemo(
    () => nodes.filter((n) => {
      const nodeData = data?.nodes.find((raw) => raw.id === n.id);
      return nodeData && !hiddenTypes.has(nodeData.type);
    }),
    [nodes, data, hiddenTypes],
  );
  const visibleIds = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () => edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target)),
    [edges, visibleIds],
  );

  const relatedIds = useMemo(() => {
    if (!selected || !data) return null;
    const set = new Set<string>([selected]);
    data.edges.forEach((e) => {
      if (e.source === selected) set.add(e.target);
      if (e.target === selected) set.add(e.source);
    });
    return set;
  }, [selected, data]);

  const displayNodes = visibleNodes.map((n) => {
    let style = { ...n.style } as Record<string, unknown>;
    if (relatedIds) {
      style = { ...style, opacity: relatedIds.has(n.id) ? 1 : 0.2 };
    } else if (showOrphans) {
      if (orphanIds.has(n.id)) {
        style = { ...style, border: '2px dashed #f59e0b', boxShadow: '0 0 0 3px rgba(245, 158, 11, 0.15)' };
      } else {
        style = { ...style, opacity: 0.2 };
      }
    }
    return { ...n, style };
  });
  const displayEdges = relatedIds
    ? visibleEdges.map((e) => ({
        ...e,
        style: {
          ...e.style,
          opacity: relatedIds.has(e.source) && relatedIds.has(e.target) ? 1 : 0.1,
        },
      }))
    : visibleEdges;

  const toggleType = (type: string) => {
    setHiddenTypes((prev) => {
      const next = new Set(prev);
      if (next.has(type)) next.delete(type);
      else next.add(type);
      return next;
    });
  };

  return (
    <main className="h-[calc(100vh-2.5rem)] flex flex-col">
      <div className="flex items-center justify-between px-4 sm:px-6 lg:px-8 py-3 border-b border-gray-200 bg-white">
        <div className="flex items-center gap-2">
          <Share2 className="w-5 h-5 text-primary-600" />
          <h1 className="text-lg font-bold text-gray-900">知识图谱</h1>
          {data && (
            <span className="text-xs text-gray-400">
              {data.nodes.length} 节点 · {data.edges.length} 连接
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
          {data && (
            <button
              onClick={() => setShowOrphans((v) => !v)}
              className={`flex items-center gap-1 text-xs px-2 py-1 rounded border transition-colors ${
                showOrphans
                  ? 'bg-amber-50 border-amber-300 text-amber-700'
                  : 'bg-gray-100 border-transparent text-gray-600 hover:bg-gray-200'
              }`}
              title="突出显示没有任何连接的孤立文档"
            >
              <Unplug className="w-3 h-3" />
              孤立节点（{orphanIds.size}）
            </button>
          )}
          {selected && (
            <button
              onClick={() => setSelected(null)}
              className="text-xs px-2 py-1 rounded bg-gray-100 text-gray-600 hover:bg-gray-200"
            >
              显示全部
            </button>
          )}
          <button
            onClick={load}
            className="flex items-center gap-1 text-xs px-2 py-1 rounded bg-gray-100 text-gray-600 hover:bg-gray-200"
          >
            <RefreshCw className="w-3 h-3" />
            刷新
          </button>
        </div>
      </div>

      <div className="flex-1 relative">
        {mode === 'local' ? (
          <div className="absolute inset-0 flex items-center justify-center text-gray-500">
            知识图谱需要连接后端服务，请先在设置页配置
          </div>
        ) : error ? (
          <div className="absolute inset-0 flex items-center justify-center text-red-600">{error}</div>
        ) : !data ? (
          <div className="absolute inset-0 flex items-center justify-center text-gray-400">加载中...</div>
        ) : data.nodes.length === 0 ? (
          <div className="absolute inset-0 flex items-center justify-center text-gray-500">
            暂无文档，先去书架或笔记添加内容吧
          </div>
        ) : (
          <ReactFlow
            nodes={displayNodes}
            edges={displayEdges}
            onNodeClick={(_, node) => setSelected(node.id === selected ? null : node.id)}
            fitView
            minZoom={0.2}
          >
            <Background gap={20} size={1} />
            <Controls />
          </ReactFlow>
        )}
      </div>

      {data && (
        <div className="flex items-center gap-3 px-4 py-2 border-t border-gray-200 bg-white text-xs text-gray-500 flex-wrap">
          {Object.entries(TYPE_LABELS).map(([type, label]) => (
            <button
              key={type}
              onClick={() => toggleType(type)}
              className={`flex items-center gap-1 px-2 py-1 rounded transition-colors ${
                hiddenTypes.has(type) ? 'opacity-30 line-through' : 'hover:bg-gray-100'
              }`}
              title="点击筛选该类型"
            >
              <span className="w-2.5 h-2.5 rounded-full" style={{ background: TYPE_COLORS[type] }} />
              {label}
            </button>
          ))}
          <span className="ml-auto text-gray-400">提示：节点大小代表连接数；点击节点聚焦相关连接</span>
        </div>
      )}
    </main>
  );
}
