"""State belongs to one run; outputs never encode business fallbacks."""
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal


@dataclass(frozen=True)
class UnitExecution:
    id: str
    kind: Literal['node', 'edge']
    status: Literal['succeeded', 'failed', 'skipped', 'blocked']
    output: object = None
    error_type: str | None = None
    duration: float = 0.0
    iteration: int = 0


@dataclass
class ExecutionBudget:
    remaining: int


@dataclass
class ExecutionState:
    states: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, object] = field(default_factory=dict)
    selected: set[tuple[str, str]] = field(default_factory=set)


@dataclass(frozen=True)
class GraphRun:
    status: Literal['completed', 'failed']
    output: object
    executions: Mapping[str, UnitExecution]
    selected_edges: tuple[tuple[str, str], ...]
    run_id: str
    error_type: str | None = None
