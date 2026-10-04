import { useEffect, useMemo, useRef, useState, type CSSProperties } from 'react';
import {
  BaseEdge,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useNodesInitialized,
  useNodesState,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import { Button } from '@icarus/ui';
import { kindLabel, type KnowledgeBase } from './types';
import { resolveDeclaredSource, type GraphData, type Target } from './evidence';
import { layoutGraph } from './graphLayout';

type DocumentNode = Node<
  { label: string; category: string; color: string; target: Target; unavailable?: boolean },
  'document'
>;
type DocumentEdge = Edge<
  { origin: Target; target: Target; kind: 'source' | 'outlink'; bend?: number },
  'reference'
>;
const nodeId = (target: Target) => JSON.stringify([target.kind, target.id]);
const DEFAULT_ZOOM = 0.87;
const colors = {
  source: '#249778',
  summaries: '#8965c7',
  concepts: '#447ad5',
  entities: '#c88732',
};
const edgeColors = { source: '#759b8c', outlink: '#8da0bd' };
const activeEdgeColors = { source: '#24866b', outlink: '#396dbe' };

function DocumentPoint({ data, selected }: NodeProps<DocumentNode>) {
  return (
    <div
      className={`document-node${selected ? ' is-selected' : ''}${data.unavailable ? ' is-unavailable' : ''}`}
      style={{ '--node-color': data.color } as CSSProperties}
    >
      <Handle type="target" position={Position.Top} isConnectable={false} />
      <span className="document-node-dot" />
      <strong>
        {data.label}
        {data.unavailable && <small>{data.category}</small>}
      </strong>
      <Handle type="source" position={Position.Bottom} isConnectable={false} />
    </div>
  );
}
function ReferenceLine({
  sourceX,
  sourceY,
  targetX,
  targetY,
  data,
  ...props
}: EdgeProps<DocumentEdge>) {
  const dx = targetX - sourceX,
    dy = targetY - sourceY,
    distance = Math.hypot(dx, dy) || 1;
  const ux = dx / distance,
    uy = dy / distance,
    trim = Math.min(10, distance / 3);
  const bend = data?.bend ?? 0;
  const path = `M ${sourceX + ux * trim},${sourceY + uy * trim} Q ${(sourceX + targetX) / 2 - uy * bend},${(sourceY + targetY) / 2 + ux * bend} ${targetX - ux * trim},${targetY - uy * trim}`;
  return (
    <BaseEdge
      id={props.id}
      path={path}
      markerEnd={props.markerEnd}
      style={props.style}
      interactionWidth={20}
    />
  );
}
const nodeTypes = { document: DocumentPoint };
const edgeTypes = { reference: ReferenceLine };

function FlowCanvas({
  base,
  graph,
  focus,
  onFocus,
  onRelation,
}: {
  base: KnowledgeBase;
  graph: GraphData;
  focus?: Target;
  onFocus: (target: Target) => void;
  onRelation: (origin: Target, kind: 'source' | 'outlink', target: Target) => void;
}) {
  const [hovered, setHovered] = useState<string | null>(null);
  const [keyboardFocus, setKeyboardFocus] = useState<string | null>(null);
  const [zoom, setZoom] = useState(DEFAULT_ZOOM);
  const model = useMemo(() => {
    const nodes = new Map<string, DocumentNode>();
    const edges = new Map<string, DocumentEdge>();
    function add(
      target: Target,
      label: string,
      category: string,
      color: string,
      unavailable = false,
    ) {
      const id = nodeId(target);
      if (nodes.has(id)) return;
      nodes.set(id, {
        id,
        type: 'document',
        position: { x: 0, y: 0 },
        data: { target, label, category, color, unavailable },
        ariaLabel: (unavailable ? '内容待核对：' : '选择节点：') + label,
        focusable: true,
      });
    }
    base.documents.forEach((item) =>
      add({ kind: 'source', id: item.hash }, item.name, '原始资料', colors.source),
    );
    base.pages.forEach((item) =>
      add({ kind: 'page', id: item.path }, item.title, kindLabel[item.kind], colors[item.kind]),
    );
    function connect(origin: Target, target: Target, kind: 'source' | 'outlink') {
      const source = nodeId(origin),
        destination = nodeId(target);
      if (source === destination) return;
      const id = JSON.stringify([source, destination, kind]);
      edges.set(id, {
        id,
        source,
        target: destination,
        type: 'reference',
        data: { origin, target, kind },
        style: {
          stroke: edgeColors[kind],
          strokeWidth: 1.35,
          ...(kind === 'source' ? { strokeDasharray: '4 5' } : {}),
        },
        ariaLabel: kind === 'source' ? '声明来源' : '正文引用',
      });
    }
    graph.nodes.forEach((item) =>
      add({ kind: 'page', id: item.id }, item.label, item.type, colors.concepts, true),
    );
    graph.nodes.forEach((item) => {
      const origin: Target = { kind: 'page', id: item.id };
      item.sources.forEach((value) => {
        const match = resolveDeclaredSource(value, graph, base.documents);
        add(
          match.target,
          value,
          match.status === 'ambiguous' ? '来源待确认' : '内容不可用',
          colors.source,
          true,
        );
        connect(origin, match.target, 'source');
      });
    });
    graph.edges.forEach((edge) => {
      const origin: Target = { kind: 'page', id: edge.source },
        target: Target = { kind: 'page', id: edge.target };
      for (const item of [origin, target]) add(item, item.id, '页面不可用', colors.concepts, true);
      connect(origin, target, 'outlink');
    });
    // Keep reciprocal links and source/reference pairs separately clickable.
    const pairs = new Map<string, DocumentEdge[]>();
    edges.forEach((edge) => {
      const key = JSON.stringify([edge.source, edge.target].sort());
      pairs.set(key, [...(pairs.get(key) ?? []), edge]);
    });
    pairs.forEach((group) =>
      group.forEach((edge, index) => {
        edge.data!.bend =
          (index - (group.length - 1) / 2) * 32 * (edge.source < edge.target ? 1 : -1);
      }),
    );
    return { nodes: [...nodes.values()], edges: [...edges.values()] };
  }, [base.documents, base.pages, graph]);
  const topology = JSON.stringify([
    model.nodes.map((node) => node.id).sort(),
    model.edges.map((edge) => [edge.source, edge.target]).sort(),
  ]);
  const positions = useMemo(() => {
    const [ids, links] = JSON.parse(topology) as [string[], [string, string][]];
    return layoutGraph(ids, links);
  }, [topology]);
  const [nodes, setNodes, onNodesChange] = useNodesState<DocumentNode>([]);
  const flow = useReactFlow<DocumentNode, DocumentEdge>();
  const initialized = useNodesInitialized();
  const fittedFocus = useRef('');
  useEffect(() => {
    setNodes((previous) => {
      const previousById = new Map(previous.map((node) => [node.id, node]));
      return model.nodes.map((node) => ({
        ...node,
        position: previousById.get(node.id)?.position ?? positions.get(node.id)!,
        selected: Boolean(focus && node.id === nodeId(focus)),
      }));
    });
  }, [model, positions, focus?.kind, focus?.id, setNodes]);
  useEffect(() => {
    if (!focus) {
      fittedFocus.current = '';
      return;
    }
    if (!initialized) return;
    const id = nodeId(focus);
    if (fittedFocus.current === id) return;
    fittedFocus.current = id;
    const neighbors = new Set([
      id,
      ...model.edges
        .filter((edge) => edge.source === id || edge.target === id)
        .flatMap((edge) => [edge.source, edge.target]),
    ]);
    void flow.fitView({
      nodes: flow.getNodes().filter((node) => neighbors.has(node.id)),
      minZoom: DEFAULT_ZOOM,
      maxZoom: DEFAULT_ZOOM,
    });
  }, [focus?.kind, focus?.id, initialized, flow, model.edges]);
  const activeId = hovered ?? keyboardFocus ?? (focus ? nodeId(focus) : null);
  const neighbors = new Set(
    activeId
      ? [
          activeId,
          ...model.edges
            .filter((edge) => edge.source === activeId || edge.target === activeId)
            .flatMap((edge) => [edge.source, edge.target]),
        ]
      : [],
  );
  const edges = model.edges.map((edge) => {
    const highlighted = activeId && (edge.source === activeId || edge.target === activeId);
    const color = activeEdgeColors[edge.data!.kind];
    return {
      ...edge,
      className: highlighted ? 'is-highlighted' : activeId ? 'is-muted' : undefined,
      style: { ...edge.style, ...(highlighted ? { stroke: color } : {}) },
      markerEnd: highlighted
        ? { type: MarkerType.ArrowClosed, color, width: 16, height: 16 }
        : undefined,
    };
  });
  return (
    <div
      className="document-canvas"
      aria-label="文档关联画布"
      style={{ '--graph-zoom': zoom } as CSSProperties}
      data-labels={zoom < (nodes.length <= 30 ? 0.3 : 0.55) ? 'focused' : 'all'}
      onFocusCapture={(event) => {
        const element = (event.target as Element).closest('.react-flow__node');
        if (element) {
          setHovered(null);
          setKeyboardFocus(element.getAttribute('data-id'));
        }
      }}
      onBlurCapture={(event) => {
        if ((event.target as Element).closest('.react-flow__node')) setKeyboardFocus(null);
      }}
      onKeyDownCapture={(event) => {
        if (
          event.key !== 'Enter' ||
          event.nativeEvent.isComposing ||
          !(event.target instanceof Element)
        )
          return;
        const element = event.target.closest('.react-flow__node, .react-flow__edge');
        if (!element) return;
        event.preventDefault();
        event.stopPropagation();
        const id = element.getAttribute('data-id');
        const node = nodes.find((item) => item.id === id),
          edge = edges.find((item) => item.id === id);
        if (node && !node.data.unavailable) onFocus(node.data.target);
        else if (edge?.data) onRelation(edge.data.origin, edge.data.kind, edge.data.target);
      }}
    >
      <ReactFlow<DocumentNode, DocumentEdge>
        nodes={nodes.map((node) => ({
          ...node,
          className: activeId
            ? node.id === activeId
              ? 'is-active is-neighbor'
              : neighbors.has(node.id)
                ? 'is-neighbor'
                : 'is-muted'
            : undefined,
          zIndex: node.id === activeId ? 2 : 0,
        }))}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onNodeMouseEnter={(_, node) => setHovered(node.id)}
        onNodeMouseLeave={() => setHovered(null)}
        onNodeClick={(_, node) => {
          if (!node.data.unavailable) onFocus(node.data.target);
        }}
        onEdgeClick={(_, edge) => {
          if (edge.data) onRelation(edge.data.origin, edge.data.kind, edge.data.target);
        }}
        onMove={(_, viewport) => setZoom(viewport.zoom)}
        nodesConnectable={false}
        edgesReconnectable={false}
        deleteKeyCode={null}
        zoomOnDoubleClick={false}
        defaultViewport={{ x: 0, y: 0, zoom: DEFAULT_ZOOM }}
        fitView
        fitViewOptions={{ minZoom: DEFAULT_ZOOM, maxZoom: DEFAULT_ZOOM }}
        minZoom={0.15}
        maxZoom={3}
        ariaLabelConfig={{
          'controls.zoomIn.ariaLabel': '放大画布',
          'controls.zoomOut.ariaLabel': '缩小画布',
          'controls.fitView.ariaLabel': '适应画布',
          'node.a11yDescription.default': '按 Enter 阅读文档，使用方向键移动节点。',
          'edge.a11yDescription.default': '按 Enter 查看引用方向与关系依据。',
        }}
      >
        <Controls showInteractive={false} />
      </ReactFlow>
      <div className="graph-caption">
        <strong>双链图谱</strong>
        <span>
          {nodes.length} 个节点 · {edges.length} 条关系
        </span>
      </div>
      <div className="canvas-legend">
        {Object.entries(colors).map(([kind, color]) => (
          <span key={kind}>
            <i style={{ background: color }} />
            {kind === 'source' ? '资料' : kindLabel[kind as keyof typeof kindLabel]}
          </span>
        ))}
        <span>
          <b />
          引用
        </span>
        <span>
          <b className="is-source" />
          来源
        </span>
      </div>
      <span className="graph-zoom">{Math.round(zoom * 100)}%</span>
      <Button
        className="canvas-reset"
        theme="light"
        type="tertiary"
        onClick={() => {
          setNodes(
            model.nodes.map((node) => ({
              ...node,
              position: positions.get(node.id)!,
              selected: Boolean(focus && node.id === nodeId(focus)),
            })),
          );
          requestAnimationFrame(
            () => void flow.fitView({ minZoom: DEFAULT_ZOOM, maxZoom: DEFAULT_ZOOM }),
          );
        }}
      >
        重置布局
      </Button>
    </div>
  );
}
export default function EvidenceCanvas(props: Parameters<typeof FlowCanvas>[0]) {
  return (
    <ReactFlowProvider>
      <FlowCanvas {...props} />
    </ReactFlowProvider>
  );
}
