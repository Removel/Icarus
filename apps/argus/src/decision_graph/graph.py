"""Immutable graph declarations; compilation is explicit."""
from collections.abc import Callable
from dataclasses import dataclass
from .node import NodeBinding
from .edge import Edge


@dataclass(frozen=True)
class DecisionGraph:
    nodes: tuple[NodeBinding, ...]
    edges: tuple[Edge, ...]
    entry: str
    output: str


@dataclass(frozen=True)
class RepeatBlock:
    body_graph: DecisionGraph
    next_input: Callable
    until: Callable
    max_iterations: int
