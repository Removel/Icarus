import {
  forceCollide,
  forceLink,
  forceManyBody,
  forceSimulation,
  forceX,
  forceY,
  type SimulationNodeDatum,
} from 'd3-force';

type Point = SimulationNodeDatum & { id: string };

// Layout depends only on topology. Reading content or changing metadata must not
// reshuffle the graph. Stop the simulation so dragging keeps a node in place.
export function layoutGraph(ids: string[], connections: [string, string][]) {
  const nodes: Point[] = [...ids].sort().map((id) => ({ id }));
  const pairs = new Map<string, { source: string; target: string }>();
  for (const [from, to] of connections) {
    if (from === to) continue;
    const [source, target] = [from, to].sort();
    pairs.set(JSON.stringify([source, target]), { source, target });
  }
  const links = [...pairs.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([, link]) => link);
  const simulation = forceSimulation(nodes)
    .stop()
    .force(
      'link',
      forceLink<Point, (typeof links)[number]>(links)
        .id((node) => node.id)
        .distance(185)
        .strength(0.45),
    )
    .force('charge', forceManyBody<Point>().strength(-650))
    .force('collision', forceCollide<Point>(78).iterations(2))
    .force('x', forceX<Point>(0).strength(0.035))
    .force('y', forceY<Point>(0).strength(0.035));
  simulation.tick(240);
  return new Map(nodes.map((node) => [node.id, { x: node.x! - 24, y: node.y! - 24 }]));
}
