'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ReactFlow, Background, Controls, type Edge, type Node } from '@xyflow/react';
import dagre from '@dagrejs/dagre';
import '@xyflow/react/dist/style.css';
import { Share2, RefreshCw, Unplug, Layers3, WandSparkles, ArrowUpRight } from 'lucide-react';
import { useQuery } from '@tanstack/react-query';
import { getGraph, GraphData } from '@/lib/api/client';
import { useDataSourceMode } from '@/lib/useDataSourceMode';

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

const EDGE_LABELS: Record<string, string> = {
  mention: '提及',
  cite: '引用',
  link: '链接',
};

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
  const router = useRouter();
  const { mode } = useDataSourceMode();
  const [selected, setSelected] = useState<string | null>(null);
  const [hiddenTypes, setHiddenTypes] = useState<Set<string>>(new Set());
  const [showOrphans, setShowOrphans] = useState(false);
  const [query, setQuery] = useState('');
  const [focusDepth, setFocusDepth] = useState<1 | 2 | 3>(1);

  const graphQuery = useQuery({
    queryKey: ['graph'],
    queryFn: getGraph,
    enabled: mode === 'api',
  });
  const data = graphQuery.data ?? null;
  const error = graphQuery.error ? (graphQuery.error as Error).message : null;

  const load = () => graphQuery.refetch();

  const { nodes, edges } = useMemo(() => (data ? layoutGraph(data) : { nodes: [], edges: [] }), [data]);

  // 原始节点索引：避免 visibleNodes/邻域计算里对每个节点做 O(N) find（整体 O(N²)）
  const rawNodeById = useMemo(() => new Map((data?.nodes ?? []).map((n) => [n.id, n])), [data]);

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
      const nodeData = rawNodeById.get(n.id);
      if (!nodeData || hiddenTypes.has(nodeData.type)) return false;
      if (!query.trim()) return true;
      const q = query.trim().toLowerCase();
      return nodeData.title.toLowerCase().includes(q) || nodeData.type.toLowerCase().includes(q);
    }),
    [nodes, rawNodeById, hiddenTypes, query],
  );
  const visibleIds = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes]);
  const visibleEdges = useMemo(
    () => edges.filter((e) => visibleIds.has(e.source) && visibleIds.has(e.target)),
    [edges, visibleIds],
  );

  const relatedIds = useMemo(() => {
    if (!selected || !data) return null;
    const frontier = new Set<string>([selected]);
    const visited = new Set<string>([selected]);
    for (let depth = 0; depth < focusDepth; depth += 1) {
      const next = new Set<string>();
      data.edges.forEach((e) => {
        if (frontier.has(e.source) && !visited.has(e.target)) next.add(e.target);
        if (frontier.has(e.target) && !visited.has(e.source)) next.add(e.source);
      });
      next.forEach((id) => visited.add(id));
      if (next.size === 0) break;
      frontier.clear();
      next.forEach((id) => frontier.add(id));
    }
    return visited;
  }, [selected, data, focusDepth]);

  // 关键：displayNodes/displayEdges 必须记忆化。此前每次渲染都新建数组，
  // ReactFlow 会视为全量变更，任意 setState 都触发内部逐节点失效重渲。
  const displayNodes = useMemo(
    () =>
      visibleNodes.map((n) => {
        let style = { ...n.style } as Record<string, unknown>;
        if (relatedIds) {
          style = { ...style, opacity: relatedIds.has(n.id) ? 1 : 0.2 };
          if (selected === n.id) {
            style = { ...style, boxShadow: '0 0 0 4px rgba(59,130,246,0.18)', transform: 'scale(1.03)' };
          }
        } else if (showOrphans) {
          if (orphanIds.has(n.id)) {
            style = { ...style, border: '2px dashed #f59e0b', boxShadow: '0 0 0 3px rgba(245, 158, 11, 0.15)' };
          } else {
            style = { ...style, opacity: 0.2 };
          }
        }
        return { ...n, style };
      }),
    [visibleNodes, relatedIds, selected, showOrphans, orphanIds],
  );

  const displayEdges = useMemo(
    () =>
      relatedIds
        ? visibleEdges.map((e) => ({
            ...e,
            label: EDGE_LABELS[e.type as keyof typeof EDGE_LABELS] || e.type,
            labelStyle: { fill: '#64748b', fontSize: 10 },
            style: {
              ...e.style,
              opacity: relatedIds.has(e.source) && relatedIds.has(e.target) ? 1 : 0.1,
              strokeWidth: selected && (e.source === selected || e.target === selected) ? 2.5 : 1.5,
            },
          }))
        : visibleEdges,
    [visibleEdges, relatedIds, selected],
  );

  const selectedNode = useMemo(() => rawNodeById.get(selected ?? '') ?? null, [rawNodeById, selected]);
  const selectedNeighbors = useMemo(() => {
    if (!selected || !data) return [];
    return data.edges
      .filter((e) => e.source === selected || e.target === selected)
      .map((e) => {
        const otherId = e.source === selected ? e.target : e.source;
        const other = rawNodeById.get(otherId);
        return other ? { ...other, via: e.type } : null;
      })
      .filter((x): x is { id: string; title: string; type: string; via: string } => Boolean(x));
  }, [selected, data, rawNodeById]);

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
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <div className="hidden sm:flex items-center gap-1 rounded border border-gray-200 bg-white px-1 py-0.5 text-xs">
            {([1, 2, 3] as const).map((depth) => (
              <button
                key={depth}
                onClick={() => setFocusDepth(depth)}
                className={`px-2 py-1 rounded ${focusDepth === depth ? 'bg-primary-600 text-white' : 'text-gray-600 hover:bg-gray-100'}`}
                title={`聚焦 ${depth} 层关联`}
              >
                {depth}跳
              </button>
            ))}
          </div>
          <div className="relative hidden md:block">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="筛选节点..."
              className="w-40 lg:w-56 px-3 py-1.5 text-xs rounded border border-gray-200 bg-white focus:outline-none focus:ring-2 focus:ring-primary-100"
            />
          </div>
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
          <div className="absolute inset-0 grid grid-cols-1 xl:grid-cols-[1fr_320px]">
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
            <aside className="hidden xl:flex flex-col border-l border-gray-200 bg-white/95 backdrop-blur-sm overflow-hidden">
              <div className="px-4 py-3 border-b border-gray-200">
                <div className="flex items-center gap-2 text-sm font-medium text-gray-900">
                  <Layers3 className="w-4 h-4 text-primary-500" />
                  结构洞察
                </div>
                <p className="text-xs text-gray-500 mt-1">点击节点后查看上下游连接、关系类型与可跳转入口</p>
              </div>
              {selectedNode ? (
                <div className="flex-1 overflow-y-auto p-4 space-y-4">
                  <div>
                    <p className="text-xs text-gray-400 mb-1">当前节点</p>
                    <h2 className="text-base font-semibold text-gray-900">{selectedNode.title}</h2>
                    <p className="text-xs text-gray-500 mt-1">{TYPE_LABELS[selectedNode.type] || selectedNode.type}</p>
                  </div>
                  <div>
                    <div className="flex items-center gap-2 text-sm font-medium text-gray-900 mb-2">
                      <WandSparkles className="w-4 h-4 text-primary-500" />
                      关联节点
                    </div>
                    {selectedNeighbors.length === 0 ? (
                      <p className="text-sm text-gray-400">没有找到直接关联项</p>
                    ) : (
                      <div className="space-y-2">
                        {selectedNeighbors.map((n) => (
                          <button
                            key={`${selectedNode.id}-${n.id}-${n.via}`}
                            onClick={() => router.push(['note', 'article', 'webclip'].includes(n.type) ? `/notes?open=${n.id}` : '/library')}
                            className="w-full text-left rounded-lg border border-gray-200 px-3 py-2 hover:bg-gray-50 transition-colors"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <p className="text-sm text-gray-800 truncate">{n.title}</p>
                              <ArrowUpRight className="w-3.5 h-3.5 text-gray-300 shrink-0" />
                            </div>
                            <p className="text-xs text-gray-400 mt-0.5">{TYPE_LABELS[n.type] || n.type} · {EDGE_LABELS[n.via] || n.via}</p>
                          </button>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              ) : (
                <div className="flex-1 flex items-center justify-center p-4 text-sm text-gray-400 text-center">
                  点击任意节点以查看它的上下游关系
                </div>
              )}
            </aside>
          </div>
        )}
      </div>

      {data && (
        <div className="flex items-center gap-3 px-4 py-2 border-t border-gray-200 bg-white text-xs text-gray-500 flex-wrap">
          <button
            onClick={() => {
              setSelected(null);
              setFocusDepth(1);
            }}
            className="px-2 py-1 rounded bg-gray-100 hover:bg-gray-200 text-gray-600"
          >
            重置聚焦
          </button>
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
          <span className="ml-auto text-gray-400">提示：节点大小代表连接数；点击节点聚焦相关连接，右侧会显示上下游关系解释</span>
        </div>
      )}
    </main>
  );
}
