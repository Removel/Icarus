import asyncio
import pytest
from apps.argus.src.decision_graph.compiler import GraphCompiler
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.edge import Edge
from apps.argus.src.decision_graph.runner import GraphRunner


async def identity(value):
    return value


@pytest.mark.parametrize('other,join,status', [
    ('skipped', 'all', 'skipped'), ('skipped', 'any', 'succeeded'),
    ('skipped', 'selected', 'succeeded'), ('failed', 'all', 'blocked'),
    ('failed', 'any', 'blocked'), ('failed', 'selected', 'succeeded'),
    ('succeeded', 'all', 'succeeded'), ('succeeded', 'any', 'failed'),
    ('succeeded', 'selected', 'succeeded'),
])
def test_join_parent_statuses(other, join, status):
    async def right(value):
        if other == 'failed':
            raise ValueError('right failed')
        return value
    graph = DecisionGraph((
        NodeBinding('root', identity, lambda *_: {'value': True}),
        NodeBinding('left', identity, lambda *_: {'value': 1}),
        NodeBinding('right', right, lambda *_: {'value': 2}),
        NodeBinding('end', identity, lambda *_: {'value': 3}, join=join),
    ), (Edge('root', 'left'), Edge('root', 'right', lambda _: other != 'skipped'),
        Edge('left', 'end'), Edge('right', 'end')), 'root', 'end')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None))
    assert run.executions['end'].status == status


def test_required_unselected_edge_blocks_even_with_successful_source():
    graph = DecisionGraph((
        NodeBinding('root', identity, lambda *_: {'value': True}),
        NodeBinding('other', identity, lambda *_: {'value': 1}),
        NodeBinding('end', identity, lambda *_: {'value': 2}, requires=('root->other',)),
    ), (Edge('root', 'other', lambda _: False), Edge('root', 'end')), 'root', 'end')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None))
    assert run.executions['end'].status == 'blocked'


def test_node_cannot_require_own_outgoing_edge():
    graph = DecisionGraph((
        NodeBinding('a', identity, lambda *_: {'value': None}, requires=('a->b',)),
        NodeBinding('b', identity, lambda *_: {'value': None}),
    ), (Edge('a', 'b'),), 'a', 'b')
    with pytest.raises(ValueError):
        GraphCompiler().compile(graph)
