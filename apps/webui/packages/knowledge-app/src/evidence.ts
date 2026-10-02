import type { KnowledgeBase, Source } from './types';
export type GraphNode = {
  id: string;
  label: string;
  type: string;
  description: string;
  sources: string[];
  in: number;
  out: number;
};
export type GraphEdge = { source: string; target: string };
export type GraphData = { nodes: GraphNode[]; edges: GraphEdge[]; types: string[] };
export type Target = { kind: 'page' | 'source'; id: string };
export type RelationKind = 'source' | 'outlink' | 'backlink' | 'shared';
export type Relation = {
  kind: RelationKind;
  target: Target;
  evidence: string[];
  status?: 'missing' | 'ambiguous' | 'unreadable' | 'unloaded';
  unresolved?: boolean;
};
export function resolveSource(name: string, documents: Source[]) {
  const normalized = name.replace(/\\/g, '/');
  const rawName = normalized.startsWith('raw/') ? normalized.slice(4) : normalized;
  const sourceName = normalized.startsWith('sources/')
    ? normalized.slice(8).replace(/\.(md|json)$/i, '')
    : undefined;
  const matches = documents.filter((item) => {
    if (sourceName !== undefined) {
      if (item.sourcePath)
        return item.sourcePath.replace(/\\/g, '/').replace(/^wiki\//, '') === normalized;
      return (item.docName ?? item.name.replace(/\.[^.]+$/, '')) === sourceName;
    }
    return item.name === rawName;
  });
  return {
    source: matches.length === 1 ? matches[0] : undefined,
    status:
      matches.length === 0
        ? ('missing' as const)
        : matches.length > 1
          ? ('ambiguous' as const)
          : undefined,
  };
}
export function resolveDeclaredSource(name: string, graph: GraphData, documents: Source[]) {
  const page = graph.nodes.find(
    (item) => item.id === name.replace(/^wiki\//, '').replace(/\.md$/, ''),
  );
  if (page) return { target: { kind: 'page' as const, id: page.id }, status: undefined };
  const document = resolveSource(name, documents);
  return {
    target: { kind: 'source' as const, id: document.source?.hash ?? name },
    status: document.status,
  };
}

// Follow declared provenance only. Ordinary wikilinks are not evidence of derivation.
export function derivedPages(source: Source, base: KnowledgeBase, graph: GraphData) {
  const reached = new Set<string>();
  const direct = new Set<string>();
  let changed = true;
  while (changed) {
    changed = false;
    for (const page of base.pages) {
      for (const name of page.sources) {
        const match = resolveDeclaredSource(name, graph, base.documents);
        if (match.status) continue;
        const isDirect = match.target.kind === 'source' && match.target.id === source.hash;
        if (isDirect) direct.add(page.path);
        if (
          (isDirect || (match.target.kind === 'page' && reached.has(match.target.id))) &&
          !reached.has(page.path)
        ) {
          reached.add(page.path);
          changed = true;
        }
      }
    }
  }
  return base.pages
    .filter((page) => reached.has(page.path))
    .map((page) => ({ page, direct: direct.has(page.path) }));
}
export function relationsFor(target: Target, graph: GraphData, documents: Source[]): Relation[] {
  if (target.kind === 'source') {
    const document = documents.find((item) => item.hash === target.id);
    if (!document) return [];
    return graph.nodes
      .filter((item) =>
        item.sources.some(
          (name) =>
            resolveDeclaredSource(name, graph, documents).target.kind === 'source' &&
            resolveSource(name, documents).source?.hash === document.hash,
        ),
      )
      .map((item) => ({
        kind: 'backlink',
        target: { kind: 'page', id: item.id },
        evidence: [document.name],
      }));
  }
  const node = graph.nodes.find((item) => item.id === target.id);
  if (!node) return [];
  const sources: Relation[] = [...new Set(node.sources)].map((name) => {
    const match = resolveDeclaredSource(name, graph, documents);
    const source =
      match.target.kind === 'source'
        ? documents.find((item) => item.hash === match.target.id)
        : undefined;
    return {
      kind: 'source',
      target: match.target,
      evidence: [name],
      status:
        match.status ??
        (source?.readState === 'unloaded' || source?.readState === 'loading'
          ? 'unloaded'
          : source?.readState === 'failed' ||
              (source?.readState === 'ready' && !source.content.trim())
            ? 'unreadable'
            : undefined),
    };
  });
  const outlinks: Relation[] = graph.edges
    .filter((edge) => edge.source === node.id)
    .map((edge) => ({
      kind: 'outlink',
      target: { kind: 'page', id: edge.target },
      evidence: [node.id],
    }));
  const backlinks: Relation[] = graph.edges
    .filter((edge) => edge.target === node.id)
    .map((edge) => ({
      kind: 'backlink',
      target: { kind: 'page', id: edge.source },
      evidence: [edge.source],
    }));
  const shared: Relation[] = graph.nodes
    .filter((item) => item.id !== node.id)
    .flatMap((item) => {
      const common = [...new Set(item.sources.filter((name) => node.sources.includes(name)))];
      if (!common.length) return [];
      const unresolved = common.some((name) =>
        Boolean(resolveDeclaredSource(name, graph, documents).status),
      );
      return [
        {
          kind: 'shared' as const,
          target: { kind: 'page' as const, id: item.id },
          evidence: common,
          unresolved,
        },
      ];
    });
  return [
    ...new Map(
      [...sources, ...outlinks, ...backlinks, ...shared].map((relation) => [
        JSON.stringify([relation.kind, relation.target.kind, relation.target.id]),
        relation,
      ]),
    ).values(),
  ];
}
