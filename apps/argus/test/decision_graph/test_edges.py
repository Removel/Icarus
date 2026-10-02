import asyncio
from apps.argus.src.decision_graph.compiler import GraphCompiler
from apps.argus.src.decision_graph.graph import DecisionGraph
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.edge import Edge, EdgeAction
from apps.argus.src.decision_graph.runner import GraphRunner


async def identity(value):
    return value


def test_collect_output_is_available_to_target():
    graph = DecisionGraph((
        NodeBinding('a', identity, lambda *_: {'value': 2}),
        NodeBinding('b', identity, lambda _, r: {'value': r['facts']}, requires=('facts',)),
    ), (Edge('a', 'b', collect=EdgeAction(identity, lambda _, r: {'value': r['a'] + 1}), id='facts'),), 'a', 'b')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None, max_steps=3))
    assert run.output == 3
    assert run.executions['facts'].kind == 'edge'
    assert run.executions['facts'].status == 'succeeded'


def test_condition_failure_keeps_source_and_sibling(caplog):
    def broken(value):
        raise ValueError('sensitive condition text')
    graph = DecisionGraph((
        NodeBinding('a', identity, lambda *_: {'value': 1}),
        NodeBinding('bad', identity, lambda *_: {'value': 0}),
        NodeBinding('ok', identity, lambda *_: {'value': 9}),
    ), (Edge('a', 'bad', broken), Edge('a', 'ok')), 'a', 'ok')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None))
    assert run.output == 9
    assert run.executions['a'].status == 'succeeded'
    assert run.executions['a->bad'].status == 'failed'
    assert run.executions['bad'].status == 'blocked'
    assert run.executions['a->ok'].status == 'succeeded'
    assert 'sensitive condition text' not in caplog.text


def test_passive_edges_do_not_consume_steps():
    graph = DecisionGraph((
        NodeBinding('a', identity, lambda *_: {'value': 1}),
        NodeBinding('b', identity, lambda *_: {'value': 2}),
    ), (Edge('a', 'b'),), 'a', 'b')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None, max_steps=2))
    assert run.status == 'completed' and run.output == 2
    assert run.executions['a->b'].output is None


def test_sibling_collects_see_only_prior_successful_outputs():
    graph = DecisionGraph((
        NodeBinding('root', identity, lambda *_: {'value': 4}),
        NodeBinding('left', identity, lambda *_: {'value': 0}),
        NodeBinding('right', identity, lambda _, r: {'value': r['second']}),
    ), (
        Edge('root', 'left', collect=EdgeAction(identity, lambda _, r: {'value': r['root'] + 1}), id='first'),
        Edge('root', 'right', collect=EdgeAction(identity, lambda _, r: {'value': r['first'] + 1}), id='second'),
    ), 'root', 'right')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None))
    assert run.output == 6
    assert run.executions['first'].output == 5


def test_unselected_and_blocked_edges_are_recorded():
    async def broken(value):
        raise ValueError('failed')
    graph = DecisionGraph((
        NodeBinding('root', identity, lambda *_: {'value': True}),
        NodeBinding('bad', broken, lambda *_: {'value': None}),
        NodeBinding('skip', identity, lambda *_: {'value': None}),
        NodeBinding('end', identity, lambda *_: {'value': None}),
    ), (Edge('root', 'bad'), Edge('root', 'skip', lambda _: False),
        Edge('bad', 'end'), Edge('skip', 'end')), 'root', 'end')
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(graph), None))
    assert run.executions['root->skip'].status == 'skipped'
    assert run.executions['skip->end'].status == 'skipped'
    assert run.executions['bad->end'].status == 'blocked'
