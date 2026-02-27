'use client';

import React, { useCallback, useMemo, useState, useEffect } from 'react';
import {
  ReactFlow,
  Controls,
  Background,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
  MarkerType,
  BackgroundVariant,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';

// ─── Types matching API response ─────────────────────────────────────────────

interface RepoNode {
  path: string;
  summary: string;
  tech_stack: string[];
  related: string[];
}

interface RelationEdge {
  from: string;
  to: string;
  type: string;
  description: string;
}

interface RelationsData {
  analyzed_at: string | null;
  repos: Record<string, RepoNode>;
  edges: RelationEdge[];
  mermaid: string;
}

// ─── Edge color / style mapping ──────────────────────────────────────────────

export const EDGE_STYLES: Record<string, { color: string; strokeDasharray?: string; animated?: boolean }> = {
  depends_on:        { color: '#0071e3' },
  likely_depends_on: { color: '#ff9f0a', strokeDasharray: '6 3' },
  provides_api_for:  { color: '#30d158' },
  shares_protocol:   { color: '#bf5af2' },
  related:           { color: '#86868b', strokeDasharray: '2 2' },
};

function getEdgeStyle(type: string) {
  return EDGE_STYLES[type] || EDGE_STYLES.related;
}

// ─── Dagre layout ────────────────────────────────────────────────────────────

function layoutElements(
  nodes: Node[],
  edges: Edge[],
  direction: 'LR' | 'TB' = 'LR',
  nodesep = 60,
  ranksep = 120,
) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep, ranksep });

  nodes.forEach((node) => {
    const w = node.type === 'groupNode' ? 280 : 220;
    const h = node.type === 'groupNode' ? 90 : 80;
    g.setNode(node.id, { width: w, height: h });
  });

  edges.forEach((edge) => {
    if (g.hasNode(edge.source) && g.hasNode(edge.target)) {
      g.setEdge(edge.source, edge.target);
    }
  });

  dagre.layout(g);

  const laidOut = nodes.map((node) => {
    const pos = g.node(node.id);
    const w = node.type === 'groupNode' ? 280 : 220;
    const h = node.type === 'groupNode' ? 90 : 80;
    return {
      ...node,
      position: {
        x: pos.x - w / 2,
        y: pos.y - h / 2,
      },
    };
  });

  return { nodes: laidOut, edges };
}

// ─── Grouping helpers ────────────────────────────────────────────────────────

interface GroupInfo {
  repos: string[];
  repoCount: number;
}

interface GroupEdge {
  from: string;
  to: string;
  count: number;
  types: string[];
}

function groupByPrefix(
  repos: Record<string, RepoNode>,
  edges: RelationEdge[],
  depth = 2,
): { groups: Map<string, GroupInfo>; groupEdges: GroupEdge[] } {
  const groups = new Map<string, GroupInfo>();

  // Assign each repo to a group based on path prefix
  for (const path of Object.keys(repos)) {
    const parts = path.split('/');
    const prefix = parts.length <= depth ? parts.join('/') : parts.slice(0, depth).join('/');
    const existing = groups.get(prefix);
    if (existing) {
      existing.repos.push(path);
      existing.repoCount++;
    } else {
      groups.set(prefix, { repos: [path], repoCount: 1 });
    }
  }

  // Build repo → group lookup
  const repoToGroup = new Map<string, string>();
  for (const [groupId, info] of groups) {
    for (const repo of info.repos) {
      repoToGroup.set(repo, groupId);
    }
  }

  // Aggregate edges between groups
  const edgeMap = new Map<string, { count: number; types: Set<string> }>();
  for (const edge of edges) {
    const fromGroup = repoToGroup.get(edge.from);
    const toGroup = repoToGroup.get(edge.to);
    if (!fromGroup || !toGroup || fromGroup === toGroup) continue;
    const key = `${fromGroup}→${toGroup}`;
    const existing = edgeMap.get(key);
    if (existing) {
      existing.count++;
      existing.types.add(edge.type);
    } else {
      edgeMap.set(key, { count: 1, types: new Set([edge.type]) });
    }
  }

  const groupEdges: GroupEdge[] = [];
  for (const [key, val] of edgeMap) {
    const [from, to] = key.split('→');
    groupEdges.push({ from, to, count: val.count, types: Array.from(val.types) });
  }

  return { groups, groupEdges };
}

function getNeighborhood(
  repoPath: string,
  edges: RelationEdge[],
): { nodes: string[]; edges: RelationEdge[] } {
  const neighborEdges = edges.filter(
    (e) => e.from === repoPath || e.to === repoPath,
  );
  const nodeSet = new Set<string>([repoPath]);
  for (const e of neighborEdges) {
    nodeSet.add(e.from);
    nodeSet.add(e.to);
  }
  return { nodes: Array.from(nodeSet), edges: neighborEdges };
}

// ─── Custom node components ─────────────────────────────────────────────────

type RepoNodeData = {
  label: string;
  techStack: string[];
  summary: string;
  highlighted: boolean;
  isFocusCenter?: boolean;
};

function RepoNodeComponent({ data }: NodeProps<Node<RepoNodeData>>) {
  return (
    <div
      className={`glass-card px-4 py-3 min-w-[180px] max-w-[240px] transition-all duration-200 ${
        data.isFocusCenter
          ? '!shadow-[0_0_24px_rgba(0,113,227,0.4)] !border-[var(--accent-primary)] ring-2 ring-[var(--accent-primary)]/30'
          : data.highlighted
            ? '!shadow-[0_0_20px_rgba(0,113,227,0.3)] !border-[var(--accent-primary)]'
            : ''
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-[var(--accent-primary)] !w-2 !h-2 !border-0" />
      <div className="font-semibold text-sm text-[var(--foreground)] truncate">
        {data.label}
      </div>
      {data.techStack && data.techStack.length > 0 && (
        <div className="flex flex-wrap gap-1 mt-1.5">
          {data.techStack.slice(0, 4).map((t) => (
            <span
              key={t}
              className="inline-block px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-[var(--accent-primary)]/10 text-[var(--accent-primary)] border border-[var(--accent-primary)]/20"
            >
              {t}
            </span>
          ))}
          {data.techStack.length > 4 && (
            <span className="text-[10px] text-[var(--muted)]">+{data.techStack.length - 4}</span>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Right} className="!bg-[var(--accent-primary)] !w-2 !h-2 !border-0" />
    </div>
  );
}

type GroupNodeData = {
  label: string;
  repoCount: number;
  highlighted: boolean;
};

function GroupNodeComponent({ data }: NodeProps<Node<GroupNodeData>>) {
  return (
    <div
      className={`glass-card px-5 py-4 min-w-[240px] max-w-[300px] transition-all duration-200 cursor-pointer hover:!border-[var(--accent-primary)] ${
        data.highlighted ? '!shadow-[0_0_20px_rgba(0,113,227,0.3)] !border-[var(--accent-primary)]' : ''
      }`}
    >
      <Handle type="target" position={Position.Left} className="!bg-[var(--accent-primary)] !w-2.5 !h-2.5 !border-0" />
      <div className="font-semibold text-sm text-[var(--foreground)]">
        {data.label}
      </div>
      <div className="text-xs text-[var(--muted)] mt-1">
        {data.repoCount} {data.repoCount === 1 ? 'repo' : 'repos'}
      </div>
      <Handle type="source" position={Position.Right} className="!bg-[var(--accent-primary)] !w-2.5 !h-2.5 !border-0" />
    </div>
  );
}

const nodeTypes = {
  repoNode: RepoNodeComponent,
  groupNode: GroupNodeComponent,
};

// ─── Main component ─────────────────────────────────────────────────────────

export type ViewMode = 'group' | 'focus' | 'full';

export interface RelationGraphProps {
  data: RelationsData;
  viewMode: ViewMode;
  focusRepo: string | null;
  edgeFilters: Record<string, boolean>;
  onFocusRepo?: (repoPath: string) => void;
}

export default function RelationGraph({
  data,
  viewMode,
  focusRepo,
  edgeFilters,
  onFocusRepo,
}: RelationGraphProps) {
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  // Filter edges by type
  const filteredDataEdges = useMemo(() => {
    return data.edges.filter((e) => edgeFilters[e.type] !== false);
  }, [data.edges, edgeFilters]);

  // Build nodes & edges based on view mode
  const { initialNodes, initialEdges } = useMemo(() => {
    if (viewMode === 'group') {
      // Group View
      const { groups, groupEdges } = groupByPrefix(data.repos, filteredDataEdges, 2);

      const rawNodes: Node<GroupNodeData>[] = Array.from(groups.entries()).map(
        ([groupId, info]) => ({
          id: groupId,
          type: 'groupNode',
          position: { x: 0, y: 0 },
          data: {
            label: groupId,
            repoCount: info.repoCount,
            highlighted: false,
          },
        }),
      );

      const rawEdges: Edge[] = groupEdges.map((ge, i) => {
        // Use the primary type for styling
        const primaryType = ge.types[0] || 'related';
        const style = getEdgeStyle(primaryType);
        return {
          id: `ge-${i}`,
          source: ge.from,
          target: ge.to,
          type: 'default',
          label: `${ge.count} dep${ge.count > 1 ? 's' : ''}`,
          labelStyle: { fontSize: 10, fill: 'var(--muted)' },
          labelBgStyle: { fill: 'var(--card-bg)', fillOpacity: 0.8 },
          labelBgPadding: [4, 2] as [number, number],
          labelBgBorderRadius: 4,
          style: {
            stroke: style.color,
            strokeWidth: 2,
            strokeDasharray: style.strokeDasharray,
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: style.color,
            width: 16,
            height: 16,
          },
        };
      });

      const laid = layoutElements(rawNodes, rawEdges, 'LR', 100, 200);
      return { initialNodes: laid.nodes, initialEdges: laid.edges };
    }

    if (viewMode === 'focus' && focusRepo) {
      // Focus View — show 1-hop neighbors
      const { nodes: neighborPaths, edges: neighborEdges } = getNeighborhood(
        focusRepo,
        filteredDataEdges,
      );

      const rawNodes: Node<RepoNodeData>[] = neighborPaths
        .filter((p) => data.repos[p])
        .map((p) => ({
          id: p,
          type: 'repoNode',
          position: { x: 0, y: 0 },
          data: {
            label: p.split('/').pop() || p,
            techStack: data.repos[p]?.tech_stack || [],
            summary: data.repos[p]?.summary || '',
            highlighted: false,
            isFocusCenter: p === focusRepo,
          },
        }));

      const rawEdges: Edge[] = neighborEdges.map((edge, i) => {
        const style = getEdgeStyle(edge.type);
        return {
          id: `fe-${i}`,
          source: edge.from,
          target: edge.to,
          type: 'default',
          animated: style.animated || false,
          label: edge.type.replace(/_/g, ' '),
          labelStyle: { fontSize: 10, fill: 'var(--muted)' },
          labelBgStyle: { fill: 'var(--card-bg)', fillOpacity: 0.8 },
          labelBgPadding: [4, 2] as [number, number],
          labelBgBorderRadius: 4,
          style: {
            stroke: style.color,
            strokeWidth: 2,
            strokeDasharray: style.strokeDasharray,
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            color: style.color,
            width: 16,
            height: 16,
          },
        };
      });

      const laid = layoutElements(rawNodes, rawEdges, 'TB', 80, 120);
      return { initialNodes: laid.nodes, initialEdges: laid.edges };
    }

    // Full View — show everything (original behavior), remove orphan nodes after edge filtering
    const connectedNodes = new Set<string>();
    for (const edge of filteredDataEdges) {
      connectedNodes.add(edge.from);
      connectedNodes.add(edge.to);
    }

    const repoEntries = Object.entries(data.repos).filter(
      ([path]) => connectedNodes.has(path),
    );

    const rawNodes: Node<RepoNodeData>[] = repoEntries.map(([path, repo]) => ({
      id: path,
      type: 'repoNode',
      position: { x: 0, y: 0 },
      data: {
        label: path.split('/').pop() || path,
        techStack: repo.tech_stack || [],
        summary: repo.summary || '',
        highlighted: false,
      },
    }));

    const rawEdges: Edge[] = filteredDataEdges.map((edge, i) => {
      const style = getEdgeStyle(edge.type);
      return {
        id: `e-${i}`,
        source: edge.from,
        target: edge.to,
        type: 'default',
        animated: style.animated || false,
        label: edge.type.replace(/_/g, ' '),
        labelStyle: { fontSize: 10, fill: 'var(--muted)' },
        labelBgStyle: { fill: 'var(--card-bg)', fillOpacity: 0.8 },
        labelBgPadding: [4, 2] as [number, number],
        labelBgBorderRadius: 4,
        style: {
          stroke: style.color,
          strokeWidth: 2,
          strokeDasharray: style.strokeDasharray,
        },
        markerEnd: {
          type: MarkerType.ArrowClosed,
          color: style.color,
          width: 16,
          height: 16,
        },
      };
    });

    const laid = layoutElements(rawNodes, rawEdges);
    return { initialNodes: laid.nodes, initialEdges: laid.edges };
  }, [data, viewMode, focusRepo, filteredDataEdges]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // Sync when initialNodes/initialEdges change
  useEffect(() => {
    setNodes(initialNodes);
  }, [initialNodes, setNodes]);

  useEffect(() => {
    setEdges(initialEdges);
  }, [initialEdges, setEdges]);

  // Highlight connected edges on node hover
  const onNodeMouseEnter = useCallback(
    (_: React.MouseEvent, node: Node) => {
      setHoveredNodeId(node.id);
      setNodes((nds) =>
        nds.map((n) => {
          const isConnected =
            n.id === node.id ||
            edges.some(
              (e) =>
                (e.source === node.id && e.target === n.id) ||
                (e.target === node.id && e.source === n.id),
            );
          return {
            ...n,
            data: { ...n.data, highlighted: isConnected },
          };
        }),
      );
    },
    [edges, setNodes],
  );

  const onNodeMouseLeave = useCallback(() => {
    setHoveredNodeId(null);
    setNodes((nds) =>
      nds.map((n) => ({
        ...n,
        data: { ...n.data, highlighted: false },
      })),
    );
  }, [setNodes]);

  const onNodeClick = useCallback(
    (_: React.MouseEvent, node: Node) => {
      if (viewMode === 'group' && node.type === 'groupNode' && onFocusRepo) {
        // Click on a group node → switch to focus view with first repo of the group
        const { groups } = groupByPrefix(data.repos, filteredDataEdges, 2);
        const groupInfo = groups.get(node.id);
        if (groupInfo && groupInfo.repos.length > 0) {
          onFocusRepo(groupInfo.repos[0]);
        }
        return;
      }
      if (viewMode !== 'group' && onFocusRepo) {
        // In focus/full view, clicking a repo node focuses it
        onFocusRepo(node.id);
        return;
      }
      setSelectedNode((prev) => (prev === node.id ? null : node.id));
    },
    [viewMode, onFocusRepo, data.repos, filteredDataEdges],
  );

  const selectedRepo = selectedNode ? data.repos[selectedNode] : null;

  // Compute dynamic height
  const nodeCount = nodes.length;
  const graphHeight = viewMode === 'group'
    ? Math.max(500, Math.min(800, nodeCount * 30))
    : viewMode === 'focus'
      ? Math.max(400, Math.min(700, nodeCount * 25))
      : Math.max(600, Math.min(1200, nodeCount * 4));

  return (
    <div className="relative w-full" style={{ height: graphHeight }}>
      {/* Full view performance warning */}
      {viewMode === 'full' && nodeCount > 100 && (
        <div className="absolute top-2 left-1/2 -translate-x-1/2 z-10 px-3 py-1.5 rounded-lg bg-[#ff9f0a]/10 border border-[#ff9f0a]/20 text-xs text-[#ff9f0a]">
          {nodeCount} nodes — performance may be slow
        </div>
      )}

      {/* Focus view info */}
      {viewMode === 'focus' && focusRepo && (
        <div className="absolute top-2 left-1/2 -translate-x-1/2 z-10 px-3 py-1.5 rounded-lg bg-[var(--accent-primary)]/10 border border-[var(--accent-primary)]/20 text-xs text-[var(--accent-primary)]">
          Focusing: <span className="font-semibold">{focusRepo}</span> — {nodeCount - 1} neighbor{nodeCount - 1 !== 1 ? 's' : ''}
        </div>
      )}

      {/* Empty state for focus view without selection */}
      {viewMode === 'focus' && !focusRepo && (
        <div className="flex items-center justify-center h-full">
          <p className="text-[var(--muted)] text-sm">Search for a repository or click a group node to focus</p>
        </div>
      )}

      {(viewMode !== 'focus' || focusRepo) && (
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onNodeMouseEnter={onNodeMouseEnter}
          onNodeMouseLeave={onNodeMouseLeave}
          onNodeClick={onNodeClick}
          nodeTypes={nodeTypes}
          fitView
          fitViewOptions={{ padding: 0.15, maxZoom: 1.5 }}
          minZoom={0.05}
          maxZoom={2}
          proOptions={{ hideAttribution: true }}
        >
          <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--border-color)" />
          <Controls
            className="!bg-[var(--card-bg)] !border-[var(--border-color)] !rounded-xl !shadow-custom [&>button]:!bg-transparent [&>button]:!border-[var(--border-color)] [&>button]:!text-[var(--foreground)] [&>button:hover]:!bg-[var(--accent-primary)]/10"
          />
        </ReactFlow>
      )}

      {/* Node detail popover (only in non-group views) */}
      {viewMode !== 'group' && selectedNode && selectedRepo && (
        <div className="absolute top-4 right-4 w-72 glass-card p-4 z-10">
          <div className="flex items-center justify-between mb-2">
            <h3 className="font-semibold text-sm text-[var(--foreground)] truncate">
              {selectedNode.split('/').pop()}
            </h3>
            <button
              onClick={() => setSelectedNode(null)}
              className="text-[var(--muted)] hover:text-[var(--foreground)] transition-colors text-xs"
            >
              &times;
            </button>
          </div>
          <p className="text-xs text-[var(--muted)] mb-2 line-clamp-3">
            {selectedRepo.summary}
          </p>
          <p className="text-[10px] text-[var(--muted)] mb-1">Full path</p>
          <p className="text-xs text-[var(--foreground)] mb-2 break-all">{selectedNode}</p>
          {selectedRepo.tech_stack.length > 0 && (
            <>
              <p className="text-[10px] text-[var(--muted)] mb-1">Tech stack</p>
              <div className="flex flex-wrap gap-1">
                {selectedRepo.tech_stack.map((t) => (
                  <span
                    key={t}
                    className="inline-block px-1.5 py-0.5 rounded-full text-[10px] font-medium bg-[var(--accent-primary)]/10 text-[var(--accent-primary)]"
                  >
                    {t}
                  </span>
                ))}
              </div>
            </>
          )}
        </div>
      )}

      {/* Legend */}
      <div className="absolute bottom-4 left-4 glass-card p-3 z-10">
        <p className="text-[10px] text-[var(--muted)] font-medium mb-1.5 uppercase tracking-wider">Edge Types</p>
        <div className="space-y-1">
          {Object.entries(EDGE_STYLES).map(([type, style]) => (
            <div key={type} className="flex items-center gap-2">
              <div
                className="w-5 h-0.5"
                style={{
                  backgroundColor: style.color,
                  backgroundImage: style.strokeDasharray ? 'none' : undefined,
                  borderTop: style.strokeDasharray ? `2px ${style.strokeDasharray.includes('2') ? 'dotted' : 'dashed'} ${style.color}` : undefined,
                  height: style.strokeDasharray ? 0 : undefined,
                }}
              />
              <span className="text-[10px] text-[var(--muted)]">{type.replace(/_/g, ' ')}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
