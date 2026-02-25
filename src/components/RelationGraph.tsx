'use client';

import React, { useCallback, useMemo, useState } from 'react';
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

const EDGE_STYLES: Record<string, { color: string; strokeDasharray?: string; animated?: boolean }> = {
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

const NODE_WIDTH = 220;
const NODE_HEIGHT = 80;

function layoutElements(
  nodes: Node[],
  edges: Edge[],
  direction: 'LR' | 'TB' = 'LR',
) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: direction, nodesep: 60, ranksep: 120 });

  nodes.forEach((node) => {
    g.setNode(node.id, { width: NODE_WIDTH, height: NODE_HEIGHT });
  });

  edges.forEach((edge) => {
    g.setEdge(edge.source, edge.target);
  });

  dagre.layout(g);

  const laidOut = nodes.map((node) => {
    const pos = g.node(node.id);
    return {
      ...node,
      position: {
        x: pos.x - NODE_WIDTH / 2,
        y: pos.y - NODE_HEIGHT / 2,
      },
    };
  });

  return { nodes: laidOut, edges };
}

// ─── Custom node component ──────────────────────────────────────────────────

type RepoNodeData = {
  label: string;
  techStack: string[];
  summary: string;
  highlighted: boolean;
};

function RepoNodeComponent({ data }: NodeProps<Node<RepoNodeData>>) {
  return (
    <div
      className={`glass-card px-4 py-3 min-w-[180px] max-w-[240px] transition-all duration-200 ${
        data.highlighted ? '!shadow-[0_0_20px_rgba(0,113,227,0.3)] !border-[var(--accent-primary)]' : ''
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

const nodeTypes = { repoNode: RepoNodeComponent };

// ─── Main component ─────────────────────────────────────────────────────────

interface RelationGraphProps {
  data: RelationsData;
}

export default function RelationGraph({ data }: RelationGraphProps) {
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  // Build nodes & edges from API data
  const { initialNodes, initialEdges } = useMemo(() => {
    const repoEntries = Object.entries(data.repos);

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

    const rawEdges: Edge[] = data.edges.map((edge, i) => {
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
  }, [data]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, , onEdgesChange] = useEdgesState(initialEdges);

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

  const onNodeClick = useCallback((_: React.MouseEvent, node: Node) => {
    setSelectedNode((prev) => (prev === node.id ? null : node.id));
  }, []);

  const selectedRepo = selectedNode ? data.repos[selectedNode] : null;

  return (
    <div className="relative w-full" style={{ minHeight: 500 }}>
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
        fitViewOptions={{ padding: 0.2 }}
        minZoom={0.3}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
      >
        <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="var(--border-color)" />
        <Controls
          className="!bg-[var(--card-bg)] !border-[var(--border-color)] !rounded-xl !shadow-custom [&>button]:!bg-transparent [&>button]:!border-[var(--border-color)] [&>button]:!text-[var(--foreground)] [&>button:hover]:!bg-[var(--accent-primary)]/10"
        />
      </ReactFlow>

      {/* Node detail popover */}
      {selectedNode && selectedRepo && (
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
