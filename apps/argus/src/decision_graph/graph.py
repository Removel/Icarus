"""Explicit decision graph definitions; no platform dependencies."""
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class NodeBinding:
    id: str
    evaluate: Any
    bind_input: Callable
    requires: tuple[str, ...] = ()
    join: Literal['all', 'any', 'selected'] = 'all'
    timeout_seconds: float = 10.0


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    condition: Callable = lambda _: True


@dataclass(frozen=True)
class DecisionGraph:
    nodes: tuple[NodeBinding, ...]
    edges: tuple[Edge, ...]
    entry: str
    output: str

    def __post_init__(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes) or self.entry not in ids or self.output not in ids:
            raise ValueError('Invalid graph identifiers')
        predecessors = {key: set() for key in ids}
        for edge in self.edges:
            if edge.source not in ids or edge.target not in ids:
                raise ValueError('Dangling graph edge')
            predecessors[edge.target].add(edge.source)
        for node in self.nodes:
            if node.join not in {'all','any','selected'} or node.timeout_seconds <= 0:
                raise ValueError('Invalid node options')
            if not set(node.requires) <= ids:
                raise ValueError('Dangling dependency')
            predecessors[node.id].update(node.requires)
        done=set()
        while len(done)<len(ids):
            ready={key for key in ids-done if predecessors[key] <= done}
            if not ready: raise ValueError('Ordinary graph must be acyclic')
            done.update(ready)
        reached={self.entry}
        while True:
            added={e.target for e in self.edges if e.source in reached}-reached
            if not added: break
            reached.update(added)
        if reached != ids:
            raise ValueError('Unreachable graph nodes')

    def order(self):
        done=set()
        while len(done)<len(self.nodes):
            for node in self.nodes:
                parents={e.source for e in self.edges if e.target==node.id}|set(node.requires)
                if node.id not in done and parents<=done:
                    done.add(node.id)
                    yield node


@dataclass(frozen=True)
class RepeatBlock:
    body_graph: DecisionGraph
    next_input: Callable
    until: Callable
    max_iterations: int

    def __post_init__(self):
        if self.max_iterations < 1: raise ValueError('Repeat must be bounded')
