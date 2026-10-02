"""Validate declarations and build a reusable, business-independent plan."""
from collections.abc import Mapping
from dataclasses import dataclass, replace
from math import isfinite
from types import MappingProxyType
from .edge import Edge
from .graph import DecisionGraph, RepeatBlock
from .node import NodeBinding


@dataclass(frozen=True)
class CompiledGraph:
    nodes: Mapping[str, NodeBinding]
    edges: Mapping[str, Edge]
    ordered_nodes: tuple[NodeBinding, ...]
    incoming: Mapping[str, tuple[Edge, ...]]
    outgoing: Mapping[str, tuple[Edge, ...]]
    repeat_bodies: Mapping[str, 'CompiledGraph']
    entry: str
    output: str


class GraphCompiler:
    def compile(self, graph: DecisionGraph) -> CompiledGraph:
        return self._compile(graph, set())

    def _compile(self, graph, ancestors):
        if id(graph) in ancestors:
            raise ValueError('Recursive graph declaration')
        ancestors = ancestors | {id(graph)}
        nodes = {n.id: n for n in graph.nodes}
        if (len(nodes) != len(graph.nodes) or any(not isinstance(key, str) or not key for key in nodes)
                or graph.entry not in nodes or graph.output not in nodes):
            raise ValueError('Invalid graph identifiers')
        incoming, outgoing = {key: [] for key in nodes}, {key: [] for key in nodes}
        edges, pairs = {}, set()
        for declared in graph.edges:
            if declared.source not in nodes or declared.target not in nodes:
                raise ValueError('Dangling graph edge')
            pair = (declared.source, declared.target)
            key = declared.id if declared.id is not None else f'{declared.source}->{declared.target}'
            if not isinstance(key, str) or not key or key in nodes or key in edges:
                raise ValueError('Invalid edge identifier')
            if pair in pairs:
                raise ValueError('Duplicate graph edge')
            if (not callable(declared.condition) or not isfinite(declared.timeout_seconds)
                    or declared.timeout_seconds <= 0):
                raise ValueError('Invalid edge options')
            if declared.collect is not None and (not callable(declared.collect.action)
                                                  or not callable(declared.collect.bind_input)):
                raise ValueError('Invalid edge action')
            edge = replace(declared, id=key)
            pairs.add(pair)
            edges[key] = edge
            incoming[edge.target].append(edge)
            outgoing[edge.source].append(edge)
        parents, bodies = {}, {}
        for node in graph.nodes:
            if (node.join not in {'all', 'any', 'selected'} or not isfinite(node.timeout_seconds)
                    or node.timeout_seconds <= 0 or not callable(node.bind_input)):
                raise ValueError('Invalid node options')
            if not set(node.requires) <= nodes.keys() | edges.keys():
                raise ValueError('Dangling dependency')
            parents[node.id] = {edge.source for edge in incoming[node.id]}
            parents[node.id].update(key if key in nodes else edges[key].source for key in node.requires)
            if isinstance(node.evaluate, RepeatBlock):
                block = node.evaluate
                if (type(block.max_iterations) is not int or block.max_iterations < 1
                        or not callable(block.next_input) or not callable(block.until)):
                    raise ValueError('Repeat must be bounded')
                bodies[node.id] = self._compile(block.body_graph, ancestors)
            elif not callable(node.evaluate):
                raise ValueError('Invalid node action')
        done, ordered = set(), []
        while len(done) < len(nodes):
            ready = [n for n in graph.nodes if n.id not in done and parents[n.id] <= done]
            if not ready:
                raise ValueError('Ordinary graph must be acyclic')
            for node in ready:
                ordered.append(node)
                done.add(node.id)
        reached = {graph.entry}
        while True:
            added = {edge.target for edge in edges.values() if edge.source in reached} - reached
            if not added:
                break
            reached.update(added)
        if reached != nodes.keys():
            raise ValueError('Unreachable graph nodes')
        return CompiledGraph(MappingProxyType(nodes), MappingProxyType(edges), tuple(ordered),
            MappingProxyType({key: tuple(value) for key, value in incoming.items()}),
            MappingProxyType({key: tuple(value) for key, value in outgoing.items()}),
            MappingProxyType(bodies), graph.entry, graph.output)
