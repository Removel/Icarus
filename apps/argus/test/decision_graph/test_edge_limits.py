import asyncio
from apps.argus.src.decision_graph.compiler import GraphCompiler
from apps.argus.src.decision_graph.graph import DecisionGraph, RepeatBlock
from apps.argus.src.decision_graph.node import NodeBinding
from apps.argus.src.decision_graph.edge import Edge, EdgeAction
from apps.argus.src.decision_graph.runner import GraphRunner


async def identity(value):
    return value


def collecting_graph(action=identity, timeout=10):
    return DecisionGraph((
        NodeBinding('a', identity, lambda root, _: {'value': root}),
        NodeBinding('b', identity, lambda _, r: {'value': r['a->b']}),
    ), (Edge('a', 'b', collect=EdgeAction(action, lambda _, r: {'value': r['a'] + 1}),
             timeout_seconds=timeout),), 'a', 'b')


def test_edge_timeout_leaves_successful_source():
    async def wait(value):
        await asyncio.Event().wait()
    run = asyncio.run(GraphRunner().run(GraphCompiler().compile(collecting_graph(wait, 0.01)), 1))
    assert run.executions['a'].status == 'succeeded'
    assert run.executions['a->b'].error_type == 'TimeoutError'
    assert run.executions['b'].status == 'blocked'


def test_cancel_collect_propagates():
    async def exercise():
        entered = asyncio.Event()
        async def wait(value):
            entered.set()
            await asyncio.Event().wait()
        task = asyncio.create_task(GraphRunner().run(GraphCompiler().compile(collecting_graph(wait)), 1))
        await entered.wait()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            return
        raise AssertionError('Cancellation was swallowed')
    asyncio.run(exercise())


def test_repeat_collects_share_budget_and_fresh_round_state():
    body = collecting_graph()
    graph = DecisionGraph((NodeBinding('loop', RepeatBlock(body, lambda _, out: out,
        lambda out: out == 2, 3), lambda root, _: {'value': root}),), (), 'loop', 'loop')
    compiled = GraphCompiler().compile(graph)
    run = asyncio.run(GraphRunner().run(compiled, 0, max_steps=7))
    assert run.status == 'completed' and run.output == 2
    assert run.executions['loop/0/a->b'].output == 1
    assert run.executions['loop/1/a->b'].output == 2
    assert asyncio.run(GraphRunner().run(compiled, 0, max_steps=6)).status == 'failed'


def test_concurrent_runs_do_not_share_outputs():
    async def exercise():
        compiled = GraphCompiler().compile(collecting_graph())
        first, second = await asyncio.gather(GraphRunner().run(compiled, 1), GraphRunner().run(compiled, 8))
        assert (first.output, second.output) == (2, 9)
        assert first.run_id != second.run_id
        assert first.executions is not second.executions
    asyncio.run(exercise())
